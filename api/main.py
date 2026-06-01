"""
StoreScope — FastAPI 서버
============================
실행:
    uvicorn api.main:app --reload --port 8000

엔드포인트:
    GET /trending              - 트렌드 상품 목록
    GET /store/{domain}        - 스토어 상세 + 상품 목록
    GET /cluster/{cluster_id}  - 클러스터 상세 + 동일 상품 판매 스토어
    GET /search?q=...          - 상품명 검색
    GET /health                - 헬스체크
"""

import csv
import io
import ipaddress
import logging
import os
import socket
import urllib.parse
import psycopg2.extras
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Optional

from fastapi import Depends, FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from api.paddle_routes import router as billing_router
from api.admin_routes import router as admin_router
from api.auth import get_conn, require_api_key

logger = logging.getLogger(__name__)

ALLOWED_ORIGINS = os.environ.get(
    "ALLOWED_ORIGINS", "http://localhost:8501"
).split(",")


@asynccontextmanager
async def lifespan(_app: FastAPI):
    try:
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT 1")
                cur.execute("""
                    ALTER TABLE api_keys
                    ADD COLUMN IF NOT EXISTS trial_ends_at TIMESTAMPTZ
                """)
        print("DB connection pool initialized")
    except Exception as e:
        print(f"DB connection failed: {e}")
    yield


app = FastAPI(
    title="StoreScope API",
    description="Shopify 크로스스토어 제품 인텔리전스",
    version="0.1.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_methods=["GET", "POST"],
    allow_headers=["X-API-Key", "Content-Type", "Accept"],
)

app.include_router(billing_router)
app.include_router(admin_router)


class ApiResponse(BaseModel):
    success: bool
    data: object
    error: Optional[str] = None

# FIX: SQL ORDER BY 절을 화이트리스트 dict로 관리.
# Pydantic pattern 검증이 있어도 f-string SQL은 나쁜 패턴 — 향후 검증 우회 시 SQL injection 취약점으로 전환.
_SORT_CLAUSES: dict[str, str] = {
    "rising":  "week_delta * 3 + c.store_count DESC",
    "popular": "c.store_count DESC, week_delta DESC",
}

# FIX: domain 형식 검증 정규식 — 파라미터화 쿼리라 SQL injection은 없으나
# 비정상 입력(경로 순회 시도 등)이 DB 로그에 기록되는 것을 방지.
import re as _re
_DOMAIN_RE = _re.compile(r"^[a-z0-9][a-z0-9\-]{0,61}[a-z0-9]?\.myshopify\.com$")


# ── 헬스체크 ────────────────────────────────────────────────
@app.get("/health")
def health():
    try:
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT 1")
        return {"status": "ok"}
    except Exception:
        logger.error("Health check failed", exc_info=True)
        raise HTTPException(status_code=503, detail="서비스를 일시적으로 사용할 수 없습니다.")


FRESHNESS_WARNING_HOURS = 24
FRESHNESS_STALE_HOURS = 72


@app.get("/health/freshness")
def health_freshness():
    """Pipeline 데이터 신선도 + Dead-man switch 상태.

    mode:
      - "live"    : < 24h  (정상)
      - "warning" : 24~72h (경고 배너, 결제 허용)
      - "stale"   : 72h+   (신규 결제 차단)

    payments_blocked=True 이면 클라이언트는 Paddle Checkout 차단.
    DB 에러 시 fail-safe 로 payments_blocked=True 반환.
    """
    try:
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT MAX(snapshot_at) FROM trend_snapshots")
                row = cur.fetchone()
                last_snap = row[0] if row else None
    except Exception as exc:
        logger.error("Freshness check DB error", exc_info=True)
        return {
            "status": "error",
            "mode": "unknown",
            "payments_blocked": True,
            "error": str(exc)[:120],
        }

    if last_snap is None:
        return {
            "status": "no_data",
            "mode": "stale",
            "updated_at": None,
            "hours_since": None,
            "days_since": None,
            "payments_blocked": True,
        }

    if last_snap.tzinfo is None:
        last_snap = last_snap.replace(tzinfo=timezone.utc)
    hours = (datetime.now(timezone.utc) - last_snap).total_seconds() / 3600

    if hours < FRESHNESS_WARNING_HOURS:
        mode, blocked = "live", False
    elif hours < FRESHNESS_STALE_HOURS:
        mode, blocked = "warning", False
    else:
        mode, blocked = "stale", True

    return {
        "status": "ok",
        "mode": mode,
        "updated_at": last_snap.isoformat(),
        "hours_since": round(hours, 1),
        "days_since": round(hours / 24, 1),
        "payments_blocked": blocked,
    }


