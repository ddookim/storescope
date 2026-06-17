"""
X-Ray Report Email — /leads 접속 후 자동 발송

사용자 약속 (랜딩 모달): "We'll email you the full product list, the trending items
they're stocking, and the price range across stores"

본 모듈이 그 약속을 이행. 미존재 시 랜딩 = 미끼만 던지고 약속 미이행 = 신뢰 폭락.

생성 흐름:
    /leads POST → email 저장 → BackgroundTask로 send_xray_report() 호출 →
    SMTP 또는 console fallback (개발) → 사용자 수신.

Path A KPI 정합:
    이메일 캡처 → 24h 내 가치 전달 → trial 전환율 ↑ (industry: 18-30%)
    가치 미전달 → trial 전환율 < 3%
"""

import html
import logging
import os
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import Optional

_log = logging.getLogger(__name__)


SMTP_HOST  = os.environ.get("SMTP_HOST", "")
SMTP_PORT  = int(os.environ.get("SMTP_PORT", "587"))
SMTP_USER  = os.environ.get("SMTP_USER", "")
SMTP_PASS  = os.environ.get("SMTP_PASS", "")
FROM_EMAIL = os.environ.get("SMTP_FROM", "noreply@storescope.com")
APP_URL    = os.environ.get("APP_URL", "https://storescope.com")


def _fetch_store_data(domain: str) -> dict:
    """DB에서 도메인 데이터 조회 — 미존재 시 빈 dict 반환 (live fetch는 Streamlit에서)."""
    if not domain:
        return {}
    try:
        from api.auth import get_conn
    except Exception:
        return {}

    norm = domain.lower().strip().replace("https://", "").replace("http://", "").rstrip("/")
    if not norm.endswith(".myshopify.com"):
        norm = norm + ".myshopify.com"

    try:
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT id, domain, product_count FROM stores WHERE domain = %s",
                    (norm,)
                )
                row = cur.fetchone()
                if not row:
                    return {"domain": norm, "in_db": False}
                store_id, dom, pcount = row

                # 상위 가격대 product 5개
                cur.execute("""
                    SELECT title, price_min, price_max, image_url
                    FROM products
                    WHERE store_id = %s AND price_min > 0
                    ORDER BY price_min DESC
                    LIMIT 5
                """, (store_id,))
                top_priced = [
                    {"title": r[0], "price_min": float(r[1]), "price_max": float(r[2] or r[1]), "image": r[3]}
                    for r in cur.fetchall()
                ]

                # 트렌딩 (cluster 매칭 + store_count ≥ 5)
                cur.execute("""
                    SELECT p.title, p.price_min, p.image_url, c.store_count
                    FROM products p
                    JOIN product_clusters pc ON pc.product_id = p.id
                    JOIN clusters c ON c.id = pc.cluster_id
                    WHERE p.store_id = %s AND c.store_count >= 5
                    ORDER BY c.store_count DESC
                    LIMIT 5
                """, (store_id,))
                trending = [
                    {"title": r[0], "price_min": float(r[1] or 0), "image": r[2], "cluster_size": r[3]}
                    for r in cur.fetchall()
                ]

                # 가격 분포
                cur.execute("""
                    SELECT
                        MIN(price_min) AS lo,
                        AVG(price_min) AS avg,
                        MAX(price_max) AS hi,
                        COUNT(*) AS n
                    FROM products
                    WHERE store_id = %s AND price_min > 0
                """, (store_id,))
                lo, avg, hi, n = cur.fetchone()

        return {
            "domain": norm,
            "in_db": True,
            "product_count": pcount,
            "price_lo": float(lo) if lo is not None else 0,
            "price_avg": float(avg) if avg is not None else 0,
            "price_hi": float(hi) if hi is not None else 0,
            "priced_n": n,
            "top_priced": top_priced,
            "trending": trending,
        }
    except Exception as exc:
        _log.exception("xray_report DB lookup failed: %s", exc)
        return {"domain": norm, "in_db": False, "error": str(exc)[:120]}


