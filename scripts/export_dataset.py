"""
Path C Dataset Export — Hugging Face / Kaggle / AWS Data Exchange 게재 준비

마스터플랜 §4 Path C · ARCHIVE 가동 시:
    - 유료 0-2명 AND DAU < 20 → Dataset 모드
    - Hugging Face 업로드 + 분기별 갱신 cron
    - 다운로드 ≥100 + 결제 ≥3건이 D+90 통과 기준

생성:
    out/storescope-shopify-cross-store-{YYYY-WNN}/
        clusters.jsonl       — 클러스터별 메타 (1 line per cluster)
        products.jsonl       — 제품별 메타 (PII 제외, hash만)
        stores.jsonl         — 스토어 도메인 + 카테고리
        manifest.json        — 데이터셋 카드 (Hugging Face README 입력)
        LICENSE              — CC BY-NC 4.0 (상업적 사용 제한)
        README.md            — Hugging Face 데이터셋 카드 형식

마스터플랜 정합:
    "데이터 sourcing은 모두 public /products.json — 2024 federal ruling 정합"
    "API 키 / 사용자 데이터 / customer 이메일은 절대 포함 안 함"

용도:
    Path C 가동 시: HF 업로드 → academic citation 발생 → 진짜 해자 (평가제안서: 26주 데이터)
    Path A/B 가동 시에도: dataset 카드는 SEO/PR 자산 (Show HN: "We open-sourced 1,671 product clusters")
"""

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path


def _setup_paths():
    here = Path(__file__).resolve().parent.parent
    sys.path.insert(0, str(here))
    os.environ.setdefault("DATABASE_URL", "postgresql:///storescope")


def _export_clusters(out: Path):
    """Cluster level: id, store_count, product_count, week_delta, representative title."""
    from api.auth import get_conn
    written = 0
    with out.open("w") as f:
        with get_conn() as conn:
            cur = conn.cursor(name="stream_clusters")  # server-side cursor
            cur.itersize = 1_000
            cur.execute("""
                SELECT
                    c.id,
                    c.cluster_hash,
                    c.store_count,
                    c.product_count,
                    c.first_seen,
                    c.last_updated,
                    COALESCE(ts.week_delta, 0) AS week_delta,
                    p.title AS rep_title
                FROM clusters c
                LEFT JOIN LATERAL (
                    SELECT week_delta FROM trend_snapshots
                    WHERE cluster_id = c.id
                    ORDER BY snapshot_at DESC LIMIT 1
                ) ts ON true
                LEFT JOIN LATERAL (
                    SELECT p2.title FROM products p2
                    JOIN product_clusters pc2 ON pc2.product_id = p2.id
                    WHERE pc2.cluster_id = c.id
                    ORDER BY (p2.price_min > 0) DESC, p2.price_min ASC NULLS LAST
                    LIMIT 1
                ) p ON true
            """)
            for r in cur:
                row = {
                    "cluster_id": r[0],
                    "phash_hex": r[1],   # 16-char hex
                    "store_count": r[2],
                    "product_count": r[3],
                    "first_seen": r[4].isoformat() if r[4] else None,
                    "last_updated": r[5].isoformat() if r[5] else None,
                    "week_delta": r[6],
                    "representative_title": r[7] or None,
                }
                f.write(json.dumps(row, ensure_ascii=False) + "\n")
                written += 1
            cur.close()
    return written


def _export_products(out: Path):
    """Product level: 가격, 첫 발견일, 이미지 hash (URL 아님). PII 제거."""
    from api.auth import get_conn
    written = 0
    with out.open("w") as f:
        with get_conn() as conn:
            cur = conn.cursor(name="stream_products")
            cur.itersize = 1_000
            cur.execute("""
                SELECT
                    p.id,
                    p.store_id,
                    p.shopify_id,
                    p.title,
                    p.handle,
                    p.price_min,
                    p.price_max,
                    p.image_hash,
                    p.first_seen,
                    p.last_seen,
                    pc.cluster_id
                FROM products p
                LEFT JOIN product_clusters pc ON pc.product_id = p.id
            """)
            for r in cur:
                row = {
                    "product_id": r[0],
                    "store_id": r[1],
                    "shopify_id": r[2],
                    "title": r[3],
                    "handle": r[4],
                    "price_min": float(r[5]) if r[5] is not None else None,
                    "price_max": float(r[6]) if r[6] is not None else None,
                    "image_phash_hex": r[7],
                    "first_seen": r[8].isoformat() if r[8] else None,
                    "last_seen": r[9].isoformat() if r[9] else None,
                    "cluster_id": r[10],
                }
                f.write(json.dumps(row, ensure_ascii=False) + "\n")
                written += 1
            cur.close()
    return written


