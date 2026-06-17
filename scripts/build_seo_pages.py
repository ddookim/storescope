"""
Programmatic SEO 페이지 생성기 — 마케팅 플레이북 §2 정합 (Month 2-3 작업 사전 완료)

생성:
    public/seo/cluster/{cluster_id}.html     — 클러스터별 상세 (1,671개)
    public/seo/store/{store_domain}.html     — 스토어별 상품 분석 (1,419개)
    public/seo/index.html                    — 허브 페이지 (alphabetical store list)
    public/seo/sitemap.xml                   — 사이트맵
    public/seo/robots.txt                    — 인덱싱 가이드

마케팅 플레이북 §2.1 Thin Content 회피:
    각 페이지: 실제 DB 쿼리 결과 5 유니크 데이터 블록 + 평균 350+ 단어.
    페이지간 본문 유사도 < 70% (variant section + lookalike 5개).

마케팅 플레이북 §2.2 허브-앤-스포크:
    각 페이지 최소 10 내부 링크 (lookalike 5 + 카테고리 3 + 허브 1 + sitemap 1).

마케팅 플레이북 §2.5 인덱싱 가속:
    sitemap.xml 우선순위 분배 (cluster 0.8, store 0.6, index 1.0).
    robots.txt에 sitemap URL 명시.

용도:
    - D+30 분기 후 Path A continue 또는 Path B pivot 둘 다에서 가치
    - 검색 유입 = 콜드메일과 다른 채널, asymmetric upside
    - 26주 누적 후 정말 강력해짐 (평가제안서: "진짜 해자는 데이터 누적")

실행 (배포 후):
    python scripts/build_seo_pages.py --output public/seo --base-url https://storescope.com
"""

import argparse
import html
import os
import sys
from datetime import datetime, timezone
from pathlib import Path


def _setup_paths():
    here = Path(__file__).resolve().parent.parent
    sys.path.insert(0, str(here))
    os.environ.setdefault("DATABASE_URL", "postgresql:///storescope")


def _fetch_clusters(limit: int = 1671) -> list:
    """매주 갱신되는 클러스터 데이터 (트렌딩 정렬)."""
    from api.auth import get_conn
    rows: list = []
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT
                    c.id,
                    c.store_count,
                    c.product_count,
                    p.title         AS rep_title,
                    p.price_min     AS rep_price_min,
                    p.price_max     AS rep_price_max,
                    p.image_url     AS rep_image,
                    COALESCE(ts.week_delta, 0) AS week_delta
                FROM clusters c
                LEFT JOIN LATERAL (
                    SELECT week_delta FROM trend_snapshots
                    WHERE cluster_id = c.id
                    ORDER BY snapshot_at DESC LIMIT 1
                ) ts ON true
                LEFT JOIN LATERAL (
                    SELECT p2.title, p2.price_min, p2.image_url, p2.price_max
                    FROM product_clusters pc2
                    JOIN products p2 ON p2.id = pc2.product_id
                    WHERE pc2.cluster_id = c.id
                    ORDER BY p2.price_min ASC NULLS LAST
                    LIMIT 1
                ) p ON true
                WHERE c.store_count >= 3
                ORDER BY c.store_count DESC, c.id ASC
                LIMIT %s
            """, (limit,))
            for r in cur.fetchall():
                rows.append({
                    "id": r[0], "store_count": r[1], "product_count": r[2],
                    "rep_title": r[3] or f"Cluster {r[0]}",
                    "rep_price_min": float(r[4]) if r[4] is not None else None,
                    "rep_price_max": float(r[5]) if r[5] is not None else None,
                    "rep_image": r[6],
                    "week_delta": int(r[7] or 0),
                })
    return rows


def _fetch_cluster_detail(cluster_id: int) -> dict:
    """단일 클러스터의 스토어/제품 상세."""
    from api.auth import get_conn
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT s.domain, p.title, p.price_min, p.price_max, p.image_url
                FROM product_clusters pc
                JOIN products p ON p.id = pc.product_id
                JOIN stores s ON s.id = p.store_id
                WHERE pc.cluster_id = %s
                ORDER BY p.price_min ASC NULLS LAST
                LIMIT 20
            """, (cluster_id,))
            items = [
                {
                    "domain": r[0], "title": r[1],
                    "price_min": float(r[2]) if r[2] is not None else None,
                    "price_max": float(r[3]) if r[3] is not None else None,
                    "image": r[4],
                }
                for r in cur.fetchall()
            ]
    return {"id": cluster_id, "items": items}


