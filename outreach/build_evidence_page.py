"""
증거페이지 (Top 5 트렌딩 상품) HTML 자동 생성기.
입력: DB의 clusters + product_clusters + products + stores
출력: outreach/evidence/top5-{date}.html  (1장)

콜드메일 본문 → 이 페이지 링크 → 받은 사람이 "이번 주 1,400+ 스토어에서
폭발 중인 상품 Top 5" 확인 + Trial CTA.

Constraint:
- 한 번 실행으로 1장 정적 HTML 생성 (서버 의존성 0)
- StoreScope 브랜드 라인 일관 (랜딩 폰트 · 색상 · 톤)
- 24시간 신선도 명시 (Dead-man UX와 일관)
"""
from __future__ import annotations

import html
import os
from datetime import datetime, timezone
from pathlib import Path

import psycopg2

OUT = Path(__file__).resolve().parent / "evidence"
OUT.mkdir(exist_ok=True)

TRIAL_URL = "https://ddookim.github.io/storescope/#pricing"
LANDING_URL = "https://ddookim.github.io/storescope/"

CSS = """
:root {
  --indigo: #4F46E5;
  --indigo-deep: #312E81;
  --stone: #1C1917;
  --muted: #57534E;
  --cream: #F9F8F6;
  --border: #E2E8F0;
}
* { box-sizing: border-box; margin: 0; padding: 0; }
body {
  font: 16px/1.55 'Inter', -apple-system, BlinkMacSystemFont, sans-serif;
  background: var(--cream);
  color: var(--stone);
  padding: 48px 24px;
  -webkit-font-smoothing: antialiased;
}
.wrap { max-width: 720px; margin: 0 auto; }
header { margin-bottom: 32px; }
.brand { font-weight: 900; color: var(--indigo); letter-spacing: -0.5px; font-size: 14px; text-transform: uppercase; }
h1 { font-size: 36px; font-weight: 900; letter-spacing: -1.2px; line-height: 1.1; margin: 8px 0 12px; }
.meta { font-size: 14px; color: var(--muted); }
.fresh { display: inline-block; padding: 2px 10px; border-radius: 999px; background: #DCFCE7; color: #0F763D; font-weight: 700; font-size: 12px; margin-left: 6px; }
.fresh.amber { background: #FEF3C7; color: #92400E; }
.fresh.red { background: #FEE2E2; color: #B91C1C; }
ol.products { list-style: none; counter-reset: rank; margin: 32px 0; }
ol.products > li {
  counter-increment: rank;
  background: #fff;
  border: 1px solid var(--border);
  border-radius: 16px;
  padding: 20px 24px;
  margin-bottom: 12px;
  display: grid;
  grid-template-columns: 36px 64px 1fr auto;
  gap: 16px;
  align-items: center;
}
ol.products > li::before {
  content: counter(rank);
  font-weight: 900;
  font-size: 22px;
  color: var(--indigo);
}
.thumb { width: 64px; height: 64px; border-radius: 8px; background: #f1f1f1 center/cover no-repeat; }
.title { font-size: 15px; font-weight: 700; line-height: 1.3; }
.sub { font-size: 13px; color: var(--muted); margin-top: 4px; }
.stat { text-align: right; font-size: 14px; }
.stat b { font-size: 18px; color: var(--indigo-deep); }
.cta {
  background: var(--indigo);
  color: white;
  display: block;
  padding: 18px 24px;
  border-radius: 12px;
  text-align: center;
  font-weight: 700;
  text-decoration: none;
  margin-top: 24px;
}
.cta:hover { background: var(--indigo-deep); }
footer { font-size: 12px; color: var(--muted); margin-top: 24px; text-align: center; }
footer a { color: var(--indigo); text-decoration: none; }
"""


