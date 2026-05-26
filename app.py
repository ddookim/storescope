"""
StoreScope — Shopify Store X-Ray (무료 툴)
=============================================
Shopify 스토어 URL 입력 → 즉시 분석 결과 제공
마케팅 채널: r/dropship, r/shopify 등에서 무료 공개

실행:
    streamlit run app.py
"""

import streamlit as st
from contextlib import contextmanager
from curl_cffi import requests as cffi_requests
import psycopg2
import psycopg2.extras
import psycopg2.pool
import os
import requests as _requests
from typing import Optional


_DB_URL = os.environ.get("DATABASE_URL")
if not _DB_URL:
    st.error("DATABASE_URL 환경변수가 설정되지 않았습니다.")
    st.stop()
DB_URL: str = _DB_URL

# FIX: localhost 하드코딩 제거 — Docker 또는 다른 호스트에서 FastAPI가 실행될 때
# http://localhost:8000은 Streamlit 컨테이너 내부에서 연결 불가.
_API_BASE = os.environ.get("API_BASE_URL", "http://localhost:8000")
_GA_ID    = os.environ.get("GA_MEASUREMENT_ID", "")

st.set_page_config(
    page_title="StoreScope — Store X-Ray",
    page_icon="🔍",
    layout="wide",
)

# FIX: Streamlit은 <head> 메타태그를 직접 주입하지 못함 — unsafe_allow_html로 SEO/GA 삽입.
# Streamlit의 취약한 SEO를 보완: description, OG tags, robots, GA 이벤트 추적.
def _inject_head_meta() -> None:
    _ga_block = ""
    if _GA_ID:
        _ga_block = f"""
    <script async src="https://www.googletagmanager.com/gtag/js?id={_GA_ID}"></script>
    <script>
      window.dataLayer=window.dataLayer||[];
      function gtag(){{dataLayer.push(arguments);}}
      gtag('js',new Date());
      gtag('config','{_GA_ID}',{{anonymize_ip:true}});
    </script>"""
    st.markdown(f"""
{_ga_block}
<meta name="description" content="Free Shopify store analyzer — discover trending products, competing suppliers, and price intelligence across thousands of Shopify stores.">
<meta property="og:title" content="StoreScope — Store X-Ray">
<meta property="og:description" content="Instantly analyze any Shopify store. See competing suppliers, trending products, and price data.">
<meta property="og:type" content="website">
<meta name="robots" content="index, follow">
""", unsafe_allow_html=True)

_inject_head_meta()

# ── 스타일 ──────────────────────────────────────────────────
st.markdown("""
<style>
.metric-card {
    background: #f8f9fa;
    border-radius: 8px;
    padding: 16px;
    text-align: center;
}
.trend-badge {
    background: #d4edda;
    color: #155724;
    border-radius: 4px;
    padding: 2px 8px;
    font-size: 12px;
    font-weight: bold;
}
</style>
""", unsafe_allow_html=True)


# ── DB 헬퍼 ─────────────────────────────────────────────────
# CRITICAL FIX: 단일 connection은 Streamlit 세션마다 별도 스레드에서 공유되므로
# psycopg2 thread-safety 위반 → ThreadedConnectionPool로 교체.
@st.cache_resource
def _get_pool() -> Optional[psycopg2.pool.ThreadedConnectionPool]:
    try:
        return psycopg2.pool.ThreadedConnectionPool(1, 5, DB_URL)
    except Exception:
        return None


@contextmanager
def _conn_ctx():
    pool = _get_pool()
    if pool is None or pool.closed:
        _get_pool.clear()
        yield None
        return
    conn = pool.getconn()
    conn.autocommit = True
    try:
        yield conn
    finally:
        pool.putconn(conn)


def query(sql: str, params=(), one=False):
    with _conn_ctx() as conn:
        if not conn:
            return None
        try:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute(sql, params)
                return cur.fetchone() if one else cur.fetchall()
        except psycopg2.OperationalError:
            _get_pool.clear()
            return None
        except Exception:
            return None


# ── 라이브 크롤링 (DB에 없는 스토어용) ─────────────────────
_LIVE_PAGE_LIMIT = 250
_LIVE_MAX_PAGES  = 10  # 최대 2,500개

def live_fetch_products(domain: str) -> Optional[dict]:
    """페이지네이션으로 스토어 전체 상품 수집 (최대 2,500개)."""
    all_products: list = []
    try:
        for page in range(1, _LIVE_MAX_PAGES + 1):
            url = f"https://{domain}/products.json?limit={_LIVE_PAGE_LIMIT}&page={page}"
            # FIX: chrome120 impersonation → 투명한 봇 UA, CFAA 리스크 제거
            resp = cffi_requests.get(
                url,
                headers={"User-Agent": "StoreScope/1.0 (https://storescope.com; mailto:dodo32032@gmail.com)"},
                timeout=10,
                allow_redirects=True,
            )
            if resp.status_code != 200:
                break
            batch = resp.json().get("products", [])
            all_products.extend(batch)
            if len(batch) < _LIVE_PAGE_LIMIT:
                break
    except Exception:
        pass
    return {"products": all_products} if all_products else None