def _render_html(email: str, data: dict) -> str:
    # SEC: domain은 /leads 통해 user input → html.escape 필수.
    # title도 DB 출처지만 옵트인 product 데이터 = 외부 콘텐츠 → 방어적 escape.
    domain = html.escape(data.get("domain", "the store"))
    in_db = data.get("in_db", False)

    # FIX 2026-06-07: 다크모드 호환 (Apple Mail / Gmail iOS / Outlook 다크모드).
    # color-scheme meta + prefers-color-scheme CSS 추가 — 텍스트 가독성 보장.
    dark_head = (
        '<meta name="color-scheme" content="light dark">'
        '<meta name="supported-color-schemes" content="light dark">'
        '<style>'
        '@media (prefers-color-scheme: dark) {'
        '  body { background:#1C1917 !important; color:#F3F1EE !important; }'
        '  h2, h3 { color:#F3F1EE !important; }'
        '  div[style*="background:#F3F1EE"] { background:#2D2A26 !important; color:#F3F1EE !important; }'
        '  a { color:#818CF8 !important; }'
        '}'
        '</style>'
    )
    if not in_db:
        # 우리 DB에 없는 스토어 → 가치 제안 + 추가하겠다는 약속
        return f"""<!DOCTYPE html>
<html><head>{dark_head}</head><body style="font-family:-apple-system,sans-serif;max-width:580px;margin:0 auto;padding:24px;color:#1C1917">
<h2 style="font-size:20px;letter-spacing:-0.3px">Your X-Ray report — {domain}</h2>
<p>Thanks for trying StoreScope. You requested an X-Ray of <strong>{domain}</strong>.</p>
<p>This store is not yet in our weekly index (we currently scan 1,400+ stores). I've added it to the next pipeline run — you'll be the first to see when it gets indexed.</p>
<h3 style="font-size:16px;color:#4F46E5">What you get for free</h3>
<ul>
<li>Weekly digest sample: <a href="{APP_URL}/#trending">see this week's top trending products</a></li>
<li>X-Ray any of our indexed stores instantly: <a href="{APP_URL}/#xray">X-Ray tool</a></li>
<li>Reply to this email — I read every one.</li>
</ul>
<p style="font-size:13px;color:#78716C;margin-top:24px">— Dodo Kim, Founder · <a href="{APP_URL}">storescope.com</a></p>
</body></html>"""

    # 정상 케이스: DB 데이터 있음
    trending_html = ""
    if data.get("trending"):
        trending_html = "<h3 style='color:#4F46E5;font-size:16px;margin-top:24px'>Trending products this store carries</h3><ul>"
        for t in data["trending"]:
            t_title = html.escape((t['title'] or '')[:60])
            trending_html += f"<li><strong>{t_title}</strong> — found in {t['cluster_size']} other stores (${t['price_min']:.2f})</li>"
        trending_html += "</ul>"

    top_priced_html = ""
    if data.get("top_priced"):
        top_priced_html = "<h3 style='color:#4F46E5;font-size:16px;margin-top:24px'>Highest-priced products (top 5)</h3><ul>"
        for p in data["top_priced"]:
            p_title = html.escape((p['title'] or '')[:60])
            price = f"${p['price_min']:.2f}" if p["price_min"] == p["price_max"] else f"${p['price_min']:.2f}–${p['price_max']:.2f}"
            top_priced_html += f"<li><strong>{p_title}</strong> — {price}</li>"
        top_priced_html += "</ul>"

    return f"""<!DOCTYPE html>
<html><head>{dark_head}</head><body style="font-family:-apple-system,sans-serif;max-width:580px;margin:0 auto;padding:24px;color:#1C1917">
<h2 style="font-size:20px;letter-spacing:-0.3px">X-Ray report — {domain}</h2>
<p>Here's what we found in <strong>{domain}</strong>.</p>

<h3 style="font-size:16px;color:#4F46E5">Catalog at a glance</h3>
<ul>
<li><strong>{data['product_count']} products</strong> total in our index</li>
<li><strong>Price range:</strong> ${data['price_lo']:.2f} – ${data['price_hi']:.2f} (avg ${data['price_avg']:.2f}, {data['priced_n']} priced)</li>
</ul>

{trending_html}
{top_priced_html}

<div style="margin-top:32px;padding:20px;background:#F3F1EE;border-radius:10px">
<strong>Want this every Monday for 1,400+ stores?</strong><br>
StoreScope's weekly digest covers the entire cross-store catalog — what's spreading, what's plateauing, where prices are converging.
<br><br>
<a href="{APP_URL}/#pricing" style="display:inline-block;padding:10px 22px;background:#4F46E5;color:#fff;text-decoration:none;border-radius:8px;font-weight:700">Start 7-day free trial</a>
</div>

<p style="font-size:12px;color:#78716C;margin-top:32px">
You requested this X-Ray at {APP_URL}. Reply to this email or contact dodo@storescope.com.<br>
StoreScope · Shopify cross-store intelligence
</p>
</body></html>"""