def fetch_top_products(conn, n: int = 5) -> list[dict]:
    cur = conn.cursor()
    cur.execute(
        """
        SELECT
          c.id, c.store_count, c.product_count,
          p.title, p.image_url, p.price_min, p.price_max
        FROM clusters c
        JOIN LATERAL (
          SELECT title, image_url, price_min, price_max
          FROM products pp
          JOIN product_clusters pc ON pp.id = pc.product_id
          WHERE pc.cluster_id = c.id
          ORDER BY pp.first_seen ASC
          LIMIT 1
        ) p ON TRUE
        ORDER BY c.store_count DESC
        LIMIT %s
        """,
        (n,),
    )
    return [
        {
            "cluster_id": r[0],
            "store_count": r[1],
            "product_count": r[2],
            "title": r[3] or "Untitled",
            "image_url": r[4] or "",
            "price_min": float(r[5]) if r[5] is not None else None,
            "price_max": float(r[6]) if r[6] is not None else None,
        }
        for r in cur.fetchall()
    ]


def freshness(conn) -> tuple[str, str]:
    cur = conn.cursor()
    cur.execute("SELECT MAX(snapshot_at) FROM trend_snapshots")
    row = cur.fetchone()
    last = row[0] if row else None
    if last is None:
        return ("red", "no data yet")
    if last.tzinfo is None:
        last = last.replace(tzinfo=timezone.utc)
    hours = (datetime.now(timezone.utc) - last).total_seconds() / 3600
    if hours < 24:
        return ("", f"updated {hours:.0f} h ago")
    if hours < 72:
        return ("amber", f"updated {hours / 24:.1f} d ago — pipeline catching up")
    return ("red", f"updated {hours / 24:.1f} d ago — free preview mode")


def price_line(p: dict) -> str:
    mn, mx = p["price_min"], p["price_max"]
    if mn is None and mx is None:
        return ""
    if mn == mx or mx is None:
        return f"${mn:.0f}"
    return f"${mn:.0f}–${mx:.0f}"


def render(products: list[dict], fresh_cls: str, fresh_text: str, today: str) -> str:
    items = []
    for p in products:
        title = html.escape(p["title"][:90])
        img = html.escape(p["image_url"])
        stores = p["store_count"]
        price = html.escape(price_line(p))
        items.append(
            f"""<li>
              <div class="thumb" style="background-image:url('{img}')"></div>
              <div>
                <div class="title">{title}</div>
                <div class="sub">cluster #{p["cluster_id"]} · {p["product_count"]} matching listings · {price}</div>
              </div>
              <div class="stat"><b>{stores}</b><br>stores</div>
            </li>"""
        )
    return f"""<!doctype html>
<html lang="en"><head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>StoreScope — Top 5 viral products this week</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;700;900&display=swap" rel="stylesheet">
<style>{CSS}</style>
</head><body>
<div class="wrap">
<header>
  <div class="brand">StoreScope</div>
  <h1>Top 5 products gaining store reach this week</h1>
  <div class="meta">Ranked by unique store count across 1,400+ Shopify stores indexed weekly. {today} <span class="fresh {fresh_cls}">{fresh_text}</span></div>
</header>

<ol class="products">
{"".join(items)}
</ol>

<a class="cta" href="{TRIAL_URL}?utm_source=evidence&utm_campaign=d1">Get the full weekly list (Free trial, no card)</a>
<a class="cta" style="background:#fff;color:var(--indigo);border:1px solid var(--indigo)" href="{LANDING_URL}?utm_source=evidence&utm_campaign=d1">Try the free X-Ray tool first</a>

<footer>
  StoreScope — cross-store Shopify product intelligence ·
  <a href="mailto:dodo@storescope.com">dodo@storescope.com</a>
</footer>
</div>
</body></html>"""


def main() -> None:
    conn = psycopg2.connect(os.environ["DATABASE_URL"])
    try:
        fresh_cls, fresh_text = freshness(conn)
        products = fetch_top_products(conn, n=5)
        today = datetime.utcnow().strftime("%Y-%m-%d")
        html_out = render(products, fresh_cls, fresh_text, today)
    finally:
        conn.close()

    out = OUT / f"top5-{today}.html"
    out.write_text(html_out, encoding="utf-8")
    print(f"✓ Generated: {out}")
    print(f"  Products: {len(products)} · Top stores: {products[0]['store_count'] if products else 0}")
    print(f"  Freshness: [{fresh_cls or 'live'}] {fresh_text}")


if __name__ == "__main__":
    main()