def _export_stores(out: Path):
    """Store level: domain + product_count. PII (이메일 등) 절대 포함 안 함."""
    from api.auth import get_conn
    written = 0
    with out.open("w") as f:
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT id, domain, product_count, first_seen, last_crawled, is_active
                    FROM stores
                    ORDER BY id ASC
                """)
                for r in cur:
                    if not r[5]:  # is_active=False (opt-out한 스토어) → 제외
                        continue
                    row = {
                        "store_id": r[0],
                        "domain": r[1],
                        "product_count": r[2],
                        "first_seen": r[3].isoformat() if r[3] else None,
                        "last_crawled": r[4].isoformat() if r[4] else None,
                    }
                    f.write(json.dumps(row, ensure_ascii=False) + "\n")
                    written += 1
    return written


def _write_manifest(out: Path, stats: dict):
    """Hugging Face 데이터셋 카드 메타데이터."""
    week = datetime.now(timezone.utc).strftime("%Y-W%V")
    manifest = {
        "dataset_id": "ddookim/storescope-shopify-cross-store",
        "version": week,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "license": "CC BY-NC 4.0",
        "description": (
            "Cross-store perceptual hash (pHash) clustering of publicly listed Shopify products. "
            "1,671 product clusters across 1,419 stores indexed weekly. "
            "Source: public /products.json endpoints in compliance with robots.txt."
        ),
        "homepage": "https://storescope.com",
        "contact": "dodo@storescope.com",
        "files": {
            "clusters.jsonl": {"records": stats.get("clusters", 0)},
            "products.jsonl": {"records": stats.get("products", 0)},
            "stores.jsonl": {"records": stats.get("stores", 0)},
        },
        "schema": {
            "clusters.jsonl": [
                "cluster_id", "phash_hex", "store_count", "product_count",
                "first_seen", "last_updated", "week_delta", "representative_title",
            ],
            "products.jsonl": [
                "product_id", "store_id", "shopify_id", "title", "handle",
                "price_min", "price_max", "image_phash_hex",
                "first_seen", "last_seen", "cluster_id",
            ],
            "stores.jsonl": [
                "store_id", "domain", "product_count",
                "first_seen", "last_crawled",
            ],
        },
        "pii_policy": "No customer emails, no API keys, no auth data, no IP addresses",
        "data_provenance": (
            "Public Shopify /products.json endpoints, scraped weekly in compliance "
            "with each store's robots.txt. Stores can request removal via "
            "https://storescope.com/#optout."
        ),
        "citation": (
            "Kim, D. (2026). StoreScope: Cross-Store Shopify Product Clusters via Perceptual Hashing. "
            "Hugging Face. https://huggingface.co/datasets/ddookim/storescope-shopify-cross-store"
        ),
    }
    (out / "manifest.json").write_text(json.dumps(manifest, indent=2, ensure_ascii=False))


_LICENSE_TXT = """Creative Commons Attribution-NonCommercial 4.0 International (CC BY-NC 4.0)

Copyright (c) 2026 StoreScope (Dodo Kim)

You are free to:
  - Share — copy and redistribute the material in any medium or format
  - Adapt — remix, transform, and build upon the material

Under the following terms:
  - Attribution — You must give appropriate credit, provide a link to the license,
    and indicate if changes were made.
  - NonCommercial — You may not use the material for commercial purposes.
    Commercial use requires a separate license (contact dodo@storescope.com).

Full license: https://creativecommons.org/licenses/by-nc/4.0/legalcode
"""


_README_TPL = """---
license: cc-by-nc-4.0
task_categories:
  - tabular-classification
  - feature-extraction