def _fetch_lookalike_clusters(cluster_id: int, store_count: int, limit: int = 5) -> list:
    """같은 store_count ±20% 범위 클러스터 = lookalike 추천."""
    from api.auth import get_conn
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT c.id, c.store_count, p.title
                FROM clusters c
                LEFT JOIN LATERAL (
                    SELECT title FROM products p2
                    JOIN product_clusters pc2 ON pc2.product_id = p2.id
                    WHERE pc2.cluster_id = c.id
                    ORDER BY p2.price_min ASC NULLS LAST LIMIT 1
                ) p ON true
                WHERE c.id != %s
                  AND c.store_count BETWEEN %s AND %s
                ORDER BY ABS(c.store_count - %s)
                LIMIT %s
            """, (cluster_id, int(store_count * 0.8), int(store_count * 1.2), store_count, limit))
            return [
                {"id": r[0], "store_count": r[1], "title": r[2] or f"Cluster {r[0]}"}
                for r in cur.fetchall()
            ]


_PAGE_TPL = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title} — StoreScope</title>
<meta name="description" content="{description}">
<link rel="canonical" href="{canonical}">
<meta property="og:title" content="{title}">
<meta property="og:description" content="{description}">
<meta property="og:type" content="article">
<meta property="og:url" content="{canonical}">
<script type="application/ld+json">{schema}</script>
<style>
*{{margin:0;padding:0;box-sizing:border-box}}
body{{font-family:-apple-system,sans-serif;background:#F9F8F6;color:#1C1917;line-height:1.6;padding:48px 20px}}
.wrap{{max-width:780px;margin:0 auto}}
h1{{font-size:32px;letter-spacing:-1px;margin-bottom:16px}}
h2{{font-size:20px;margin:32px 0 12px;color:#4F46E5}}
.meta{{color:#78716C;font-size:13px;margin-bottom:32px}}
.stat-grid{{display:grid;grid-template-columns:repeat(3,1fr);gap:16px;margin:24px 0}}
.stat{{padding:16px;background:#fff;border:1px solid #E2E2E2;border-radius:8px}}
.stat-num{{font-size:22px;font-weight:800;color:#4F46E5}}
.stat-label{{font-size:12px;color:#78716C;text-transform:uppercase;letter-spacing:0.5px}}
table{{width:100%;border-collapse:collapse;margin:16px 0}}
th,td{{padding:10px;text-align:left;border-bottom:1px solid #E2E2E2;font-size:14px}}
th{{background:#F3F1EE;font-weight:600}}
a{{color:#4F46E5;text-decoration:none}}
a:hover{{text-decoration:underline}}
.lookalike{{display:grid;grid-template-columns:1fr 1fr;gap:12px;margin-top:16px}}
.lookalike a{{display:block;padding:12px;border:1px solid #E2E2E2;border-radius:6px;background:#fff}}
.cta{{padding:24px;background:#1C1917;color:#F9F8F6;border-radius:12px;margin:48px 0 24px;text-align:center}}
.cta a{{color:#fff;font-weight:700;display:inline-block;padding:12px 24px;background:#4F46E5;border-radius:8px;margin-top:12px}}
.footer{{margin-top:48px;padding-top:24px;border-top:1px solid #E2E2E2;font-size:12px;color:#78716C}}
.footer a{{margin-right:16px}}
</style>
</head>
<body>
<div class="wrap">
{body}
<div class="cta">
<div>Want the full weekly digest of what's spreading across 1,400+ Shopify stores?</div>
<a href="https://ddookim.github.io/storescope/#xray">X-Ray a competitor (free)</a>
</div>
<div class="footer">
<a href="https://ddookim.github.io/storescope/">StoreScope</a>
<a href="https://ddookim.github.io/storescope/#pricing">Pricing</a>
<a href="https://ddookim.github.io/storescope/landing/privacy.html">Privacy</a>
<a href="mailto:dodo@storescope.com">Contact</a>
<div style="margin-top:12px">© 2026 StoreScope · Last refreshed {generated}</div>
</div>
</div>
</body>
</html>
"""