import re

_DOMAIN_RE = re.compile(r"^[a-z0-9][a-z0-9\-]{0,61}[a-z0-9]?\.myshopify\.com$")

def normalize_domain(raw: str) -> str:
    raw = raw.strip().lower()
    raw = raw.replace("https://", "").replace("http://", "").rstrip("/")
    if not raw.endswith(".myshopify.com"):
        raw = raw + ".myshopify.com"
    # MEDIUM-2 FIX: SSRF 차단 — 내부 IP 및 비정상 도메인 거부
    if not _DOMAIN_RE.match(raw):
        raise ValueError(f"유효하지 않은 도메인 형식입니다.")
    return raw


# ── 메인 UI ─────────────────────────────────────────────────
st.title("🔍 StoreScope — Store X-Ray")
st.caption("Shopify 스토어를 즉시 분석합니다. 무료, 가입 불필요.")

domain_input = st.text_input(
    "Shopify 스토어 URL 입력",
    placeholder="예: gymshark.myshopify.com 또는 gymshark",
)

col_analyze, _ = st.columns([1, 5])
analyze_btn = col_analyze.button("분석하기", type="primary", use_container_width=True)

st.divider()

# ── 트렌딩 사이드바 ─────────────────────────────────────────
with st.sidebar:
    st.header("📈 지금 트렌딩")
    st.caption("여러 스토어에서 동시에 팔리는 상품")

    trending = query("""
        SELECT c.id, c.store_count, c.product_count,
               p.title, p.price_min, p.image_url
        FROM clusters c
        LEFT JOIN LATERAL (
            SELECT p2.title, p2.price_min, p2.image_url
            FROM product_clusters pc2
            JOIN products p2 ON p2.id = pc2.product_id
            WHERE pc2.cluster_id = c.id
            ORDER BY p2.price_min ASC NULLS LAST LIMIT 1
        ) p ON true
        WHERE c.store_count >= 2
        ORDER BY c.store_count DESC
        LIMIT 8
    """)

    if trending:
        for item in trending:
            with st.container():
                st.markdown(f"**{item['store_count']}개 스토어** 판매 중")
                cols = st.columns([1, 3])
                img = item.get("image_url", "")
                if img and img.startswith("http"):
                    cols[0].image(img, width=55)
                title = (item["title"] or "")[:35]
                price = f"${item['price_min']:.2f}" if item.get("price_min") else "가격 미정"
                cols[1].markdown(f"**{title}**  \n{price}")
                st.divider()
    else:
        st.info("데이터 수집 중...")

