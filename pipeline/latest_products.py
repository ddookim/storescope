"""
Latest Products Detection — cross-brand newest product aggregation.

DTC seed (curated_shopify_stores.txt 40 brands) 에서 pHash 클러스터링은
false positive 100% (각 브랜드 unique 제품). Fallback signal:
'이번 주 신상' = published_at 기반 최신 상품 top-N.

이는 진짜 트렌드 signal: DTC 브랜드가 launch 하는 제품 = 다음 주 SNS/광고 노출.

입력: data/products/*.json (STEP 2 crawl output, 각 스토어별)
출력: data/latest_products.json (top 20, published_at DESC)

실행:
    python -m pipeline.latest_products

호출: run_pipeline.py STEP 3.5 (cluster 이후, load_to_db 이전).
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path

_HERE = Path(__file__).resolve().parent.parent
PRODUCTS_DIR    = _HERE / "data" / "products"
OUTPUT_FILE     = _HERE / "data" / "latest_products.json"
META_FILE       = _HERE / "data" / "latest_products_meta.json"  # category summary + stats
HISTORY_DIR     = _HERE / "data" / "history"                    # D+11: weekly snapshots for WoW delta
SAMPLE_HTML     = _HERE / "landing" / "digest-sample.html"  # D+11: SEO + demo asset
SITEMAP_FILE    = _HERE / "landing" / "sitemap.xml"        # D+11: auto lastmod update

CATEGORY_TOP_N  = 8  # digest 표시 카테고리 개수

TOP_N              = 20     # digest 표시 개수
LOOKBACK_DAYS      = 30     # 30일 이내 published 만 대상 (fresh signal)
MIN_TITLE_LEN      = 8      # placeholder/junk 제외 ("Test", "Sample" 등)
MAX_PER_VENDOR     = 2      # D+11: 편중 방지 (5x 같은 브랜드 방지, UX 이슈)
MAX_PER_TYPE       = 4      # D+11: product_type 편중 방지 (Semi-Permanent 5개 등)


_ISO_CLEAN = re.compile(r"([+-]\d{2}):?(\d{2})$")


def _parse_iso(s: str) -> datetime | None:
    if not s:
        return None
    # Shopify format: '2026-05-19T10:19:28+08:00' — Python 3.11 fromisoformat OK.
    try:
        return datetime.fromisoformat(s)
    except (ValueError, TypeError):
        return None


def _extract_products(now: datetime) -> list[dict]:
    """모든 스토어의 최신 상품 추출 (30일 이내 published, 정렬 준비된 flat list)."""
    cutoff = now.timestamp() - LOOKBACK_DAYS * 86400
    all_products: list[dict] = []

    for f in sorted(PRODUCTS_DIR.glob("*.json")):
        try:
            data = json.loads(f.read_text())
        except json.JSONDecodeError:
            continue
        domain = data.get("domain") or f.stem
        for p in data.get("products", []):
            title = (p.get("title") or "").strip()
            if len(title) < MIN_TITLE_LEN:
                continue
            published = _parse_iso(p.get("published_at") or "")
            if not published:
                continue
            if published.timestamp() < cutoff:
                continue

            images = p.get("images") or []
            image_url = images[0].get("src", "") if images else ""

            # Extract price from first variant (Shopify convention).
            variants = p.get("variants") or []
            price = None
            if variants:
                try:
                    price = float(variants[0].get("price") or 0)
                except (ValueError, TypeError):
                    price = None

            all_products.append({
                "domain": domain,
                "title": title[:120],
                "handle": p.get("handle", ""),
                "product_type": p.get("product_type", ""),
                "vendor": p.get("vendor", ""),
                "published_at": p["published_at"],
                "price": price,
                "image_url": image_url,
                # 'age_days' = for digest ranking + display (freshness signal).
                "age_days": round((now.timestamp() - published.timestamp()) / 86400, 1),
            })

    return all_products


def _dedupe_by_title_domain(products: list[dict]) -> list[dict]:
    """같은 domain + 유사 title 중복 제거 (variant explosion 방지)."""
    seen: set[tuple[str, str]] = set()
    unique: list[dict] = []
    for p in products:
        # Normalized title key: lowercase, strip punctuation, first 40 chars.
        key = (p["domain"], re.sub(r"[^a-z0-9]+", "", p["title"].lower())[:40])
        if key in seen:
            continue
        seen.add(key)
        unique.append(p)
    return unique


def _update_sitemap_lastmod(now: datetime) -> None:
    """digest-sample.html + index lastmod → 오늘 날짜.

    Google 은 sitemap lastmod 를 crawl priority signal 로 사용 (freshness).
    매주 pipeline 실행 시 auto-update 하면 새 콘텐츠 반영 accelerate.
    """
    if not SITEMAP_FILE.exists():
        return
    today = now.strftime("%Y-%m-%d")
    text = SITEMAP_FILE.read_text()

    # Update index.html + digest-sample.html lastmod. Other URLs (privacy, terms, blog) 유지.
    import re
    # 1. Root URL entry (index) — first <loc> matches.
    text = re.sub(
        r"(<loc>https://ddookim\.github\.io/storescope/</loc>\s*<lastmod>)[^<]*(</lastmod>)",
        rf"\g<1>{today}\g<2>", text, count=1,
    )
    # 2. digest-sample.html entry.
    text = re.sub(
        r"(<loc>https://ddookim\.github\.io/storescope/digest-sample\.html</loc>\s*<lastmod>)[^<]*(</lastmod>)",
        rf"\g<1>{today}\g<2>", text, count=1,
    )
    SITEMAP_FILE.write_text(text)
    print(f"  → sitemap lastmod = {today}")


def _save_historical_snapshot(now: datetime) -> None:
    """Copy previous week's latest_products.json to data/history/ for future WoW delta.

    Idempotent: 같은 주에 여러 번 실행돼도 하나만 저장.
    Only copies if OUTPUT_FILE exists (직전 pipeline run 결과).
    """
    if not OUTPUT_FILE.exists():
        return
    HISTORY_DIR.mkdir(parents=True, exist_ok=True)
    # Snapshot filename: history/YYYY-WNN.json (ISO week)
    prev_week = f"{now.isocalendar().year}-W{now.isocalendar().week:02d}"
    snap = HISTORY_DIR / f"{prev_week}.json"
    # Idempotent: 이미 있으면 skip.
    if snap.exists():
        return
    snap.write_text(OUTPUT_FILE.read_text())
    print(f"  → history snapshot: {snap.name}")


def _category_summary(all_recent_products: list[dict]) -> list[dict]:
    """product_type 별 카테고리 신제품 수 + unique vendor 수 계산.

    Input: 30일 window filtered + dedupe 통과한 모든 상품 (top_N slice 이전).
    Output: [{product_type, product_count, vendor_count, top_vendors}, ...]
        sorted by product_count DESC.
    "" or "Uncategorized" 는 별도 표기.
    """
    from collections import defaultdict
    by_type: dict[str, list[dict]] = defaultdict(list)
    for p in all_recent_products:
        t = (p.get("product_type") or "Uncategorized").strip() or "Uncategorized"
        by_type[t].append(p)

    summaries: list[dict] = []
    for ptype, items in by_type.items():
        vendors = list({p.get("vendor", "") for p in items if p.get("vendor")})
        summaries.append({
            "product_type": ptype[:60],
            "product_count": len(items),
            "vendor_count": len(vendors),
            "top_vendors": sorted(vendors)[:3],
        })
    summaries.sort(key=lambda s: (-s["product_count"], -s["vendor_count"]))
    return summaries[:CATEGORY_TOP_N]


def _diversity_cap(products: list[dict], max_per_vendor: int, max_per_type: int) -> list[dict]:
    """Round-robin 스타일 다양성 필터.

    Input 은 age_days ASC 정렬 상태 가정. 각 vendor 최대 N개, product_type 최대 M개.
    나머지 신제품이 다른 브랜드로 넘어가도록 유지.
    """
    from collections import Counter
    vendor_count: Counter[str] = Counter()
    type_count: Counter[str] = Counter()
    picked: list[dict] = []
    overflow: list[dict] = []
    for p in products:
        v = p.get("vendor", "") or "?"
        t = p.get("product_type", "") or "?"
        if vendor_count[v] >= max_per_vendor or type_count[t] >= max_per_type:
            overflow.append(p)
            continue
        vendor_count[v] += 1
        type_count[t] += 1
        picked.append(p)
    return picked + overflow  # overflow 는 뒤로 밀림 → top_N slice 로 자연 제외


def main() -> None:
    if not PRODUCTS_DIR.exists():
        print(f"[SKIP] {PRODUCTS_DIR} 없음 — crawl 먼저 실행")
        # write empty output so downstream steps don't 404.
        OUTPUT_FILE.parent.mkdir(parents=True, exist_ok=True)
        OUTPUT_FILE.write_text("[]")
        return

    # D+11 history: 매주 snapshot 저장 → 향후 WoW delta 계산 가능.
    _save_historical_snapshot(datetime.now(timezone.utc))

    now = datetime.now(timezone.utc)
    products = _extract_products(now)
    print(f"  {LOOKBACK_DAYS}일 내 published: {len(products):,}개")

    products = _dedupe_by_title_domain(products)
    print(f"  dedupe 후: {len(products):,}개")

    # Category summary — computed BEFORE diversity cap (전체 signal 반영).
    categories = _category_summary(products)
    print(f"  categories: {len(categories)}개 (top {CATEGORY_TOP_N})")

    # Sort: newest first (published_at DESC → age_days ASC).
    products.sort(key=lambda p: p["age_days"])
    # D+11 diversity: prevent 5x same vendor / 4x same product_type in top output.
    products = _diversity_cap(products, MAX_PER_VENDOR, MAX_PER_TYPE)
    top = products[:TOP_N]

    OUTPUT_FILE.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_FILE.write_text(json.dumps(top, ensure_ascii=False, indent=2))
    print(f"  → {OUTPUT_FILE} ({len(top)}개, top {TOP_N})")

    # Meta: category summary + generation stats (별도 파일, backward compat).
    META_FILE.write_text(json.dumps({
        "generated_at": now.isoformat(),
        "week": f"{now.isocalendar().year}-W{now.isocalendar().week:02d}",
        "total_products_before_dedupe": None,   # (extract 시 num 있으면 채움 — 지금은 stub)
        "total_after_dedupe": len(products),
        "top_products_returned": len(top),
        "categories": categories,
    }, ensure_ascii=False, indent=2))
    print(f"  → {META_FILE}")

    if top:
        print("\n=== 상위 5개 최신 상품 ===")
        for i, p in enumerate(top[:5], 1):
            print(f"  {i}. [{p['age_days']}d] {p['title'][:60]}")
            print(f"     {p['vendor']} · {p['product_type']} · ${p['price'] or '—'}")

    if categories:
        print("\n=== Category summary ===")
        for c in categories[:5]:
            print(f"  {c['product_type'][:40]:<40} · {c['product_count']:>3} products · {c['vendor_count']} vendors")

    # D+11: Sitemap lastmod auto-update (SEO freshness signal).
    _update_sitemap_lastmod(now)

    _write_sample_html(top, categories, now)


def _write_sample_html(products: list[dict], categories: list[dict], now: datetime) -> None:
    """Regenerate landing/digest-sample.html — SEO + demo asset (D+11).

    사용자가 landing 방문 시 '실제로 받는 digest 는 뭐?' 궁금증 해결.
    Auto-updated every pipeline run (Monday 08:00 KST).
    """
    from html import escape as h
    week = f"{now.isocalendar().year}-W{now.isocalendar().week:02d}"

    rows = []
    for i, p in enumerate(products[:20], 1):
        title = h(p.get("title") or "Untitled")[:100]
        vendor = h(p.get("vendor") or "—")
        ptype = h(p.get("product_type") or "")
        price = p.get("price")
        price_str = f"${price:.2f}" if isinstance(price, (int, float)) and price > 0 else "—"
        age = p.get("age_days", "?")
        rows.append(f"""
      <tr>
        <td>{i}</td>
        <td><strong>{title}</strong><br><span class="meta">{vendor} · {ptype}</span></td>
        <td class="right">{age}d</td>
        <td class="right">{price_str}</td>
      </tr>""")
    table = "".join(rows) or '<tr><td colspan=4 style="padding:24px;text-align:center;color:#78716C">No qualifying products this week. Digest skipped.</td></tr>'

    # Category chips (D+11) — displayed above product table.
    cat_chips = ""
    if categories:
        chips = []
        for c in categories:
            n = c.get("product_count", 0)
            v = c.get("vendor_count", 0)
            ptype = h(c.get("product_type") or "?")[:40]
            # 'vendor' == Shopify /products.json 'vendor' field (product-level, 아티스트/디자이너/브랜드 태그).
            # Ceargone 같은 marketplace 는 아티스트, DTC 브랜드는 자기 이름 등록 → 정직 매핑.
            v_word = "vendor" if v == 1 else "vendors"
            n_word = "new item" if n == 1 else "new items"
            chips.append(f'<span class="cat-chip"><strong>{ptype}</strong> · {n} {n_word} · {v} {v_word}</span>')
        cat_chips = f'''
<section class="cat-section">
  <h2>Hot categories · last {LOOKBACK_DAYS} days</h2>
  <div class="cat-grid">{"".join(chips)}</div>
</section>'''

    html_out = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="description" content="StoreScope Weekly Sample — real digest from {week}. {len(products)} newest product launches across curated DTC Shopify brands. See the actual signal subscribers receive.">
<meta name="robots" content="index, follow">
<link rel="canonical" href="https://ddookim.github.io/storescope/digest-sample.html">
<title>Weekly Digest Sample · {week} — StoreScope</title>

<!-- Open Graph — Twitter/Facebook/LinkedIn/Slack preview cards (D+11). -->
<meta property="og:title" content="Weekly Digest Sample · {week} — StoreScope" />
<meta property="og:description" content="{len(products)} newest DTC brand product launches, updated every Monday. What subscribers actually get." />
<meta property="og:type" content="article" />
<meta property="og:url" content="https://ddookim.github.io/storescope/digest-sample.html" />
<meta property="og:image" content="https://ddookim.github.io/storescope/landing/og-image.png" />
<meta property="og:site_name" content="StoreScope" />
<meta property="article:published_time" content="{now.isoformat()}" />

<!-- Twitter Card summary_large_image (image visible in feed). -->
<meta name="twitter:card" content="summary_large_image" />
<meta name="twitter:title" content="Weekly Digest Sample · {week}" />
<meta name="twitter:description" content="{len(products)} DTC product launches from last 30 days. Real signal, updated Monday." />
<meta name="twitter:image" content="https://ddookim.github.io/storescope/landing/og-image.png" />

<!-- Article schema (SEO structured data). -->
<script type="application/ld+json">
{{
  "@context": "https://schema.org",
  "@type": "Article",
  "headline": "Weekly Digest Sample · {week} — StoreScope",
  "description": "{len(products)} newest DTC brand product launches. Real signal delivered weekly.",
  "url": "https://ddookim.github.io/storescope/digest-sample.html",
  "datePublished": "{now.strftime('%Y-%m-%d')}",
  "dateModified": "{now.strftime('%Y-%m-%d')}",
  "author": {{"@type": "Organization", "name": "StoreScope"}},
  "publisher": {{
    "@type": "Organization", "name": "StoreScope",
    "logo": {{"@type": "ImageObject", "url": "https://ddookim.github.io/storescope/landing/og-image.png"}}
  }},
  "mainEntityOfPage": {{"@type": "WebPage", "@id": "https://ddookim.github.io/storescope/digest-sample.html"}}
}}
</script>
<style>
  body {{ font-family: -apple-system, "Inter", sans-serif; max-width: 720px; margin: 0 auto; padding: 60px 24px; color: #1C1917; line-height: 1.65; background: #F9F8F6; }}
  .breadcrumb {{ font-size: 0.85rem; color: #6B655F; margin-bottom: 24px; }}
  .breadcrumb a {{ color: #4338ca; text-decoration: underline; text-decoration-color: rgba(67,56,202,0.35); text-underline-offset: 3px; }}
  h1 {{ font-size: 2rem; font-weight: 800; letter-spacing: -0.02em; margin-bottom: 0.4rem; line-height: 1.15; }}
  .intro {{ color: #57534E; margin-bottom: 32px; }}
  .badge {{ display: inline-block; padding: 4px 12px; background: rgba(79,70,229,0.08); color: #4338ca; font-size: 0.8rem; font-weight: 700; border-radius: 100px; margin-bottom: 20px; letter-spacing: 0.3px; }}
  table {{ width: 100%; border-collapse: collapse; margin: 24px 0; font-size: 14px; background: #fff; border-radius: 12px; overflow: hidden; box-shadow: 0 1px 3px rgba(0,0,0,0.04); }}
  th, td {{ text-align: left; padding: 14px 16px; border-bottom: 1px solid #E5E5E5; vertical-align: top; }}
  th {{ background: #F3F1EE; font-weight: 700; color: #57534E; font-size: 0.75rem; text-transform: uppercase; letter-spacing: 0.5px; }}
  td:first-child, th:first-child {{ width: 40px; color: #78716C; font-weight: 600; }}
  td.right, th.right {{ text-align: right; white-space: nowrap; }}
  td strong {{ color: #1C1917; font-weight: 600; display: block; }}
  .meta {{ color: #78716C; font-size: 12px; }}
  .cta-box {{ margin-top: 48px; padding: 32px; background: linear-gradient(135deg, rgba(79,70,229,0.06), rgba(79,70,229,0.02)); border: 1px solid rgba(79,70,229,0.15); border-radius: 14px; text-align: center; }}
  .cta-box h2 {{ margin: 0 0 8px; font-size: 1.3rem; font-weight: 700; }}
  .cta-box p {{ color: #6B655F; margin: 0 0 20px; font-size: 0.95rem; }}
  .cta-box a {{ display: inline-block; background: linear-gradient(135deg, #4F46E5 0%, #3730A3 100%); color: #fff; padding: 12px 28px; border-radius: 10px; font-weight: 700; text-decoration: none; }}
  footer {{ margin-top: 48px; padding-top: 24px; border-top: 1px solid #E5E5E5; font-size: 0.85rem; color: #6B655F; text-align: center; }}
  footer a {{ color: #4338ca; text-decoration: underline; text-underline-offset: 3px; }}
  .cat-section {{ margin: 32px 0 8px; }}
  .cat-section h2 {{ font-size: 1.05rem; font-weight: 700; margin: 0 0 12px; color: #1C1917; }}
  .cat-grid {{ display: flex; flex-wrap: wrap; gap: 8px; }}
  .cat-chip {{ display: inline-block; padding: 8px 14px; background: #fff; border: 1px solid #E5E5E5; border-radius: 100px; font-size: 13px; color: #57534E; }}
  .cat-chip strong {{ color: #1C1917; font-weight: 700; margin-right: 4px; }}
</style>
</head>
<body>

<div class="breadcrumb"><a href="./">Home</a> · Digest Sample</div>

<main>
<span class="badge">Live sample · {week}</span>
<h1>What subscribers actually get</h1>
<p class="intro">This is the actual weekly digest generated from our pipeline. {len(products)} newest product launches across 50+ curated DTC Shopify brands, sorted by newest first. No mock, no filler. Auto-updated every Monday.</p>
{cat_chips}
<table>
  <thead><tr>
    <th>#</th>
    <th>Product</th>
    <th class="right">Age</th>
    <th class="right">Price</th>
  </tr></thead>
  <tbody>{table}
  </tbody>
</table>

<div class="cta-box">
  <h2>Want this every Monday?</h2>
  <p>Drop your email on the site — no card, no fluff. Unsubscribe with one click.</p>
  <a href="./?ref=digest-sample#hero">Get the Monday digest →</a>
</div>

<footer>
  Signal source: Shopify /products.json across curated DTC brands, filtered for freshness (30-day window) with vendor and category diversity caps.
  <br><a href="./">← Back to StoreScope</a>
</footer>

</main>
</body>
</html>
"""
    SAMPLE_HTML.parent.mkdir(parents=True, exist_ok=True)
    SAMPLE_HTML.write_text(html_out)
    print(f"  → {SAMPLE_HTML} ({len(html_out)} chars)")


if __name__ == "__main__":
    main()