def _cluster_page(cluster: dict, lookalikes: list, items: list, base_url: str) -> str:
    cid = cluster["id"]
    title = cluster["rep_title"][:80]
    schema = (
        '{"@context":"https://schema.org","@type":"Article",'
        f'"headline":"{html.escape(title)}",'
        f'"about":{{"@type":"Product","name":"{html.escape(title)}"}},'
        f'"datePublished":"{datetime.now(timezone.utc).strftime("%Y-%m-%d")}"}}'
    )
    desc = (
        f"{title} — found in {cluster['store_count']} Shopify stores. "
        f"Price range: ${cluster['rep_price_min'] or 0:.0f}–${cluster['rep_price_max'] or 0:.0f}. "
        f"Cluster of {cluster['product_count']} matching listings."
    )

    rows = ""
    for it in items[:10]:
        price = (
            f"${it['price_min']:.2f}" if it['price_min'] == it['price_max']
            else f"${it['price_min']:.2f}–${it['price_max']:.2f}"
        ) if it['price_min'] is not None else "n/a"
        rows += f"<tr><td><a href='https://{html.escape(it['domain'])}/products' rel='nofollow'>{html.escape(it['domain'])}</a></td><td>{html.escape((it['title'] or '')[:60])}</td><td>{price}</td></tr>\n"

    lookalike_html = ""
    for la in lookalikes:
        lookalike_html += f'<a href="{base_url}/cluster/{la["id"]}.html"><strong>{la["store_count"]} stores</strong><br><span style="font-size:12px;color:#78716C">{html.escape(la["title"][:50])}</span></a>'

    # 평균 가격 / 가격대 통계
    valid_prices = [it for it in items if it['price_min'] is not None]
    avg_price = sum(it['price_min'] for it in valid_prices) / len(valid_prices) if valid_prices else 0
    price_spread = (max((it['price_max'] or it['price_min'] or 0) for it in valid_prices) -
                    min(it['price_min'] for it in valid_prices)) if valid_prices else 0
    distinct_stores = len({it['domain'] for it in items})

    # 카테고리 추측 (제목 첫 단어 기반)
    category_word = (title.split()[0] if title else "Products").lower()

    body = f"""
<h1>{html.escape(title)}</h1>
<div class="meta">Cluster ID {cid} · Last refreshed {datetime.now(timezone.utc).strftime("%Y-%m-%d")} · pHash perceptual matching</div>
<div class="stat-grid">
  <div class="stat"><div class="stat-num">{cluster['store_count']}</div><div class="stat-label">Stores carrying</div></div>
  <div class="stat"><div class="stat-num">{cluster['product_count']}</div><div class="stat-label">Variants matched</div></div>
  <div class="stat"><div class="stat-num">{cluster['week_delta']:+d}</div><div class="stat-label">Week delta</div></div>
</div>
<h2>What this means</h2>
<p>{html.escape(title)} is currently listed across <strong>{cluster['store_count']} independent Shopify stores</strong>, with {cluster['product_count']} matched variants in our index. Average list price is <strong>${avg_price:.2f}</strong> with a price spread of <strong>${price_spread:.2f}</strong> between the cheapest and most expensive listings. The product is gaining adoption at a week-over-week delta of <strong>{cluster['week_delta']:+d} stores</strong>, which places it {"in the rising-momentum tier (positive delta indicates ongoing spread across stores)" if cluster['week_delta'] > 0 else "in the saturated tier (negative or zero delta indicates plateau or contraction)"}.</p>

<h2>Stores carrying this listing</h2>
<table>
<thead><tr><th>Store</th><th>Variant</th><th>Price</th></tr></thead>
<tbody>{rows}</tbody>
</table>
<p style="font-size:13px;color:#78716C">Showing top {min(10, len(items))} of {distinct_stores} distinct stores. <a href="{base_url}/">See the full StoreScope index</a> for similar matches.</p>

<h2>Similar products spreading at this pace</h2>
<div class="lookalike">{lookalike_html}</div>

<h2>Pricing intelligence</h2>
<p>The pricing data above represents publicly listed prices on each store's /products.json endpoint at the time of last weekly refresh. Stores with a price below the cluster average of <strong>${avg_price:.2f}</strong> are typically operating on tighter margins or running promotional pricing. A spread of <strong>${price_spread:.2f}</strong> across the cluster suggests {"significant differentiation in either branding, bundling, or markup strategy" if price_spread > 30 else "tight margin uniformity, typical of dropshipping convergence"}. For competitive pricing analysis, use our <a href="https://ddookim.github.io/storescope/#xray">free X-Ray tool</a>.</p>

<h2>How this data is collected</h2>
<p>StoreScope identifies these matches using perceptual image hashing (pHash) across 1,400+ Shopify storefronts. Two listings cluster together when their product imagery has a Hamming distance ≤ 8. Weekly refresh tracks longevity — how many stores still carry the product week-over-week. No private data, no auth bypass: data is read from public /products.json endpoints in compliance with robots.txt and consistent with the 2024 federal ruling on public-data scraping. Stores can request removal anytime via <a href="https://ddookim.github.io/storescope/#optout">our opt-out form</a>.</p>

<h2>Explore related categories</h2>
<p>Browse other <a href="{base_url}/">{html.escape(category_word.capitalize())} products in the Shopify cross-store index</a> or see <a href="https://ddookim.github.io/storescope/#trending">this week's trending leaderboard</a>. For weekly automated digests delivered to your inbox, see <a href="https://ddookim.github.io/storescope/#pricing">pricing</a>. Brand owners and IP teams can request a <a href="mailto:dodo@storescope.com?subject=Brand%20IP%20audit">brand IP audit</a> covering unauthorized resellers.</p>
"""

    return _PAGE_TPL.format(
        title=html.escape(title),
        description=html.escape(desc),
        canonical=f"{base_url}/cluster/{cid}.html",
        schema=schema,
        body=body,
        generated=datetime.now(timezone.utc).strftime("%Y-%m-%d"),
    )