language:
  - en
tags:
  - shopify
  - e-commerce
  - perceptual-hashing
  - cross-store
  - product-clustering
size_categories:
  - 100K<n<1M
---

# StoreScope — Shopify Cross-Store Product Clusters

Perceptual hash (pHash) clustering of publicly listed Shopify products across 1,400+ stores.
Refreshed weekly. Useful for: dropshipping competitive intelligence, brand IP monitoring,
e-commerce time-series studies, recommender system training data.

## Dataset Composition (Week {week})

- **clusters.jsonl** — {n_clusters} product clusters (perceptual hash families)
- **products.jsonl** — {n_products} product records (PII-free, hash only)
- **stores.jsonl** — {n_stores} active Shopify storefronts (opt-out respected)

## Methodology

Each product's image is encoded as a 64-bit perceptual hash (pHash, 16 hex chars).
Two products cluster together when their Hamming distance ≤ 8. Weekly refresh tracks
how store adoption (count of stores carrying each cluster) changes over time.

## Data Provenance & License

- Source: public Shopify `/products.json` endpoints, scraped weekly per each store's robots.txt
- Legal basis: 2024 U.S. federal ruling on logged-out public commercial data
- License: **CC BY-NC 4.0** (free for research / non-commercial; contact dodo@storescope.com for commercial)
- PII: **none** — no customer emails, no API keys, no auth tokens, no IPs
- Opt-out: stores can request removal via https://storescope.com/#optout

## Loading

```python
import pandas as pd

clusters = pd.read_json("clusters.jsonl", lines=True)
products = pd.read_json("products.jsonl", lines=True)
stores   = pd.read_json("stores.jsonl", lines=True)

# Top trending clusters this week
top = clusters.nlargest(20, "week_delta")
print(top[["cluster_id", "representative_title", "store_count", "week_delta"]])
```

## Citation

```bibtex
@dataset{{kim2026storescope,
  title  = {{StoreScope: Cross-Store Shopify Product Clusters via Perceptual Hashing}},
  author = {{Kim, Dodo}},
  year   = {{2026}},
  publisher = {{Hugging Face}},
  url    = {{https://huggingface.co/datasets/ddookim/storescope-shopify-cross-store}}
}}
```

## Contact

dodo@storescope.com · https://storescope.com
"""


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--output", default=None, help="output dir (default: out/storescope-{YYYY-WNN})")
    args = p.parse_args()

    _setup_paths()
    week = datetime.now(timezone.utc).strftime("%Y-W%V")
    out_root = Path(args.output) if args.output else Path(f"out/storescope-shopify-cross-store-{week}")
    out_root.mkdir(parents=True, exist_ok=True)

    print(f"=== Path C Dataset Export ({week}) ===")
    print(f"output: {out_root}")

    print("[1/4] clusters.jsonl 스트리밍...")
    n_clusters = _export_clusters(out_root / "clusters.jsonl")
    print(f"  {n_clusters}건")

    print("[2/4] products.jsonl 스트리밍 (대용량 — 수초 소요)...")
    n_products = _export_products(out_root / "products.jsonl")
    print(f"  {n_products}건")

    print("[3/4] stores.jsonl (opt-out 제외)...")
    n_stores = _export_stores(out_root / "stores.jsonl")
    print(f"  {n_stores}건")

    print("[4/4] manifest + LICENSE + README...")
    stats = {"clusters": n_clusters, "products": n_products, "stores": n_stores}
    _write_manifest(out_root, stats)
    (out_root / "LICENSE").write_text(_LICENSE_TXT)
    (out_root / "README.md").write_text(
        _README_TPL.format(
            week=week,
            n_clusters=n_clusters,
            n_products=n_products,
            n_stores=n_stores,
        )
    )

    total_size = sum(f.stat().st_size for f in out_root.rglob("*") if f.is_file())
    print(f"\n완료. 총 사이즈 {total_size/1024/1024:.1f} MB")
    print(f"Hugging Face 업로드 (Path C 가동 시):")
    print(f"  cd {out_root}")
    print(f"  huggingface-cli upload ddookim/storescope-shopify-cross-store .")


if __name__ == "__main__":
    main()