def _render_text(email: str, data: dict) -> str:
    """Plain text fallback (대다수 메일 클라이언트가 HTML 우선이지만 fallback 필수)."""
    domain = data.get("domain", "the store")
    if not data.get("in_db"):
        return (
            f"X-Ray report — {domain}\n\n"
            f"Thanks for trying StoreScope. {domain} is not yet in our weekly index.\n"
            f"Visit {APP_URL}/#xray to X-Ray any of our 1,400+ indexed stores.\n\n"
            f"— Dodo Kim, Founder\n{APP_URL}"
        )
    lines = [
        f"X-Ray report — {domain}",
        "",
        f"Products in index: {data['product_count']}",
        f"Price range: ${data['price_lo']:.2f} – ${data['price_hi']:.2f} (avg ${data['price_avg']:.2f})",
        "",
    ]
    if data.get("trending"):
        lines.append("Trending products:")
        for t in data["trending"]:
            lines.append(f"  - {t['title'][:60]} (found in {t['cluster_size']} other stores)")
        lines.append("")
    lines.append(f"Start 7-day free trial: {APP_URL}/#pricing")
    lines.append("")
    lines.append("— Dodo Kim, Founder")
    return "\n".join(lines)


def send_xray_report(to_email: str, domain: Optional[str] = None) -> bool:
    """X-Ray report 발송. 성공 시 True, SMTP 미설정 시 console 출력 + False."""
    data = _fetch_store_data(domain or "")
    html = _render_html(to_email, data)
    text = _render_text(to_email, data)

    if not SMTP_HOST:
        # 개발 환경: 콘솔 출력. SMTP 미설정 시 silent fail 방지.
        _log.warning("[XRAY EMAIL STUB] To=%s Domain=%s", to_email, domain)
        print(f"\n=== XRAY REPORT EMAIL (SMTP 미설정 — STUB) ===")
        print(f"To: {to_email}")
        print(f"Subject: Your X-Ray report — {data.get('domain', domain)}")
        print(f"--- text body ---")
        print(text)
        print(f"--- end ---\n")
        return False

    try:
        msg = MIMEMultipart("alternative")
        msg["From"] = FROM_EMAIL
        msg["To"] = to_email
        msg["Subject"] = f"Your X-Ray report — {data.get('domain', domain or 'StoreScope')}"
        msg.attach(MIMEText(text, "plain", "utf-8"))
        msg.attach(MIMEText(html, "html", "utf-8"))
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=15) as srv:
            srv.ehlo()
            srv.starttls()
            srv.login(SMTP_USER, SMTP_PASS)
            srv.send_message(msg)
        _log.info("X-Ray report sent: to=%s domain=%s", to_email, domain)
        return True
    except Exception as exc:
        _log.exception("X-Ray report send failed: to=%s domain=%s err=%s", to_email, domain, exc)
        return False
