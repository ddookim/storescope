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
RSS_FILE        = _HERE / "landing" / "feed.xml"           # D+11: RSS/Atom subscription
BLOG_DIR        = _HERE / "landing" / "blog"                 # D+12: 매주 SEO blog post
BRANDS_DIR      = _HERE / "landing" / "brands"               # D+12: 개별 브랜드 프로필 (SEO surface)

CATEGORY_TOP_N  = 8  # digest 표시 카테고리 개수

TOP_N              = 20     # digest 표시 개수
LOOKBACK_DAYS      = 30     # 30일 이내 published 만 대상 (fresh signal)
MIN_TITLE_LEN      = 8      # placeholder/junk 제외 ("Test", "Sample" 등)
MAX_PER_VENDOR     = 2      # D+11: 편중 방지 (5x 같은 브랜드 방지, UX 이슈)
MAX_PER_TYPE       = 4      # D+11: product_type 편중 방지 (Semi-Permanent 5개 등)


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
    """digest-sample.html + index + weekly blog lastmod → 오늘 날짜.

    Google 은 sitemap lastmod 를 crawl priority signal 로 사용 (freshness).
    매주 pipeline 실행 시 auto-update + weekly blog entry 추가.
    """
    if not SITEMAP_FILE.exists():
        return
    today = now.strftime("%Y-%m-%d")
    week = f"{now.isocalendar().year}-W{now.isocalendar().week:02d}"
    weekly_url = f"https://ddookim.github.io/storescope/blog/weekly-{week}.html"
    text = SITEMAP_FILE.read_text()

    # 1. Root URL entry (index).
    text = re.sub(
        r"(<loc>https://ddookim\.github\.io/storescope/</loc>\s*<lastmod>)[^<]*(</lastmod>)",
        rf"\g<1>{today}\g<2>", text, count=1,
    )
    # 2. digest-sample.html entry.
    text = re.sub(
        r"(<loc>https://ddookim\.github\.io/storescope/digest-sample\.html</loc>\s*<lastmod>)[^<]*(</lastmod>)",
        rf"\g<1>{today}\g<2>", text, count=1,
    )
    # 3. Weekly blog entry — insert before </urlset> if not present.
    if weekly_url not in text:
        new_entry = f"""  <!-- Weekly auto-generated analysis -->
  <url>
    <loc>{weekly_url}</loc>
    <lastmod>{today}</lastmod>
    <changefreq>never</changefreq>
    <priority>0.7</priority>
  </url>
</urlset>"""
        text = text.replace("</urlset>", new_entry)

    # D+14 Red Tactic 3 pivot + 5: bulk-update lastmod for weekly-refreshed programmatic pages.
    # category/*.html + report/*.html + brands/*.html + compare/*.html + brands/ + compare/ + category/ + report/ indexes.
    # 매 pipeline run 시 이 URL 들의 lastmod = today (Google freshness signal).
    _WEEKLY_PREFIXES = (
        "https://ddookim.github.io/storescope/category/",
        "https://ddookim.github.io/storescope/report/",
        "https://ddookim.github.io/storescope/brands/",
    )
    for prefix in _WEEKLY_PREFIXES:
        # Match any URL starting with prefix and update its lastmod.
        pattern = rf"(<loc>{re.escape(prefix)}[^<]*</loc>\s*<lastmod>)[^<]*(</lastmod>)"
        text = re.sub(pattern, rf"\g<1>{today}\g<2>", text)

    SITEMAP_FILE.write_text(text)
    print(f"  → sitemap lastmod = {today} + weekly entry (week={week}) + programmatic refresh")


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
    _write_rss_feed(top, categories, now)
    _write_weekly_blog(top, categories, now)
    _update_blog_index()
    brand_map = _write_brand_profiles(now) or {}
    # D+14 Red Tactic 3 pivot + 5: category & state report weekly auto-regen.
    # brand_map = {vendor_name: brand_slug} for category → brand cross-linking.
    _write_category_pages(top, categories, now, brand_map)
    _write_state_report(top, categories, now)


def _update_blog_index() -> None:
    """blog/index.html 의 WEEKLY_LIST_START/END 마커 사이를 자동 생성 weekly posts 목록으로 갱신.

    최대 8개 최신 weekly-*.html 표시. Each entry links to blog/weekly-YYYY-WNN.html.
    Idempotent: 매번 재계산.
    """
    from html import escape as h
    idx = BLOG_DIR / "index.html"
    if not idx.exists():
        return

    weekly_files = sorted(
        BLOG_DIR.glob("weekly-*.html"),
        key=lambda p: p.name,
        reverse=True,
    )[:8]
    if not weekly_files:
        return

    # Parse title + description from each weekly file (첫 <h1> + first cat_row).
    entries: list[str] = []
    for wf in weekly_files:
        try:
            text = wf.read_text()
        except Exception:
            continue
        # Title: <h1>This Week in DTC · YYYY-WNN</h1>
        m_title = re.search(r"<h1[^>]*>([^<]+)</h1>", text)
        m_meta = re.search(r"Published ([^·]+)·", text)
        title = h((m_title.group(1) if m_title else wf.stem)).strip()[:120]
        date = h((m_meta.group(1) if m_meta else "")).strip()[:20]
        entries.append(
            f'''  <a class="weekly-post" href="./{wf.name}">
    <div class="wp-title">{title}</div>
    <div class="wp-meta"><span class="wp-cat">Weekly analysis</span> · {date}</div>
  </a>'''
        )

    new_block = (
        '<!-- WEEKLY_LIST_START — pipeline STEP 4 auto-updates this block. Do not hand-edit. -->\n'
        '<div class="weekly-block">\n'
        '  <h2 style="margin-bottom:8px;color:#1C1917;font-size:1.1rem">Weekly analyses · auto-updated Monday</h2>\n'
        f'{chr(10).join(entries)}\n'
        '</div>\n'
        '<!-- WEEKLY_LIST_END -->'
    )
    text = idx.read_text()
    text = re.sub(
        r"<!-- WEEKLY_LIST_START[^>]*-->.*?<!-- WEEKLY_LIST_END -->",
        new_block,
        text,
        count=1,
        flags=re.DOTALL,
    )
    idx.write_text(text)
    print(f"  → blog/index.html updated ({len(weekly_files)} weekly entries)")