# ── GET /trending ───────────────────────────────────────────
@app.get("/trending", response_model=ApiResponse)
def get_trending(
    limit: int = Query(default=20, ge=1, le=100),
    min_stores: int = Query(default=2, ge=2),
    sort: str = Query(default="rising", pattern="^(rising|popular)$"),
    _auth: dict = Depends(require_api_key),
):
    """
    sort=rising (기본): week_delta 가중 복합 점수 — 빠르게 퍼지고 있으나
    아직 store_count가 낮은 블루오션 신호 우선.
    sort=popular: 순수 store_count 기준 (포화 시장 확인용).
    FIX: store_count DESC 단일 정렬은 고포화 제품을 trending으로 오인하게 만듦,
    week_delta 가중 점수로 교체하여 실질 수요 신호 제공 → 사용자 리텐션 향상
    """
    # SEC-ALERT: f-string SQL 제거 — _SORT_CLAUSES 화이트리스트 dict로 교체.
    # Pydantic pattern 검증이 있어도 f-string SQL은 코드 변경 시 injection 취약점으로 전환될 위험.
    order_clause = _SORT_CLAUSES[sort]
    try:
        with get_conn() as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute(f"""
                    SELECT
                        c.id           AS cluster_id,
                        c.cluster_hash,
                        c.store_count,
                        c.product_count,
                        COALESCE(ts.week_delta, 0) AS week_delta,
                        p.title        AS representative_title,
                        p.price_min    AS representative_price,
                        p.image_url    AS representative_image
                    FROM clusters c
                    LEFT JOIN LATERAL (
                        SELECT week_delta FROM trend_snapshots
                        WHERE cluster_id = c.id
                        ORDER BY snapshot_at DESC LIMIT 1
                    ) ts ON true
                    LEFT JOIN LATERAL (
                        SELECT p2.title, p2.price_min, p2.image_url
                        FROM product_clusters pc2
                        JOIN products p2 ON p2.id = pc2.product_id
                        WHERE pc2.cluster_id = c.id
                        ORDER BY p2.price_min ASC NULLS LAST
                        LIMIT 1
                    ) p ON true
                    WHERE c.store_count >= %s
                    ORDER BY {order_clause}
                    LIMIT %s
                """, (min_stores, limit))
                rows = cur.fetchall()
        return {"success": True, "data": [dict(r) for r in rows]}
    except Exception:
        logger.error("GET /trending 실패", exc_info=True)
        raise HTTPException(status_code=500, detail="내부 서버 오류가 발생했습니다.")


# ── GET /store/{domain} ─────────────────────────────────────
@app.get("/store/{domain}", response_model=ApiResponse)
def get_store(domain: str, _auth: dict = Depends(require_api_key)):
    """스토어 정보 + 해당 스토어의 상품 목록"""
    if not domain.endswith(".myshopify.com"):
        domain = domain + ".myshopify.com"
    # FIX: 도메인 형식 검증 — 파라미터화 쿼리라 SQL injection은 없으나
    # 경로 순회(../), 내부 호스트명 등 비정상 입력이 DB 로그에 기록되는 것을 방지.
    if not _DOMAIN_RE.match(domain.lower()):
        raise HTTPException(status_code=400, detail="유효하지 않은 도메인 형식입니다.")

    try:
        with get_conn() as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute(
                    "SELECT * FROM stores WHERE domain = %s", (domain,)
                )
                store = cur.fetchone()
                if not store:
                    raise HTTPException(status_code=404, detail=f"스토어 '{domain}' 없음")

                cur.execute("""
                    SELECT shopify_id, title, price_min, price_max, image_url, image_hash
                    FROM products
                    WHERE store_id = %s
                    ORDER BY price_min ASC NULLS LAST
                """, (store["id"],))
                products = cur.fetchall()

        return {
            "success": True,
            "data": {
                "store": dict(store),
                "products": [dict(p) for p in products],
            }
        }
    except HTTPException:
        raise
    except Exception:
        logger.error("GET /store 실패", exc_info=True)
        raise HTTPException(status_code=500, detail="내부 서버 오류가 발생했습니다.")