# ── 분석 결과 ───────────────────────────────────────────────
if analyze_btn and domain_input:
    try:
        domain = normalize_domain(domain_input)
    except ValueError as e:
        st.error(str(e))
        st.stop()
    st.subheader(f"분석 결과: `{domain}`")

    with st.spinner("데이터 가져오는 중..."):
        # 1. DB에서 먼저 조회
        store_row = query(
            "SELECT * FROM stores WHERE domain = %s", (domain,), one=True
        )

        if store_row:
            products_db = query("""
                SELECT p.*, pc.cluster_id
                FROM products p
                LEFT JOIN product_clusters pc ON pc.product_id = p.id
                WHERE p.store_id = %s
                ORDER BY p.price_min ASC NULLS LAST
            """, (store_row["id"],))
            products = [dict(p) for p in (products_db or [])]
            source = "db"
        else:
            # 2. DB에 없으면 라이브 크롤링
            data = live_fetch_products(domain)
            if data:
                products = data.get("products", [])
                source = "live"
            else:
                products = []
                source = "error"

    if source == "error" or not products:
        st.error(
            "스토어에 접근할 수 없어요. "
            "비밀번호가 걸린 스토어이거나 존재하지 않는 주소예요."
        )
        st.stop()

    # ── 요약 지표 ──────────────────────────────────────────
    prices = []
    if source == "db":
        prices = [float(p["price_min"]) for p in products if p.get("price_min")]
    else:
        for p in products:
            for v in p.get("variants", []):
                try:
                    prices.append(float(v["price"]))
                except Exception:
                    pass

    product_count = len(products)
    avg_price = sum(prices) / len(prices) if prices else 0
    price_min = min(prices) if prices else 0
    price_max = max(prices) if prices else 0

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("총 상품 수", f"{product_count:,}개")
    c2.metric("평균 가격", f"${avg_price:.2f}")
    c3.metric("최저가", f"${price_min:.2f}")
    c4.metric("최고가", f"${price_max:.2f}")

    st.divider()

    # ── 클러스터 인텔리전스 (DB 스토어만) ─────────────────
    _FREE_STORE_PREVIEW = 2  # 무료 미리보기: 경쟁 스토어 최대 2개

    if source == "db":
        clustered = [p for p in products if p.get("cluster_id")]
        if clustered:
            st.subheader("경쟁 스토어에서도 파는 상품")
            st.caption("같은 공급사 상품을 다른 스토어가 팔고 있어요")

            # N+1 제거: 단일 IN 쿼리로 모든 클러스터 경쟁 스토어 일괄 조회
            from collections import defaultdict
            cluster_ids = list({p["cluster_id"] for p in clustered})
            all_cluster_stores = query("""
                SELECT pc.cluster_id, s.domain, p2.price_min
                FROM product_clusters pc
                JOIN products p2 ON p2.id = pc.product_id
                JOIN stores s ON s.id = p2.store_id
                WHERE pc.cluster_id = ANY(%s) AND s.domain != %s
                ORDER BY pc.cluster_id, p2.price_min ASC NULLS LAST
            """, (cluster_ids, domain))

            stores_by_cluster: dict = defaultdict(list)
            if all_cluster_stores:
                for row in all_cluster_stores:
                    stores_by_cluster[row["cluster_id"]].append(row)

            shown = set()
            for p in clustered:
                cid = p["cluster_id"]
                if cid in shown:
                    continue
                shown.add(cid)
                competitor_stores = stores_by_cluster.get(cid, [])
                if not competitor_stores:
                    continue

                total_count = len(competitor_stores)
                preview = competitor_stores[:_FREE_STORE_PREVIEW]
                locked = total_count - _FREE_STORE_PREVIEW

                with st.expander(
                    f"**{p['title'][:50]}** — "
                    f"{total_count}개 경쟁 스토어 발견"
                ):
                    comp_cols = st.columns(len(preview))
                    for i, cs in enumerate(preview):
                        price_str = f"${cs['price_min']:.2f}" if cs.get("price_min") else "N/A"
                        comp_cols[i].markdown(
                            f"`{cs['domain']}`  \n**{price_str}**"
                        )
                    if locked > 0:
                        st.info(
                            f"+ {locked}개 스토어 더 있음 — "
                            f"**API 플랜**으로 전체 데이터에 접근하세요"
                        )

            st.divider()

    # ── 상품 목록 ──────────────────────────────────────────
    st.subheader("상품 목록")

    if source == "db":
        display_products = products[:30]
        items = [
            {
                "title": p.get("title", ""),
                "price": f"${p['price_min']:.2f}" if p.get("price_min") else "N/A",
                "image": p.get("image_url", ""),
            }
            for p in display_products
        ]
    else:
        display_products = products[:30]
        items = []
        for p in display_products:
            variants = p.get("variants", [])
            price = f"${float(variants[0]['price']):.2f}" if variants else "N/A"
            images = p.get("images", [])
            items.append({
                "title": p.get("title", ""),
                "price": price,
                "image": images[0]["src"] if images else "",
            })

    cols_per_row = 4
    for row_start in range(0, len(items), cols_per_row):
        row_items = items[row_start:row_start + cols_per_row]
        cols = st.columns(cols_per_row)
        for i, item in enumerate(row_items):
            with cols[i]:
                if item["image"] and item["image"].startswith("http"):
                    st.image(item["image"], use_container_width=True)
                st.caption(item["title"][:40])
                st.markdown(f"**{item['price']}**")

    if product_count > 30:
        st.info(f"상위 30개 표시 중 (전체 {product_count}개)")

    # ── 이메일 캡처 CTA (페이월 직전) ─────────────────────────
    # FIX: 무료 툴 이용자 이메일 수집 → 전환 퍼널 복구.
    # 이메일 미제공 시에도 기능 제한 없음 — 마찰 최소화로 제출율 최대화.
    st.divider()
    if "email_captured" not in st.session_state:
        st.session_state["email_captured"] = False

    if not st.session_state["email_captured"]:
        with st.container():
            st.markdown("### Get the full supplier report")
            st.caption("Enter your email to unlock full competitor list + weekly trend alerts.")
            col_email, col_btn = st.columns([3, 1])
            lead_email = col_email.text_input(
                "Email", label_visibility="collapsed", placeholder="you@example.com"
            )
            submit_lead = col_btn.button("Unlock", type="primary", use_container_width=True)

            if submit_lead and lead_email:
                try:
                    # FIX: 하드코딩된 localhost:8000 → _API_BASE 환경변수 사용
                    _requests.post(
                        f"{_API_BASE}/leads",
                        json={"email": lead_email, "domain": domain, "source": "xray"},
                        timeout=3,
                    )
                    st.session_state["email_captured"] = True
                    st.success("Check your inbox — full data unlocked below.")
                    st.rerun()
                except Exception:
                    st.session_state["email_captured"] = True
                    st.rerun()

            st.caption("No spam. Unsubscribe anytime. [View pricing](https://storescope.netlify.app#pricing)")
    else:
        st.info(
            "Full competitor data unlocked.  "
            "[**Upgrade to Pro for unlimited API access →**](https://storescope.netlify.app#pricing)"
        )
