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
PRODUCTS_DIR = _HERE / "data" / "products"
OUTPUT_FILE  = _HERE / "data" / "latest_products.json"

TOP_N              = 20     # digest 표시 개수
LOOKBACK_DAYS      = 30     # 30일 이내 published 만 대상 (fresh signal)
MIN_TITLE_LEN      = 8      # placeholder/junk 제외 ("Test", "Sample" 등)


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


def main() -> None:
    if not PRODUCTS_DIR.exists():
        print(f"[SKIP] {PRODUCTS_DIR} 없음 — crawl 먼저 실행")
        # write empty output so downstream steps don't 404.
        OUTPUT_FILE.parent.mkdir(parents=True, exist_ok=True)
        OUTPUT_FILE.write_text("[]")
        return

    now = datetime.now(timezone.utc)
    products = _extract_products(now)
    print(f"  {LOOKBACK_DAYS}일 내 published: {len(products):,}개")

    products = _dedupe_by_title_domain(products)
    print(f"  dedupe 후: {len(products):,}개")

    # Sort: newest first (published_at DESC → age_days ASC).
    products.sort(key=lambda p: p["age_days"])
    top = products[:TOP_N]

    OUTPUT_FILE.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_FILE.write_text(json.dumps(top, ensure_ascii=False, indent=2))
    print(f"  → {OUTPUT_FILE} ({len(top)}개, top {TOP_N})")

    if top:
        print("\n=== 상위 5개 최신 상품 ===")
        for i, p in enumerate(top[:5], 1):
            print(f"  {i}. [{p['age_days']}d] {p['title'][:60]}")
            print(f"     {p['vendor']} · {p['product_type']} · ${p['price'] or '—'}")


if __name__ == "__main__":
    main()