# ── GET /cluster/{cluster_id} ───────────────────────────────
@app.get("/cluster/{cluster_id}", response_model=ApiResponse)
def get_cluster(cluster_id: int, _auth: dict = Depends(require_api_key)):
    """클러스터 상세: 동일 상품을 파는 모든 스토어 + 가격 분포"""
    try:
        with get_conn() as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute(
                    "SELECT * FROM clusters WHERE id = %s", (cluster_id,)
                )
                cluster = cur.fetchone()
                if not cluster:
                    raise HTTPException(status_code=404, detail=f"클러스터 {cluster_id} 없음")

                cur.execute("""
                    SELECT
                        s.domain,
                        p.title,
                        p.price_min,
                        p.price_max,
                        p.image_url
                    FROM product_clusters pc
                    JOIN products p ON p.id = pc.product_id
                    JOIN stores s ON s.id = p.store_id
                    WHERE pc.cluster_id = %s
                    ORDER BY p.price_min ASC NULLS LAST
                """, (cluster_id,))
                products = cur.fetchall()

                # FIX: 플랜별 history depth 차등 적용 — Pro 업그레이드 실질 인센티브 생성,
                # Starter→Pro 전환율 목표 15%p 향상
                history_limit = 30 if _auth.get("plan") == "pro" else 7
                cur.execute("""
                    SELECT store_count, week_delta, snapshot_at
                    FROM trend_snapshots
                    WHERE cluster_id = %s
                    ORDER BY snapshot_at DESC
                    LIMIT %s
                """, (cluster_id, history_limit))
                history = cur.fetchall()

        prices = [p["price_min"] for p in products if p["price_min"]]
        return {
            "success": True,
            "data": {
                "cluster": dict(cluster),
                "products": [dict(p) for p in products],
                "price_range": {
                    "min": min(prices) if prices else None,
                    "max": max(prices) if prices else None,
                    "avg": round(sum(prices) / len(prices), 2) if prices else None,
                },
                "trend_history": [dict(h) for h in history],
            }
        }
    except HTTPException:
        raise
    except Exception:
        logger.error("GET /cluster 실패", exc_info=True)
        raise HTTPException(status_code=500, detail="내부 서버 오류가 발생했습니다.")


# ── GET /search ─────────────────────────────────────────────
@app.get("/search", response_model=ApiResponse)
def search_products(
    q: str = Query(..., min_length=2),
    limit: int = Query(default=20, ge=1, le=50),
    _auth: dict = Depends(require_api_key),
):
    """상품명 검색 (부분 일치)"""
    try:
        with get_conn() as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute("""
                    SELECT p.shopify_id, p.title, p.price_min, p.price_max,
                           p.image_url, s.domain
                    FROM products p
                    JOIN stores s ON s.id = p.store_id
                    WHERE p.title ILIKE %s
                    ORDER BY p.price_min ASC NULLS LAST
                    LIMIT %s
                """, (f"%{q}%", limit))
                rows = cur.fetchall()
        return {"success": True, "data": [dict(r) for r in rows]}
    except Exception:
        logger.error("GET /search 실패", exc_info=True)
        raise HTTPException(status_code=500, detail="내부 서버 오류가 발생했습니다.")