def _write_brand_profiles(now: datetime) -> None:
    """Individual brand profile pages — 각 curated brand 의 최신 상품 aggregation.

    /brands/{domain-slug}.html 형식. SEO: brand name 이 long-tail keyword
    (e.g., 'Taylor Stitch new arrivals', 'Fenty Beauty latest launches').
    58 brands = 58 indexed URLs. 매주 pipeline 실행 시 auto-regen.

    Data source: data/products/*.json (crawler output, per-store).
    """
    from html import escape as h
    if not PRODUCTS_DIR.exists():
        return
    BRANDS_DIR.mkdir(parents=True, exist_ok=True)
    brand_pages = []
    cutoff = now.timestamp() - LOOKBACK_DAYS * 86400

    # brand index build.
    brand_summary: list[dict] = []
    for f in sorted(PRODUCTS_DIR.glob("*.json")):
        try:
            data = json.loads(f.read_text())
        except json.JSONDecodeError:
            continue
        domain = data.get("domain") or f.stem
        # slug: domain minus TLD variations.
        slug = re.sub(r"[^a-z0-9-]+", "-", domain.lower()).strip("-")[:40]
        products_all = data.get("products", [])
        recent = []
        for p in products_all:
            title = (p.get("title") or "").strip()
            if len(title) < MIN_TITLE_LEN:
                continue
            pub = _parse_iso(p.get("published_at") or "")
            if not pub or pub.timestamp() < cutoff:
                continue
            images = p.get("images") or []
            img_url = images[0].get("src", "") if images else ""
            variants = p.get("variants") or []
            price = None
            if variants:
                try:
                    price = float(variants[0].get("price") or 0)
                except (ValueError, TypeError):
                    price = None
            recent.append({
                "title": title, "handle": p.get("handle",""), "price": price,
                "product_type": p.get("product_type",""), "vendor": p.get("vendor",""),
                "image_url": img_url,
                "age_days": round((now.timestamp() - pub.timestamp()) / 86400, 1),
            })
        if not recent:
            continue
        recent.sort(key=lambda x: x["age_days"])
        recent = recent[:24]  # top 24 recent

        # Vendor & type summary
        from collections import Counter
        vend_counter = Counter(p["vendor"] for p in recent if p.get("vendor"))
        type_counter = Counter(p["product_type"] for p in recent if p.get("product_type"))
        prices = [p["price"] for p in recent if isinstance(p["price"], (int, float)) and p["price"] > 0]
        price_median = sorted(prices)[len(prices)//2] if prices else 0
        price_min = min(prices) if prices else 0
        price_max = max(prices) if prices else 0

        brand_name = vend_counter.most_common(1)[0][0] if vend_counter else domain
        brand_summary.append({"slug": slug, "domain": domain, "name": brand_name,
                             "count": len(recent), "median": price_median})

        cards_html = "".join([
            f'''<a class="pd-card" href="https://{h(domain)}/products/{h(p["handle"])}" target="_blank" rel="noreferrer noopener">
              <img class="pd-card-img" src="{h(p["image_url"])}" alt="{h(p["title"])[:60]}" loading="lazy" decoding="async">
              <div class="pd-card-body">
                <div class="pd-card-title">{h(p["title"])[:60]}</div>
                <div class="pd-card-vendor">{h(p["product_type"])}</div>
                <div class="pd-card-foot">
                  <span class="pd-card-price">${p["price"]:.2f}</span>
                  <span class="pd-card-age">{p["age_days"]}d</span>
                </div>
              </div>
            </a>'''
            for p in recent[:12] if p.get("image_url")
        ])
        top_types = type_counter.most_common(5)
        type_chips = "".join(f'<span class="tag">{h(t)} ({n})</span>' for t, n in top_types)

        html_out = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="description" content="{h(brand_name)} latest product launches — {len(recent)} new items in the last {LOOKBACK_DAYS} days. Price range ${price_min:.0f}-${price_max:.0f}. Auto-updated weekly.">
<meta name="robots" content="index, follow">
<link rel="canonical" href="https://ddookim.github.io/storescope/brands/{slug}.html">
<title>{h(brand_name)} · latest launches — StoreScope</title>
<script type="application/ld+json">
{{
  "@context": "https://schema.org", "@type": "CollectionPage",
  "name": "{h(brand_name)} latest launches",
  "url": "https://ddookim.github.io/storescope/brands/{slug}.html",
  "isPartOf": {{"@type": "WebSite", "name": "StoreScope", "url": "https://ddookim.github.io/storescope/"}},
  "about": {{"@type": "Organization", "name": "{h(brand_name)}", "url": "https://{h(domain)}"}}
}}
</script>
<style>
  body {{ font-family: -apple-system, "Inter", sans-serif; max-width: 900px; margin: 0 auto; padding: 60px 24px; color: #1C1917; line-height: 1.65; background: #F9F8F6; }}
  .breadcrumb {{ font-size: 0.85rem; color: #6B655F; margin-bottom: 24px; }}
  .breadcrumb a {{ color: #4338ca; text-decoration: underline; text-decoration-color: rgba(67,56,202,0.35); text-underline-offset: 3px; }}
  h1 {{ font-size: 2rem; font-weight: 800; letter-spacing: -0.02em; margin-bottom: 0.4rem; }}
  .meta {{ color: #78716C; font-size: 0.9rem; margin-bottom: 20px; }}
  .stats {{ display: flex; gap: 20px; margin: 20px 0 32px; flex-wrap: wrap; }}
  .stat {{ background: #fff; border: 1px solid #E5E5E5; border-radius: 10px; padding: 14px 20px; }}
  .stat-num {{ font-size: 1.4rem; font-weight: 800; color: #1C1917; }}
  .stat-label {{ font-size: 0.8rem; color: #78716C; text-transform: uppercase; letter-spacing: 0.5px; }}
  .tag {{ display: inline-block; padding: 6px 12px; background: #fff; border: 1px solid #E5E5E5; border-radius: 100px; font-size: 12.5px; color: #57534E; margin: 0 6px 6px 0; }}
  .pd-grid {{ display: grid; grid-template-columns: repeat(auto-fill, minmax(160px, 1fr)); gap: 12px; margin: 20px 0 32px; }}
  .pd-card {{ background: #fff; border: 1px solid #E5E5E5; border-radius: 12px; overflow: hidden; transition: transform 0.15s ease, box-shadow 0.15s ease; text-decoration: none; color: inherit; display: flex; flex-direction: column; }}
  .pd-card:hover {{ transform: translateY(-2px); box-shadow: 0 8px 20px rgba(0,0,0,0.06); border-color: rgba(79,70,229,0.35); }}
  .pd-card-img {{ width: 100%; aspect-ratio: 1/1; object-fit: cover; background: #F3F1EE; display: block; }}
  .pd-card-body {{ padding: 10px 12px 12px; flex: 1; display: flex; flex-direction: column; gap: 4px; }}
  .pd-card-title {{ font-size: 13px; font-weight: 700; color: #1C1917; line-height: 1.3; display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; overflow: hidden; }}
  .pd-card-vendor {{ font-size: 11px; color: #78716C; }}
  .pd-card-foot {{ display: flex; justify-content: space-between; margin-top: auto; padding-top: 4px; font-size: 12px; }}
  .pd-card-price {{ font-weight: 700; color: #1C1917; }}
  .pd-card-age {{ color: #78716C; font-size: 11px; padding: 2px 8px; background: #F3F1EE; border-radius: 100px; font-weight: 600; }}
  footer {{ margin-top: 48px; padding-top: 24px; border-top: 1px solid #E5E5E5; font-size: 0.85rem; color: #6B655F; }}
  footer a {{ color: #4338ca; text-decoration: underline; }}
</style>
</head>
<body>
{_TOP_NAV}<div class="breadcrumb"><a href="../">Home</a> · <a href="./">Brands</a> · {h(brand_name)}</div>
<main>
<h1>{h(brand_name)}</h1>
<p class="meta">Latest launches from <a href="https://{h(domain)}" target="_blank" rel="noreferrer noopener">{h(domain)}</a> · updated {now.strftime('%B %d, %Y')}</p>
<div class="stats">
  <div class="stat"><div class="stat-num">{len(recent)}</div><div class="stat-label">New items · {LOOKBACK_DAYS}d</div></div>
  <div class="stat"><div class="stat-num">${price_median:.0f}</div><div class="stat-label">Median price</div></div>
  <div class="stat"><div class="stat-num">${price_min:.0f}–${price_max:.0f}</div><div class="stat-label">Price range</div></div>
  <div class="stat"><div class="stat-num">{len(type_counter)}</div><div class="stat-label">Product types</div></div>
</div>
<div>{type_chips}</div>
<h2 style="margin-top:32px">Latest launches</h2>
<div class="pd-grid">{cards_html}</div>
<footer>
  Data: Shopify /products.json crawled weekly. This page is auto-generated.
  <br><a href="../">← All StoreScope</a> · <a href="../digest-sample.html">← This week's digest</a>
</footer>
</main>
</body>
</html>
"""
        (BRANDS_DIR / f"{slug}.html").write_text(html_out)
        brand_pages.append(slug)

    print(f"  → {len(brand_pages)} brand profile pages")

    # brand index page (list of all brands)
    if brand_summary:
        brand_summary.sort(key=lambda b: -b["count"])
        list_items = "\n".join(
            f'<a class="brand-card" href="./{b["slug"]}.html"><div class="brand-name">{h(b["name"])}</div><div class="brand-meta">{b["count"]} new · ${b["median"]:.0f} median</div></a>'
            for b in brand_summary
        )
        idx_html = f"""<!DOCTYPE html>
<html lang="en"><head>
<meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="description" content="Curated DTC brand index — {len(brand_summary)} Shopify brands tracked weekly. Latest launches, prices, category summaries.">
<meta name="robots" content="index, follow">
<link rel="canonical" href="https://ddookim.github.io/storescope/brands/">
<title>DTC brands index — StoreScope</title>
<style>
body{{font-family:-apple-system,"Inter",sans-serif;max-width:900px;margin:0 auto;padding:60px 24px;color:#1C1917;background:#F9F8F6}}
h1{{font-size:2rem;font-weight:800;margin-bottom:.6rem}}
.subtitle{{color:#57534E;margin-bottom:32px}}
.grid{{display:grid;grid-template-columns:repeat(auto-fill,minmax(200px,1fr));gap:12px}}
.brand-card{{background:#fff;border:1px solid #E5E5E5;border-radius:10px;padding:14px 18px;text-decoration:none;color:inherit;transition:border-color .15s ease}}
.brand-card:hover{{border-color:rgba(79,70,229,.4)}}
.brand-name{{font-weight:700;color:#1C1917;margin-bottom:4px}}
.brand-meta{{font-size:12.5px;color:#78716C}}
.breadcrumb{{font-size:.85rem;color:#6B655F;margin-bottom:24px}}
.breadcrumb a{{color:#4338ca;text-decoration:underline}}
</style>
</head><body>
{_TOP_NAV}<div class="breadcrumb"><a href="../">Home</a> · Brands</div>
<main>
<h1>Curated DTC brand index</h1>
<p class="subtitle">{len(brand_summary)} Shopify brands tracked weekly · sorted by recent activity</p>
<div class="grid">{list_items}</div>
</main>
</body></html>
"""
        (BRANDS_DIR / "index.html").write_text(idx_html)
        print(f"  → brands/index.html ({len(brand_summary)} brands)")

    # D+14: return vendor_name → brand_slug map for cross-linking (category pages 등).
    return {b["name"]: b["slug"] for b in brand_summary}


def _write_weekly_blog(products: list[dict], categories: list[dict], now: datetime) -> None:
    """Auto-generate landing/blog/weekly-YYYY-WNN.html — SEO compound content.

    매주 실행 시 새 blog post 생성 → Google 크롤 → indexed URL 증가 →
    long-tail keyword 유입 (e.g. "new DTC clothing launches August").
    Distribution 채널 없이도 organic search 유입 가능.
    """
    from html import escape as h
    week = f"{now.isocalendar().year}-W{now.isocalendar().week:02d}"
    filename = f"weekly-{week}.html"
    fpath = BLOG_DIR / filename

    # Skip 중복 (같은 주 이미 생성된 경우).
    if fpath.exists():
        print(f"  → blog skip (already exists): {filename}")
        return

    # Insights: top category + vendor + price range
    if not categories or not products:
        print(f"  → blog skip (no content): categories={len(categories)}, products={len(products)}")
        return

    top_cat = categories[0]
    prices = [p.get("price") for p in products if isinstance(p.get("price"), (int, float)) and p.get("price") > 0]
    med_price = sorted(prices)[len(prices)//2] if prices else 0
    max_price = max(prices) if prices else 0
    min_price = min(prices) if prices else 0

    # Category chips + product rows.
    cat_rows = "".join(
        f"<li><strong>{h(c.get('product_type') or '')}</strong> — {c.get('product_count', 0)} new items across {c.get('vendor_count', 0)} vendor(s)</li>"
        for c in categories
    )
    product_rows = "".join(
        f"""      <tr>
        <td>{i}</td>
        <td><strong>{h(p.get('title') or 'Untitled')[:80]}</strong><br><span style="color:#78716C;font-size:12px">{h(p.get('vendor') or '—')} · {h(p.get('product_type') or '')}</span></td>
        <td style="text-align:right">${p.get('price', 0):.2f}</td>
        <td style="text-align:right">{p.get('age_days', '?')}d</td>
      </tr>"""
        for i, p in enumerate(products[:10], 1)
        if isinstance(p.get("price"), (int, float))
    )
    date_str = now.strftime("%Y-%m-%d")
    date_pretty = now.strftime("%B %d, %Y")

    html_out = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="description" content="This Week in DTC Shopify launches — Week {week}. Top category: {h(top_cat['product_type'])} ({top_cat['product_count']} new items). {len(products)} products indexed from 50+ curated DTC brands. Price range ${min_price:.0f}-${max_price:.0f}, median ${med_price:.2f}.">
<meta name="robots" content="index, follow">
<link rel="canonical" href="https://ddookim.github.io/storescope/blog/{filename}">
<title>This Week in DTC · {week} — {h(top_cat['product_type'])} Lead | StoreScope</title>

<!-- OG + Article schema for SEO -->
<meta property="og:title" content="This Week in DTC · {week} — StoreScope" />
<meta property="og:description" content="Top category: {h(top_cat['product_type'])}. {len(products)} new DTC brand launches. Fashion, beauty, home." />
<meta property="og:type" content="article" />
<meta property="og:url" content="https://ddookim.github.io/storescope/blog/{filename}" />
<meta name="twitter:card" content="summary_large_image" />
<meta name="twitter:title" content="This Week in DTC · {week}" />
<meta name="twitter:description" content="{h(top_cat['product_type'])} lead with {top_cat['product_count']} new items. {len(products)} total." />
<script type="application/ld+json">
{{
  "@context": "https://schema.org",
  "@type": "Article",
  "headline": "This Week in DTC · {week} — {h(top_cat['product_type'])} Lead",
  "description": "Weekly analysis of DTC Shopify brand product launches. Week {week}.",
  "url": "https://ddookim.github.io/storescope/blog/{filename}",
  "datePublished": "{date_str}",
  "dateModified": "{date_str}",
  "author": {{"@type": "Organization", "name": "StoreScope"}},
  "publisher": {{
    "@type": "Organization", "name": "StoreScope",
    "logo": {{"@type": "ImageObject", "url": "https://ddookim.github.io/storescope/landing/og-image.png"}}
  }},
  "mainEntityOfPage": {{"@type": "WebPage", "@id": "https://ddookim.github.io/storescope/blog/{filename}"}}
}}
</script>

<style>
  body {{ font-family: -apple-system, "Inter", sans-serif; max-width: 720px; margin: 0 auto; padding: 60px 24px; color: #1C1917; line-height: 1.65; background: #F9F8F6; }}
  .breadcrumb {{ font-size: 0.85rem; color: #6B655F; margin-bottom: 24px; }}
  .breadcrumb a {{ color: #4338ca; text-decoration: underline; text-decoration-color: rgba(67,56,202,0.35); text-underline-offset: 3px; }}
  h1 {{ font-size: 2rem; font-weight: 800; letter-spacing: -0.02em; margin-bottom: 0.4rem; line-height: 1.15; }}
  .meta {{ color: #78716C; font-size: 0.9rem; margin-bottom: 24px; }}
  h2 {{ font-size: 1.3rem; font-weight: 700; margin-top: 32px; }}
  .lede {{ font-size: 1.05rem; color: #3F3B37; margin-bottom: 24px; }}
  ul {{ padding-left: 20px; }}
  ul li {{ margin-bottom: 8px; }}
  table {{ width: 100%; border-collapse: collapse; margin: 20px 0; font-size: 14px; background: #fff; border-radius: 10px; overflow: hidden; }}
  th, td {{ text-align: left; padding: 12px 14px; border-bottom: 1px solid #E5E5E5; vertical-align: top; }}
  th {{ background: #F3F1EE; font-size: 0.75rem; text-transform: uppercase; letter-spacing: 0.5px; color: #57534E; }}
  td:first-child, th:first-child {{ width: 30px; color: #78716C; }}
  .cta-box {{ margin-top: 40px; padding: 28px; background: linear-gradient(135deg, rgba(79,70,229,0.06), rgba(79,70,229,0.02)); border: 1px solid rgba(79,70,229,0.15); border-radius: 12px; text-align: center; }}
  .cta-box a {{ display: inline-block; background: linear-gradient(135deg, #4F46E5 0%, #3730A3 100%); color: #fff; padding: 10px 22px; border-radius: 8px; font-weight: 700; text-decoration: none; }}
  footer {{ margin-top: 40px; padding-top: 20px; border-top: 1px solid #E5E5E5; font-size: 0.85rem; color: #6B655F; }}
  footer a {{ color: #4338ca; text-decoration: underline; text-underline-offset: 3px; }}
</style>
</head>
<body>

{_TOP_NAV}<div class="breadcrumb"><a href="../">Home</a> · <a href="./">Blog</a> · Week {week}</div>

<main>
<h1>This Week in DTC · {week}</h1>
<p class="meta">Published {date_pretty} · Auto-generated from 50+ curated DTC Shopify brands</p>

<p class="lede"><strong>{h(top_cat['product_type'])}</strong> lead this week with {top_cat['product_count']} new launches across {top_cat['vendor_count']} vendor(s). {len(products)} total new products, price range <strong>${min_price:.0f}–${max_price:.0f}</strong> (median <strong>${med_price:.2f}</strong>).</p>

<h2>Hot categories · last 30 days</h2>
<ul>{cat_rows}</ul>

<h2>Top 10 latest launches</h2>
<table>
  <thead><tr>
    <th>#</th>
    <th>Product</th>
    <th style="text-align:right">Price</th>
    <th style="text-align:right">Age</th>
  </tr></thead>
  <tbody>{product_rows}</tbody>
</table>

<div class="cta-box">
  <p><strong>Get this every Monday.</strong><br>Real DTC brand launches, updated weekly. No fluff.</p>
  <a href="../?ref=weekly-{week}#hero">Subscribe →</a>
</div>

<footer>
  Signal source: Shopify /products.json across curated DTC brands, 30-day published window, vendor + category diversity capped.
  <br><a href="./">← All weekly analyses</a>
</footer>
</main>
</body>
</html>
"""
    BLOG_DIR.mkdir(parents=True, exist_ok=True)
    fpath.write_text(html_out)
    print(f"  → {fpath} ({len(html_out)} chars)")


def _write_rss_feed(products: list[dict], categories: list[dict], now: datetime) -> None:
    """RSS 2.0 feed at landing/feed.xml — email 없이 subscription 채널.

    Reader (Feedly/Inoreader/etc) → 매주 새 top 20 items push.
    Also SEO signal (Google 크롤러 는 feed 를 freshness 근거로 사용).
    """
    from html import escape as h
    from email.utils import format_datetime  # RFC 822 date format
    week = f"{now.isocalendar().year}-W{now.isocalendar().week:02d}"
    week_pub = format_datetime(now)

    items_xml = []
    for i, p in enumerate(products[:20], 1):
        title = h(p.get("title") or "Untitled")[:120]
        vendor = h(p.get("vendor") or "")
        ptype = h(p.get("product_type") or "")
        price = p.get("price")
        price_str = f"${price:.2f}" if isinstance(price, (int, float)) and price > 0 else "—"
        age = p.get("age_days", "?")
        pub = p.get("published_at", "")
        # Parse published_at → RFC 822.
        try:
            dt = datetime.fromisoformat(pub) if pub else now
            pub_rfc = format_datetime(dt)
        except Exception:
            pub_rfc = week_pub
        # unique GUID = domain + handle (stable across weeks).
        guid = h(f"{p.get('domain','')}#{p.get('handle','')}"[:100])
        item_link = f"https://ddookim.github.io/storescope/digest-sample.html#p{i}"
        items_xml.append(f"""
    <item>
      <title>{title}</title>
      <link>{item_link}</link>
      <guid isPermaLink="false">{guid}</guid>
      <pubDate>{pub_rfc}</pubDate>
      <description>{vendor} · {ptype} · {price_str} · {age}d old</description>
      <category>{ptype}</category>
    </item>""")

    cat_summary = ", ".join(
        f"{c['product_type']} ({c['product_count']})"
        for c in categories[:5]
    )

    rss = f"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0" xmlns:atom="http://www.w3.org/2005/Atom">
  <channel>
    <title>StoreScope Weekly · {week}</title>
    <link>https://ddookim.github.io/storescope/digest-sample.html</link>
    <atom:link href="https://ddookim.github.io/storescope/feed.xml" rel="self" type="application/rss+xml"/>
    <description>Newest DTC brand product launches from 50+ curated Shopify brands. Updated every Monday. Hot: {cat_summary}.</description>
    <language>en-us</language>
    <lastBuildDate>{week_pub}</lastBuildDate>
    <pubDate>{week_pub}</pubDate>
    <ttl>10080</ttl>
    <generator>StoreScope pipeline/latest_products.py</generator>{"".join(items_xml)}
  </channel>
</rss>
"""
    RSS_FILE.parent.mkdir(parents=True, exist_ok=True)
    RSS_FILE.write_text(rss)
    print(f"  → {RSS_FILE} ({len(products)} items)")


def _write_sample_html(products: list[dict], categories: list[dict], now: datetime) -> None:
    """Regenerate landing/digest-sample.html — SEO + demo asset (D+11).

    사용자가 landing 방문 시 '실제로 받는 digest 는 뭐?' 궁금증 해결.
    Auto-updated every pipeline run (Monday 08:00 KST).
    """
    from html import escape as h
    week = f"{now.isocalendar().year}-W{now.isocalendar().week:02d}"

    # D+12 grid cards — 실 Shopify product page 로 링크 (competitive parity+).
    cards = []
    for p in products[:12]:
        title = h(p.get("title") or "Untitled")[:80]
        vendor = h(p.get("vendor") or "—")
        price = p.get("price")
        price_str = f"${price:.2f}" if isinstance(price, (int, float)) and price > 0 else "—"
        age = p.get("age_days", "?")
        img_url = h(p.get("image_url") or "")
        cat_slug = re.sub(r'[^a-z0-9]+', '-', (p.get('product_type') or 'other').lower()).strip('-')[:30] or 'other'
        # 실 Shopify product URL — {domain}/products/{handle} 표준.
        domain = h(p.get("domain") or "")
        handle = h(p.get("handle") or "")
        product_url = f"https://{domain}/products/{handle}" if domain and handle else "#"
        img_html = (
            f'<img class="pd-card-img" src="{img_url}" alt="{title}" loading="lazy" decoding="async" onerror="this.style.background=\'#F3F1EE\';this.style.opacity=\'0\'">'
            if img_url else '<div class="pd-card-img"></div>'
        )
        # target=_blank + rel=noreferrer noopener = privacy + 우리 페이지 유지.
        cards.append(f'''
      <a class="pd-card" data-cat="{cat_slug}" href="{product_url}" target="_blank" rel="noreferrer noopener">
        {img_html}
        <div class="pd-card-body">
          <div class="pd-card-title">{title}</div>
          <div class="pd-card-vendor">{vendor}</div>
          <div class="pd-card-foot">
            <span class="pd-card-price">{price_str}</span>
            <span class="pd-card-age">{age}d</span>
          </div>
        </div>
      </a>''')
    grid_html = f'''<div class="pd-section-label">Latest launches · visual (click to view on brand's site)</div>
<div class="pd-grid">{"".join(cards)}</div>''' if cards else ''

    # D+12 (경쟁사 대비 upgrade): product image thumbnails + 실 Shopify page 링크.
    rows = []
    for i, p in enumerate(products[:20], 1):
        title = h(p.get("title") or "Untitled")[:100]
        vendor = h(p.get("vendor") or "—")
        ptype = h(p.get("product_type") or "")
        price = p.get("price")
        price_str = f"${price:.2f}" if isinstance(price, (int, float)) and price > 0 else "—"
        age = p.get("age_days", "?")
        img_url = h(p.get("image_url") or "")
        domain = h(p.get("domain") or "")
        handle = h(p.get("handle") or "")
        product_url = f"https://{domain}/products/{handle}" if domain and handle else ""
        # loading=lazy + async decode: perf; onerror hides broken images gracefully.
        img_html = (
            f'<img src="{img_url}" alt="{title}" loading="lazy" decoding="async" '
            f'class="pd-thumb" onerror="this.style.display=\'none\'">'
            if img_url else '<div class="pd-thumb-placeholder">—</div>'
        )
        # Product title 도 실 브랜드 페이지로 링크 (external, target=_blank).
        title_html = (
            f'<a href="{product_url}" target="_blank" rel="noreferrer noopener" style="color:#1C1917;text-decoration:none;border-bottom:1px dotted #A29E98">{title}</a>'
            if product_url else title
        )
        cat_slug = re.sub(r'[^a-z0-9]+', '-', (p.get('product_type') or 'other').lower()).strip('-')[:30] or 'other'
        rows.append(f"""
      <tr class="pd-row" data-cat="{cat_slug}">
        <td class="pd-idx">{i}</td>
        <td class="pd-thumb-cell">{img_html}</td>
        <td class="pd-info"><strong>{title_html}</strong><br><span class="meta">{vendor} · {ptype}</span></td>
        <td class="right">{age}d</td>
        <td class="right">{price_str}</td>
      </tr>""")
    table = "".join(rows) or '<tr><td colspan=5 style="padding:24px;text-align:center;color:#78716C">No qualifying products this week. Digest skipped.</td></tr>'

    # D+12 category filter tabs — CSS-only via <details> or JS via tabbed data-cat filter.
    # data-cat slug 로 filter 하는 minimal JS (5 line).
    filter_tabs = ""
    if categories and products:
        tabs = ['<button class="pd-tab pd-tab-active" data-cat="all">All ({total})</button>'.format(total=len(products[:20]))]
        for c in categories[:6]:
            slug = re.sub(r'[^a-z0-9]+', '-', (c.get('product_type') or 'other').lower()).strip('-')[:30] or 'other'
            tabs.append(f'<button class="pd-tab" data-cat="{slug}">{h(c.get("product_type") or "?")[:30]}</button>')
        filter_tabs = f'''
<div class="pd-filter">
  <span class="pd-filter-label">Filter:</span>
  <div class="pd-tabs">{"".join(tabs)}</div>
</div>'''

    # D+12 CSV export — data URL 방식 (no server, 순수 client).
    csv_lines = ["Rank,Product,Vendor,Product Type,Price,Age (days),Image URL"]
    for i, p in enumerate(products[:20], 1):
        t = (p.get("title") or "").replace('"','""')
        v = (p.get("vendor") or "").replace('"','""')
        pt = (p.get("product_type") or "").replace('"','""')
        pr = p.get("price") or ""
        a = p.get("age_days","")
        iu = (p.get("image_url") or "").replace('"','""')
        csv_lines.append(f'{i},"{t}","{v}","{pt}",{pr},{a},"{iu}"')
    import base64
    csv_b64 = base64.b64encode('\n'.join(csv_lines).encode()).decode()
    csv_link = f'<a href="data:text/csv;base64,{csv_b64}" download="storescope-week-{week}.csv" class="pd-export">↓ Export CSV</a>'

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
  /* D+12 경쟁사 대비 upgrade — visual grid ABOVE table (modern ecom research tool 표준) */
  .pd-grid {{ display: grid; grid-template-columns: repeat(auto-fill, minmax(160px, 1fr)); gap: 12px; margin: 20px 0 32px; }}
  .pd-card {{ background: #fff; border: 1px solid #E5E5E5; border-radius: 12px; overflow: hidden; transition: transform 0.15s ease, box-shadow 0.15s ease, border-color 0.15s ease; text-decoration: none; color: inherit; display: flex; flex-direction: column; }}
  .pd-card:hover {{ transform: translateY(-2px); box-shadow: 0 8px 20px rgba(0,0,0,0.06); border-color: rgba(79,70,229,0.35); }}
  .pd-card-img {{ width: 100%; aspect-ratio: 1 / 1; object-fit: cover; background: #F3F1EE; display: block; }}
  .pd-card-body {{ padding: 10px 12px 12px; flex: 1; display: flex; flex-direction: column; gap: 4px; }}
  .pd-card-title {{ font-size: 13px; font-weight: 700; color: #1C1917; line-height: 1.3; display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; overflow: hidden; }}
  .pd-card-vendor {{ font-size: 11px; color: #78716C; }}
  .pd-card-foot {{ display: flex; justify-content: space-between; align-items: center; margin-top: auto; padding-top: 4px; font-size: 12px; }}
  .pd-card-price {{ font-weight: 700; color: #1C1917; }}
  .pd-card-age {{ color: #78716C; font-size: 11px; padding: 2px 8px; background: #F3F1EE; border-radius: 100px; font-weight: 600; }}
  /* Table view — 축소된 보조 정보 view */
  .pd-thumb {{ width: 56px; height: 56px; border-radius: 8px; object-fit: cover; background: #F3F1EE; display: block; }}
  .pd-thumb-placeholder {{ width: 56px; height: 56px; border-radius: 8px; background: #F3F1EE; display: flex; align-items: center; justify-content: center; color: #A29E98; font-size: 20px; }}
  .pd-thumb-cell {{ width: 72px; padding: 10px 6px 10px 14px; }}
  .pd-info strong {{ display: block; margin-bottom: 3px; color: #1C1917; }}
  .pd-idx {{ color: #78716C; font-weight: 600; width: 32px; }}
  .pd-filter {{ display: flex; align-items: center; gap: 12px; margin: 24px 0 12px; flex-wrap: wrap; }}
  .pd-filter-label {{ font-size: 13px; font-weight: 600; color: #57534E; text-transform: uppercase; letter-spacing: 0.5px; }}
  .pd-tabs {{ display: flex; gap: 6px; flex-wrap: wrap; }}
  .pd-tab {{ background: #fff; border: 1px solid #E5E5E5; color: #57534E; padding: 6px 14px; border-radius: 100px; font-size: 12.5px; font-weight: 600; cursor: pointer; transition: all 0.15s ease; }}
  .pd-tab:hover {{ border-color: #4338ca; color: #4338ca; }}
  .pd-tab-active {{ background: #4338ca; border-color: #4338ca; color: #fff; }}
  .pd-export {{ margin-left: auto; display: inline-flex; align-items: center; gap: 6px; padding: 6px 14px; background: #F3F1EE; border: 1px solid #E5E5E5; border-radius: 100px; font-size: 12.5px; font-weight: 600; color: #57534E; text-decoration: none; }}
  .pd-export:hover {{ background: #E5E5E5; }}
  .pd-row {{ transition: opacity 0.15s ease; }}
  .pd-row.pd-hidden, .pd-card.pd-hidden {{ display: none; }}
  .pd-section-label {{ font-size: 0.85rem; font-weight: 700; text-transform: uppercase; letter-spacing: 0.5px; color: #78716C; margin: 32px 0 8px; }}
</style>
</head>
<body>

{_TOP_NAV}<div class="breadcrumb"><a href="./">Home</a> · Digest Sample</div>

<main>
<span class="badge">Live sample · {week}</span>
<h1>What subscribers actually get</h1>
<p class="intro">This is the actual weekly digest generated from our pipeline. {len(products)} newest product launches across 50+ curated DTC Shopify brands, sorted by newest first. No mock, no filler. Auto-updated every Monday.</p>
{cat_chips}
{filter_tabs}

{grid_html}

<div class="pd-section-label">Full list · table view</div>
<table>
  <thead><tr>
    <th>#</th>
    <th></th>
    <th>Product</th>
    <th class="right">Age</th>
    <th class="right">Price</th>
  </tr></thead>
  <tbody>{table}
  </tbody>
</table>

<div style="text-align:right;margin:8px 0 24px">{csv_link}</div>

<script>
  // D+12 minimal filter (no library, ~10 lines)
  document.querySelectorAll('.pd-tab').forEach(function(btn) {{
    btn.addEventListener('click', function() {{
      var cat = btn.getAttribute('data-cat');
      document.querySelectorAll('.pd-tab').forEach(function(b) {{ b.classList.remove('pd-tab-active'); }});
      btn.classList.add('pd-tab-active');
      document.querySelectorAll('.pd-row, .pd-card').forEach(function(el) {{
        var match = cat === 'all' || el.getAttribute('data-cat') === cat;
        el.classList.toggle('pd-hidden', !match);
      }});
    }});
  }});
</script>

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


# ─────────────────────────────────────────────────────────────────
# D+14 Red Tactic 3 pivot: /category/*.html + hub weekly regen
# D+14 Red Tactic 5: /report/state-of-dtc-shopify-latest.html weekly regen
# ─────────────────────────────────────────────────────────────────

_CATEGORY_DIR = _HERE / "landing" / "category"
_REPORT_DIR   = _HERE / "landing" / "report"

_SLUG_MAP = {
    "Dresses":    "dresses",
    "Sweaters":   "sweaters",
    "Bottoms":    "bottoms",
    "Knit Tops":  "knit-tops",
    "Denim":      "denim",
    "Woven Tops": "woven-tops",
    "Outerwear":  "outerwear",
}

_SEO_CSS = """
  body { font-family: -apple-system, "Inter", sans-serif; max-width: 900px; margin: 0 auto; padding: 60px 24px; color: #1C1917; line-height: 1.65; background: #F9F8F6; }
  .breadcrumb { font-size: 0.85rem; color: #6B655F; margin-bottom: 24px; }
  .breadcrumb a { color: #4338ca; text-decoration: underline; text-underline-offset: 3px; }
  h1 { font-size: 2rem; font-weight: 800; letter-spacing: -0.02em; margin-bottom: 0.4rem; line-height: 1.2; }
  h2 { font-size: 1.3rem; font-weight: 700; margin: 36px 0 12px; }
  h3 { font-size: 1.05rem; font-weight: 700; margin: 20px 0 8px; color: #292524; }
  p { color: #3F3B37; margin-bottom: 1rem; }
  .meta { color: #78716C; font-size: 0.9rem; margin-bottom: 20px; }
  .stats { display: grid; grid-template-columns: repeat(auto-fit, minmax(140px, 1fr)); gap: 12px; margin: 20px 0 28px; }
  .stat { background: #fff; border: 1px solid #E5E5E5; border-radius: 12px; padding: 14px 18px; }
  .stat-num { font-size: 1.5rem; font-weight: 800; color: #1C1917; line-height: 1; }
  .stat-label { font-size: 0.78rem; color: #78716C; text-transform: uppercase; letter-spacing: 0.5px; margin-top: 4px; }
  .tag { display: inline-block; padding: 6px 12px; background: #fff; border: 1px solid #E5E5E5; border-radius: 100px; font-size: 12.5px; color: #57534E; margin: 0 6px 6px 0; text-decoration: none; }
  .tag:hover { border-color: rgba(79,70,229,0.5); }
  .pd-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(180px, 1fr)); gap: 14px; margin: 16px 0 32px; }
  .pd-card { background: #fff; border: 1px solid #E5E5E5; border-radius: 12px; overflow: hidden; text-decoration: none; color: inherit; display: flex; flex-direction: column; transition: transform 0.15s, box-shadow 0.15s; }
  .pd-card:hover { transform: translateY(-2px); box-shadow: 0 8px 20px rgba(0,0,0,0.06); border-color: rgba(79,70,229,0.35); }
  .pd-card-img { width: 100%; aspect-ratio: 1/1; object-fit: cover; background: #F3F1EE; display: block; }
  .pd-card-body { padding: 12px 14px; flex: 1; display: flex; flex-direction: column; gap: 4px; }
  .pd-card-title { font-size: 13.5px; font-weight: 700; line-height: 1.3; display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; overflow: hidden; }
  .pd-card-vendor { font-size: 11.5px; color: #78716C; }
  .pd-card-foot { display: flex; justify-content: space-between; margin-top: auto; padding-top: 6px; font-size: 12.5px; }
  .pd-card-price { font-weight: 700; }
  .pd-card-age { color: #78716C; font-size: 11px; padding: 2px 8px; background: #F3F1EE; border-radius: 100px; font-weight: 600; }
  table { width: 100%; border-collapse: collapse; margin: 16px 0 28px; font-size: 0.94rem; }
  th, td { text-align: left; padding: 10px 12px; border-bottom: 1px solid #E7E5E4; }
  th { background: #F3F1EE; font-weight: 700; }
  th:last-child, td:last-child { text-align: right; }
  code { background: #F3F1EE; padding: 2px 6px; border-radius: 4px; font-family: "SF Mono", monospace; font-size: 0.9em; color: #4338ca; }
  .cite-box { background: #F3F1EE; border-left: 3px solid #4F46E5; padding: 14px 18px; border-radius: 6px; margin: 20px 0; }
  .cite-box code { display: block; padding: 8px 12px; background: #fff; margin: 8px 0; word-break: break-all; }
  .cta { display: inline-block; background: linear-gradient(135deg, #4F46E5 0%, #3730A3 100%); color: #fff; padding: 14px 28px; border-radius: 10px; font-weight: 700; text-decoration: none; margin: 12px 0; }
  .cta:hover { transform: translateY(-1px); box-shadow: 0 8px 24px rgba(79,70,229,0.25); }
  .related { margin-top: 28px; padding-top: 20px; border-top: 1px solid #E5E5E5; }
  footer { margin-top: 40px; padding-top: 20px; border-top: 1px solid #E5E5E5; font-size: 0.85rem; color: #6B655F; }
  footer a { color: #4338ca; text-decoration: underline; }
"""

_FAVICON = "data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 32 32'%3E%3Crect width='32' height='32' rx='7' fill='%234F46E5'/%3E%3Cpath d='M9 20l5-5 4 4 5-6' stroke='%23fff' stroke-width='2.6' stroke-linecap='round' stroke-linejoin='round' fill='none'/%3E%3C/svg%3E"

_BASE = "https://ddookim.github.io/storescope"

# D+14 site-wide top nav — inserted after <body> in every generated SEO page.
# Cross-navigation UX (Home/Category/Compare/Reports/Brands/Blog) + SEO internal-link equity.
_TOP_NAV = '''<nav class="ss-topnav" aria-label="Site sections" style="max-width:900px;margin:0 auto;padding:14px 24px 0;font-size:13px;color:#6B655F;line-height:1.6;">
  <a href="/storescope/" style="color:#4338ca;text-decoration:none;font-weight:600;">← StoreScope</a>
  <span style="margin:0 6px;color:#D6D3D1;">·</span>
  <a href="/storescope/category/" style="color:#4338ca;text-decoration:none;">Categories</a>
  <span style="margin:0 6px;color:#D6D3D1;">·</span>
  <a href="/storescope/compare/" style="color:#4338ca;text-decoration:none;">Compare</a>
  <span style="margin:0 6px;color:#D6D3D1;">·</span>
  <a href="/storescope/report/" style="color:#4338ca;text-decoration:none;">Reports</a>
  <span style="margin:0 6px;color:#D6D3D1;">·</span>
  <a href="/storescope/brands/" style="color:#4338ca;text-decoration:none;">Brands</a>
  <span style="margin:0 6px;color:#D6D3D1;">·</span>
  <a href="/storescope/blog/" style="color:#4338ca;text-decoration:none;">Blog</a>
</nav>
'''


def _card_html(p: dict) -> str:
    from html import escape as h
    domain = p.get("domain", "")
    handle = p.get("handle", "")
    href = f"https://{h(domain)}/products/{h(handle)}" if domain and handle else "#"
    img = h(p.get("image_url", ""))
    title = h(p.get("title", ""))[:70]
    vendor = h(p.get("vendor", ""))[:40]
    price = p.get("price")
    price_disp = f"${price:.0f}" if isinstance(price, (int, float)) and price else ""
    age = p.get("age_days")
    age_disp = f"{age}d" if isinstance(age, (int, float)) else ""
    img_html = f'<img class="pd-card-img" src="{img}" alt="{title}" loading="lazy" decoding="async">' if img else '<div class="pd-card-img"></div>'
    return f"""<a class="pd-card" href="{href}" target="_blank" rel="noreferrer noopener">
{img_html}
<div class="pd-card-body">
<div class="pd-card-title">{title}</div>
<div class="pd-card-vendor">{vendor}</div>
<div class="pd-card-foot"><span class="pd-card-price">{price_disp}</span><span class="pd-card-age">{age_disp}</span></div>
</div>
</a>"""


def _write_category_pages(products: list[dict], categories: list[dict], now: datetime, brand_map: dict[str, str] | None = None) -> None:
    """7 programmatic category pages (Uncategorized 제외) + hub.

    Weekly refresh on pipeline run — real data (category counts, top vendors,
    sample products from top-20 digest).

    brand_map: {vendor_name → brand_slug} from _write_brand_profiles.
    Used to cross-link top_vendors → /brands/{slug}.html for SEO link equity.
    Fallback to plain span if vendor has no brand page (crawl미검출/신규).
    """
    from html import escape as h
    # D+14 empty guard: crawl 데이터 stale/empty 시 기존 페이지 empty overwrite 금지.
    # 이전 사고: dev 환경에서 stale data/products/* 로 pipeline 실행 → 0 categories →
    # 기존 카테고리 페이지 empty overwrite → live SEO 자산 파괴. 발생 재현 차단.
    if not products or not categories:
        print(f"  → category pages SKIP (empty products/categories — stale crawl?)")
        return
    brand_map = brand_map or {}
    _CATEGORY_DIR.mkdir(parents=True, exist_ok=True)

    named_cats = [c for c in categories if c["product_type"] in _SLUG_MAP]
    week = f"{now.isocalendar().year}-W{now.isocalendar().week:02d}"
    today = now.strftime("%Y-%m-%d")
    total = len(products)

    def _vendor_tag_html(vendor: str) -> str:
        slug = brand_map.get(vendor)
        if slug:
            return f'<a class="tag" href="../brands/{slug}.html">{h(vendor)}</a>'
        return f'<span class="tag">{h(vendor)}</span>'

    for cat in named_cats:
        ptype = cat["product_type"]
        slug = _SLUG_MAP[ptype]
        count = cat["product_count"]
        vcount = cat["vendor_count"]
        top_vendors = cat.get("top_vendors", [])[:3]
        cat_products = [p for p in products if p.get("product_type") == ptype][:12]
        grid = "\n".join(_card_html(p) for p in cat_products) or "<p><em>Sample products not in this week's top-20 digest — full list in Monday email.</em></p>"
        vendor_tags = "".join(_vendor_tag_html(v) for v in top_vendors)
        related = "".join(
            f'<a class="tag" href="./{_SLUG_MAP[o["product_type"]]}.html">→ {h(o["product_type"])} ({o["product_count"]})</a>'
            for o in named_cats if o["product_type"] != ptype
        )
        canonical = f"{_BASE}/category/{slug}.html"
        title = f"Newest DTC {ptype} — {count} New This Week Across {vcount} Curated Brands"
        desc = (
            f"Latest {ptype.lower()} launches from {vcount} curated independent DTC brands "
            f"({', '.join(top_vendors[:2]) or 'various vendors'}). {count} new items in week {week}."
        )
        html_out = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="description" content="{h(desc)}">
<meta name="robots" content="index, follow">
<link rel="canonical" href="{canonical}">
<title>{h(title)} | StoreScope</title>
<script type="application/ld+json">
{{
  "@context": "https://schema.org", "@type": "CollectionPage",
  "name": "Newest DTC {h(ptype)}", "url": "{canonical}",
  "description": "{h(desc)}",
  "isPartOf": {{"@type": "WebSite", "name": "StoreScope", "url": "{_BASE}/"}},
  "hasPart": {{"@type": "ItemList", "numberOfItems": {count}, "name": "{h(ptype)} — week {week}"}}
}}
</script>
<script type="application/ld+json">
{{
  "@context": "https://schema.org", "@type": "BreadcrumbList",
  "itemListElement": [
    {{"@type": "ListItem", "position": 1, "name": "Home", "item": "{_BASE}/"}},
    {{"@type": "ListItem", "position": 2, "name": "Category", "item": "{_BASE}/category/"}},
    {{"@type": "ListItem", "position": 3, "name": "{h(ptype)}", "item": "{canonical}"}}
  ]
}}
</script>
<link rel="icon" type="image/svg+xml" href="{_FAVICON}">
<style>{_SEO_CSS}</style>
</head>
<body>
{_TOP_NAV}<div class="breadcrumb"><a href="../">Home</a> · <a href="./">Category</a> · {h(ptype)}</div>
<main>
<h1>Newest DTC {h(ptype)}</h1>
<p class="meta">{count} new {h(ptype).lower()} indexed in week {week} across {vcount} curated independent DTC brands. Auto-refreshed every Sunday.</p>

<div class="stats">
  <div class="stat"><div class="stat-num">{count}</div><div class="stat-label">New items · this week</div></div>
  <div class="stat"><div class="stat-num">{vcount}</div><div class="stat-label">Contributing brands</div></div>
  <div class="stat"><div class="stat-num">{week}</div><div class="stat-label">Week</div></div>
</div>

<h2>Top {h(ptype).lower()} vendors this week</h2>
<p>{vendor_tags}</p>

<h2>Sample from this week's top 20 digest</h2>
{grid}

<h2>How this list is built</h2>
<p>StoreScope crawls the public <code>/products.json</code> feed of 58 curated DTC brands every Sunday night. New items from the last 30 days are grouped by <code>product_type</code> (Shopify's native taxonomy), deduplicated across brands, and surfaced by first-appearance freshness with vendor/category diversity caps.</p>

<a class="cta" href="{_BASE}/?ref=cat-{slug}">Get the Monday digest →</a>

<div class="related"><h3>Browse other categories</h3><p>{related}</p></div>

<footer><a href="{_BASE}/">← StoreScope</a> · <a href="{_BASE}/blog/">Blog</a> · <a href="{_BASE}/compare/">Compare</a> · <a href="{_BASE}/report/">Reports</a> · Independent · Bootstrapped</footer>
</main>
</body>
</html>
"""
        (_CATEGORY_DIR / f"{slug}.html").write_text(html_out, encoding="utf-8")

    # Hub index
    rows = "\n".join(
        f'<li><a href="./{_SLUG_MAP[c["product_type"]]}.html"><strong>{h(c["product_type"])}</strong></a> — {c["product_count"]} new items across {c["vendor_count"]} brands. Top: {h(", ".join(c["top_vendors"][:2]))}</li>'
        for c in named_cats
    )
    hub_html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="description" content="Browse DTC Shopify product launches by category — auto-refreshed weekly across 58 curated independent brands.">
<meta name="robots" content="index, follow">
<link rel="canonical" href="{_BASE}/category/">
<title>DTC Shopify Categories — Weekly Launches | StoreScope</title>
<link rel="icon" type="image/svg+xml" href="{_FAVICON}">
<style>{_SEO_CSS}</style>
</head>
<body>
{_TOP_NAV}<div class="breadcrumb"><a href="../">Home</a> · Category</div>
<main>
<h1>DTC Shopify Categories</h1>
<p class="meta">{total} products in this week's ({week}) top digest across 58 curated independent brands. Auto-refreshed {today}.</p>
<h2>Categories with new items this week</h2>
<ul>
{rows}
</ul>
<a class="cta" href="{_BASE}/?ref=cat-hub">Get the weekly digest →</a>
<footer><a href="{_BASE}/">← StoreScope</a> · Independent · Bootstrapped</footer>
</main>
</body>
</html>
"""
    (_CATEGORY_DIR / "index.html").write_text(hub_html, encoding="utf-8")
    print(f"  → {_CATEGORY_DIR}/*.html ({len(named_cats)} cats + hub)")


def _svg_bar_chart(items: list[tuple[str, int]], width=760, bar_h=32, gap=8) -> str:
    from html import escape as h
    if not items:
        return ""
    max_v = max(v for _, v in items) or 1
    label_w = 130
    bar_w_max = width - label_w - 80
    total_h = len(items) * (bar_h + gap) + 20
    bars = []
    for i, (label, v) in enumerate(items):
        y = i * (bar_h + gap) + 10
        bw = int((v / max_v) * bar_w_max)
        bars.append(
            f'<text x="{label_w - 8}" y="{y + bar_h/2 + 5}" text-anchor="end" fill="#1C1917" font-size="13" font-weight="600">{h(label)}</text>'
            f'<rect x="{label_w}" y="{y}" width="{bw}" height="{bar_h}" fill="#4F46E5" rx="4"/>'
            f'<text x="{label_w + bw + 8}" y="{y + bar_h/2 + 5}" fill="#1C1917" font-size="13" font-weight="700">{v}</text>'
        )
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {total_h}" role="img" aria-label="Category distribution">'
        f'<rect width="{width}" height="{total_h}" fill="#F9F8F6"/>'
        + "".join(bars) + "</svg>"
    )


def _write_state_report(products: list[dict], categories: list[dict], now: datetime) -> None:
    """State of DTC data report — weekly-regenerated backlink bait.

    URL: /report/state-of-dtc-shopify-latest.html (permanent) + hub.
    매주 pipeline 실행 시 fresh data 로 재작성. Archive (state-of-dtc-shopify-YYYY-WNN)
    는 별도 파일로 유지 — sitemap 관리 부담 회피 위해 latest URL 만 sitemap 등록.
    """
    from html import escape as h
    from collections import Counter
    # D+14 empty guard: stale crawl 로 empty report 생성 방지.
    if not products or not categories:
        print(f"  → state report SKIP (empty products/categories — stale crawl?)")
        return
    _REPORT_DIR.mkdir(parents=True, exist_ok=True)

    week = f"{now.isocalendar().year}-W{now.isocalendar().week:02d}"
    today = now.strftime("%Y-%m-%d")
    named_cats = [c for c in categories if c["product_type"] in _SLUG_MAP]
    uncat = next((c for c in categories if c["product_type"] == "Uncategorized"), None)
    uncat_count = uncat["product_count"] if uncat else 0
    cat_total = sum(c["product_count"] for c in named_cats)

    # Vendor top-N from top-20 products.
    vend_counter = Counter(p.get("vendor", "?") for p in products if p.get("vendor"))
    top_vendors = vend_counter.most_common(10)

    # Price stats.
    prices = sorted(float(p["price"]) for p in products if isinstance(p.get("price"), (int, float)) and p.get("price"))
    if prices:
        p_min, p_max = int(prices[0]), int(prices[-1])
        p_median = int(prices[len(prices) // 2])
    else:
        p_min = p_max = p_median = 0

    ages = [p["age_days"] for p in products if isinstance(p.get("age_days"), (int, float))]
    avg_age = sum(ages) / len(ages) if ages else 0

    cats_sorted = sorted(named_cats, key=lambda c: -c["product_count"])
    chart = _svg_bar_chart([(c["product_type"], c["product_count"]) for c in cats_sorted])

    vendor_rows = "\n".join(
        f"<tr><td>{h(v)[:50]}</td><td>{c}</td></tr>" for v, c in top_vendors
    )
    cat_rows = "\n".join(
        f"<tr><td>{h(c['product_type'])}</td><td>{c['product_count']}</td><td>{c['vendor_count']}</td><td>{h(', '.join(c['top_vendors'][:3]))}</td></tr>"
        for c in cats_sorted
    )

    canonical = f"{_BASE}/report/state-of-dtc-shopify-latest.html"
    title = f"The State of DTC Shopify — Week {now.isocalendar().week}, {now.year}"
    desc = f"Data snapshot of 58 curated independent DTC Shopify brands — {len(products)} items in the top digest, {cat_total} categorized items across {len(named_cats)} product types."
    citation = f'StoreScope. "The State of DTC Shopify — Week {now.isocalendar().week}, {now.year}." {today}. {canonical}'

    html_out = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="description" content="{h(desc)}">
<meta name="robots" content="index, follow">
<link rel="canonical" href="{canonical}">
<title>{h(title)} | StoreScope</title>
<script type="application/ld+json">
{{
  "@context": "https://schema.org", "@type": "Report",
  "headline": "{h(title)}", "description": "{h(desc)}",
  "url": "{canonical}", "datePublished": "{today}", "dateModified": "{today}",
  "author": {{"@type": "Organization", "name": "StoreScope", "url": "{_BASE}/"}},
  "publisher": {{"@type": "Organization", "name": "StoreScope", "logo": {{"@type": "ImageObject", "url": "{_BASE}/og-image.png"}}}},
  "mainEntityOfPage": {{"@type": "WebPage", "@id": "{canonical}"}},
  "keywords": ["DTC", "Shopify", "product research", "e-commerce data"]
}}
</script>
<script type="application/ld+json">
{{
  "@context": "https://schema.org", "@type": "BreadcrumbList",
  "itemListElement": [
    {{"@type": "ListItem", "position": 1, "name": "Home", "item": "{_BASE}/"}},
    {{"@type": "ListItem", "position": 2, "name": "Reports", "item": "{_BASE}/report/"}},
    {{"@type": "ListItem", "position": 3, "name": "State of DTC {week}", "item": "{canonical}"}}
  ]
}}
</script>
<link rel="icon" type="image/svg+xml" href="{_FAVICON}">
<style>{_SEO_CSS}</style>
</head>
<body>
{_TOP_NAV}<div class="breadcrumb"><a href="../">Home</a> · <a href="./">Reports</a> · State of DTC {week}</div>
<main>
<h1>{h(title)}</h1>
<p class="meta">Published {today} · Week {week} · Free to cite, embed, republish with attribution.</p>

<p><strong>What this is:</strong> A weekly snapshot of new-product velocity across 58 curated independent DTC Shopify brands. Not "top sellers." Not "TikTok trending." Just: what did these 58 brands actually launch, publicly, in the last 30 days.</p>

<h2>Headline numbers</h2>
<div class="stats">
  <div class="stat"><div class="stat-num">58</div><div class="stat-label">Curated DTC brands</div></div>
  <div class="stat"><div class="stat-num">{len(products)}</div><div class="stat-label">Products in top digest</div></div>
  <div class="stat"><div class="stat-num">{cat_total}</div><div class="stat-label">Categorized items · 30d</div></div>
  <div class="stat"><div class="stat-num">{len(named_cats)}</div><div class="stat-label">Categories</div></div>
  <div class="stat"><div class="stat-num">${p_median}</div><div class="stat-label">Median top-{len(products)} price</div></div>
  <div class="stat"><div class="stat-num">{avg_age:.1f}d</div><div class="stat-label">Avg age (freshness)</div></div>
</div>

<h2>Category distribution</h2>
<p>The {cat_total} categorized items published in the last 30 days break down as follows. <strong>{uncat_count} additional items</strong> lacked a Shopify <code>product_type</code> tag and are excluded from category totals but present in the full crawl.</p>
{chart}

<table>
<thead><tr><th>Category</th><th>Items (30d)</th><th>Contributing brands</th><th>Top vendors</th></tr></thead>
<tbody>
{cat_rows}
</tbody>
</table>

<h2>Top vendors in this week's digest</h2>
<p>The Monday digest surfaces {len(products)} items per week with strict vendor and category diversity caps (max {MAX_PER_VENDOR} per vendor).</p>
<table>
<thead><tr><th>Vendor</th><th>Items in top {len(products)}</th></tr></thead>
<tbody>
{vendor_rows}
</tbody>
</table>

<h2>Price + freshness</h2>
<p>Top-{len(products)} digest spans <strong>${p_min}-${p_max}</strong> (median <strong>${p_median}</strong>). Average product age <strong>{avg_age:.1f} days</strong>, newest <strong>{min(ages) if ages else 0:.1f} days</strong>. The pipeline enforces a 30-day sliding window; older items drop off.</p>

<h2>Methodology</h2>
<p>StoreScope crawls the public <code>/products.json</code> feed of each curated brand every Sunday night (UTC). No authenticated data, no private catalogs, no ad-library scraping. Deduplication uses perceptual image hash (pHash) + title similarity. Categories use Shopify's native <code>product_type</code> field.</p>

<p><strong>What this is NOT:</strong></p>
<ul>
<li>Not a "trend prediction" — no ML, no ad spy, no revenue estimate.</li>
<li>Not exhaustive of the DTC universe — 58 brands is a curated slice.</li>
<li>Not a leading indicator of what will "go viral" — a lagging indicator of what these brands chose to ship.</li>
</ul>

<h2>Use this data</h2>
<p>Free to cite, embed, or republish. All charts are inline SVG (right-click → save, or view source).</p>

<div class="cite-box">
<strong>Citation:</strong>
<code>{h(citation)}</code>
<strong>Direct link:</strong>
<code>{canonical}</code>
</div>

<a class="cta" href="{_BASE}/?ref=state-report">See the live product →</a>

<footer><a href="{_BASE}/">← StoreScope</a> · <a href="{_BASE}/blog/">Blog</a> · <a href="{_BASE}/compare/">Compare</a> · <a href="{_BASE}/category/">Categories</a> · Independent · Bootstrapped</footer>
</main>
</body>
</html>
"""
    (_REPORT_DIR / "state-of-dtc-shopify-latest.html").write_text(html_out, encoding="utf-8")

    # Hub index
    hub_html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="description" content="StoreScope data reports on DTC Shopify — weekly snapshots of 58 curated independent brands. Free, open, cite anywhere.">
<meta name="robots" content="index, follow">
<link rel="canonical" href="{_BASE}/report/">
<title>DTC Shopify Data Reports | StoreScope</title>
<link rel="icon" type="image/svg+xml" href="{_FAVICON}">
<style>{_SEO_CSS}</style>
</head>
<body>
{_TOP_NAV}<div class="breadcrumb"><a href="../">Home</a> · Reports</div>
<main>
<h1>DTC Shopify Data Reports</h1>
<p class="meta">Weekly snapshots. Free to cite, embed, republish.</p>
<h2>Latest report</h2>
<p><a href="./state-of-dtc-shopify-latest.html" style="font-weight:700; font-size: 1.1rem;">→ The State of DTC Shopify — Week {now.isocalendar().week}, {now.year}</a></p>
<p>Published {today}. 58 DTC brands · {len(products)} products in digest · {cat_total} categorized items across {len(named_cats)} categories.</p>
<h2>What these reports are for</h2>
<p>Working data. Cite it, embed the charts, republish the numbers. Attribution back to the source URL so readers see the raw weekly refresh.</p>
<footer><a href="{_BASE}/">← StoreScope</a> · Independent · Bootstrapped</footer>
</main>
</body>
</html>
"""
    (_REPORT_DIR / "index.html").write_text(hub_html, encoding="utf-8")
    print(f"  → {_REPORT_DIR}/state-of-dtc-shopify-latest.html + hub")


if __name__ == "__main__":
    main()