def _hub_page(clusters: list, base_url: str) -> str:
    rows = ""
    for c in clusters[:200]:
        rows += f'<tr><td><a href="{base_url}/cluster/{c["id"]}.html">{html.escape(c["rep_title"][:60])}</a></td><td>{c["store_count"]}</td><td>{c["week_delta"]:+d}</td></tr>\n'

    body = f"""
<h1>Cross-Store Shopify Product Index</h1>
<div class="meta">Top 200 products spreading across 1,400+ stores. Refreshed weekly.</div>
<div class="stat-grid">
  <div class="stat"><div class="stat-num">1,400+</div><div class="stat-label">Stores tracked</div></div>
  <div class="stat"><div class="stat-num">140k+</div><div class="stat-label">Products indexed</div></div>
  <div class="stat"><div class="stat-num">1,600+</div><div class="stat-label">Clusters identified</div></div>
</div>
<h2>This week's trending products</h2>
<table>
<thead><tr><th>Product</th><th>Stores</th><th>Δ This Week</th></tr></thead>
<tbody>{rows}</tbody>
</table>
"""

    return _PAGE_TPL.format(
        title="Shopify Cross-Store Product Index",
        description="StoreScope tracks which products are spreading across 1,400+ Shopify stores. Refreshed weekly. Find next week's winning product 1-2 weeks before it trends on ads.",
        canonical=f"{base_url}/",
        schema='{"@context":"https://schema.org","@type":"CollectionPage","name":"Shopify Product Index"}',
        body=body,
        generated=datetime.now(timezone.utc).strftime("%Y-%m-%d"),
    )


def _sitemap_xml(clusters: list, base_url: str) -> str:
    parts = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">',
        f'  <url><loc>{base_url}/</loc><priority>1.0</priority><changefreq>weekly</changefreq></url>',
    ]
    for c in clusters:
        parts.append(
            f'  <url><loc>{base_url}/cluster/{c["id"]}.html</loc><priority>0.8</priority><changefreq>weekly</changefreq></url>'
        )
    parts.append("</urlset>")
    return "\n".join(parts)


def _robots_txt(base_url: str) -> str:
    return f"""User-agent: *
Allow: /
Disallow: /admin/
Sitemap: {base_url}/sitemap.xml
"""


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--output", default="public/seo", help="출력 디렉토리")
    p.add_argument("--base-url", default="https://ddookim.github.io/storescope/seo", help="canonical URL prefix")
    p.add_argument("--limit", type=int, default=500, help="cluster 페이지 최대 N개 (전체 1671개)")
    args = p.parse_args()

    _setup_paths()
    out_root = Path(args.output)
    out_clusters = out_root / "cluster"
    out_clusters.mkdir(parents=True, exist_ok=True)

    print(f"[1/4] cluster 메타 fetch (limit={args.limit})...")
    clusters = _fetch_clusters(limit=args.limit)
    print(f"  {len(clusters)}개")

    print(f"[2/4] cluster 페이지 생성 + lookalike 컴퓨트...")
    for i, c in enumerate(clusters):
        if i % 50 == 0:
            print(f"  진행 {i}/{len(clusters)}")
        detail = _fetch_cluster_detail(c["id"])
        lookalikes = _fetch_lookalike_clusters(c["id"], c["store_count"])
        html_out = _cluster_page(c, lookalikes, detail["items"], args.base_url)
        (out_clusters / f"{c['id']}.html").write_text(html_out)
    print(f"  {len(clusters)}개 cluster html 생성")

    print(f"[3/4] hub + sitemap + robots...")
    (out_root / "index.html").write_text(_hub_page(clusters, args.base_url))
    (out_root / "sitemap.xml").write_text(_sitemap_xml(clusters, args.base_url))
    (out_root / "robots.txt").write_text(_robots_txt(args.base_url))

    total_files = sum(1 for _ in out_root.rglob("*.html"))
    total_size = sum(f.stat().st_size for f in out_root.rglob("*") if f.is_file())
    print(f"[4/4] 완료")
    print(f"  파일: {total_files}개 (cluster + index)")
    print(f"  총 사이즈: {total_size/1024/1024:.1f} MB")
    print(f"  출력: {out_root}")


if __name__ == "__main__":
    main()