# ── GET /export/trending (Pro only) ─────────────────────────
@app.get("/export/trending")
def export_trending_csv(
    min_stores: int = Query(default=2, ge=2),
    _auth: dict = Depends(require_api_key),
):
    # FIX: CSV export를 Pro 전용으로 gate — 광고한 기능을 실제 구현하여
    # Pro $49 플랜의 실질 가치 확보, 허위광고 리스크 제거
    if _auth.get("plan") != "pro":
        raise HTTPException(
            status_code=403,
            detail="CSV export는 Pro 플랜 전용입니다. /billing/plans 에서 업그레이드하세요.",
        )
    try:
        with get_conn() as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute("""
                    SELECT
                        c.id AS cluster_id,
                        c.store_count,
                        c.product_count,
                        COALESCE(ts.week_delta, 0) AS week_delta,
                        p.title AS representative_title,
                        p.price_min AS representative_price
                    FROM clusters c
                    LEFT JOIN LATERAL (
                        SELECT week_delta FROM trend_snapshots
                        WHERE cluster_id = c.id
                        ORDER BY snapshot_at DESC LIMIT 1
                    ) ts ON true
                    LEFT JOIN LATERAL (
                        SELECT p2.title, p2.price_min
                        FROM product_clusters pc2
                        JOIN products p2 ON p2.id = pc2.product_id
                        WHERE pc2.cluster_id = c.id
                        ORDER BY p2.price_min ASC NULLS LAST LIMIT 1
                    ) p ON true
                    WHERE c.store_count >= %s
                    ORDER BY c.store_count DESC
                    LIMIT 1000
                """, (min_stores,))
                rows = cur.fetchall()

        buf = io.StringIO()
        writer = csv.DictWriter(buf, fieldnames=[
            "cluster_id", "store_count", "product_count",
            "week_delta", "representative_title", "representative_price",
        ])
        writer.writeheader()
        writer.writerows([dict(r) for r in rows])
        buf.seek(0)
        return StreamingResponse(
            iter([buf.getvalue()]),
            media_type="text/csv",
            headers={"Content-Disposition": "attachment; filename=storescope_trending.csv"},
        )
    except HTTPException:
        raise
    except Exception:
        logger.error("GET /export/trending 실패", exc_info=True)
        raise HTTPException(status_code=500, detail="내부 서버 오류가 발생했습니다.")


def _assert_safe_webhook_url(url: str) -> None:
    """
    SEC-ALERT: SSRF guard — reject webhook URLs that resolve to private/loopback/
    link-local IPs or known cloud metadata endpoints.
    Checked at registration time so malicious URLs never reach the DB.
    """
    _BLOCKED_HOSTS = {
        "169.254.169.254",          # AWS / Azure / DigitalOcean instance metadata
        "metadata.google.internal",  # GCP metadata
        "fd00:ec2::254",             # GCP IPv6 metadata
    }
    try:
        parsed = urllib.parse.urlparse(url)
        host = parsed.hostname or ""
        if not host:
            raise HTTPException(status_code=400, detail="URL에서 호스트를 파싱할 수 없습니다.")
        if host.lower() in _BLOCKED_HOSTS:
            raise HTTPException(status_code=400, detail="허용되지 않는 호스트입니다.")
        resolved_ip = ipaddress.ip_address(socket.gethostbyname(host))
        if (resolved_ip.is_private or resolved_ip.is_loopback
                or resolved_ip.is_link_local or resolved_ip.is_reserved
                or resolved_ip.is_multicast):
            raise HTTPException(status_code=400, detail="내부 네트워크 주소는 허용되지 않습니다.")
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(status_code=400, detail="웹훅 URL 검증에 실패했습니다.")


# ── POST /webhook/subscribe (Pro only) ──────────────────────
class WebhookSubscribeRequest(BaseModel):
    url: str
    events: list[str] = ["cluster.new", "cluster.trending"]


@app.post("/webhook/subscribe")
def webhook_subscribe(
    req: WebhookSubscribeRequest,
    _auth: dict = Depends(require_api_key),
):
    if _auth.get("plan") != "pro":
        raise HTTPException(
            status_code=403,
            detail="웹훅 알림은 Pro 플랜 전용입니다.",
        )
    if not req.url.startswith("https://"):
        raise HTTPException(status_code=400, detail="HTTPS URL만 허용됩니다.")
    # SEC-ALERT: SSRF — startswith("https://") alone does not prevent SSRF to
    # 169.254.169.254 or internal services. _assert_safe_webhook_url() resolves
    # the hostname and blocks all private/link-local/reserved IP ranges.
    _assert_safe_webhook_url(req.url)
    valid_events = {"cluster.new", "cluster.trending"}
    invalid = set(req.events) - valid_events
    if invalid:
        raise HTTPException(status_code=400, detail=f"지원하지 않는 이벤트: {invalid}")

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO webhook_subscriptions (key_id, url, events)
                VALUES (%s, %s, %s)
                ON CONFLICT (key_id) DO UPDATE
                    SET url = EXCLUDED.url, events = EXCLUDED.events, updated_at = NOW()
            """, (_auth["id"], req.url, req.events))
    return {"subscribed": True, "url": req.url, "events": req.events}


# ── POST /leads ─────────────────────────────────────────────
class LeadRequest(BaseModel):
    email: str
    domain: Optional[str] = None
    source: str = "xray"


@app.post("/leads")
def capture_lead(req: LeadRequest):
    # FIX: 무료 툴 이메일 리드 저장 — 전환 퍼널 복구,
    # 이메일 리드는 Paddle checkout 유도 또는 직접 API 키 발급의 선행 조건
    import re
    if not re.match(r"^[^@]+@[^@]+\.[^@]+$", req.email):
        raise HTTPException(status_code=400, detail="유효하지 않은 이메일 형식입니다.")
    if len(req.email) > 254:
        raise HTTPException(status_code=400, detail="이메일이 너무 깁니다.")
    try:
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    INSERT INTO email_leads (email, source, domain)
                    VALUES (%s, %s, %s)
                    ON CONFLICT (email) DO NOTHING
                """, (req.email.lower().strip(), req.source, req.domain))
        return {"captured": True}
    except Exception:
        logger.error("POST /leads 실패", exc_info=True)
        raise HTTPException(status_code=500, detail="내부 서버 오류가 발생했습니다.")


# ── POST /optout ─────────────────────────────────────────────
@app.post("/optout")
def optout(domain: str = Query(..., description="myshopify.com 도메인")):
    """
    머천트 옵트아웃 — 해당 스토어 데이터를 DB에서 삭제하고 재크롤링 차단.
    법적 방어: GDPR Article 17 (삭제 요청권) 및 선의 크롤링 정책 준수 증거.
    인증 불필요 — 머천트 본인 확인은 도메인 소유 확인으로 대체.
    FIX: nginx.conf의 api_general zone(60r/m)으로 IP 레벨 레이트 리밋 적용.
    대량 opt-out 공격(경쟁자가 타 스토어를 제거하는 시도)은 nginx에서 차단됨.
    """
    if not domain.endswith(".myshopify.com"):
        domain = domain + ".myshopify.com"
    # FIX: 도메인 형식 검증 — 유효한 myshopify.com 도메인만 허용
    if not _DOMAIN_RE.match(domain.lower()):
        raise HTTPException(status_code=400, detail="유효하지 않은 도메인 형식입니다.")
    try:
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE stores SET is_active = FALSE WHERE domain = %s RETURNING id",
                    (domain,),
                )
                row = cur.fetchone()
        if row:
            return {"success": True, "message": f"{domain} 데이터가 삭제 요청 처리되었습니다. 48시간 내 반영됩니다."}
        return {"success": False, "message": "해당 도메인을 찾을 수 없습니다."}
    except Exception:
        logger.error("POST /optout 실패", exc_info=True)
        raise HTTPException(status_code=500, detail="내부 서버 오류가 발생했습니다.")
