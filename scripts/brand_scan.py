"""
StoreScope Brand IP Scanner — Cold Email 무기

마스터플랜 §5 Path B 72h 플랜 + 평가제안서 #1 처방 (콜드메일 = 결정론적 채널).
DTC 브랜드명 → 무단 sellers 매칭 + 증거 PDF 생성 + 콜드메일용 한줄 요약.

사용:
    python scripts/brand_scan.py LULULEMON
    python scripts/brand_scan.py --batch brands.txt    # 한 줄에 브랜드 하나
    python scripts/brand_scan.py --report-only NIKE    # PDF 생성, 출력 최소

출력:
    /tmp/storescope_scan_{brand}_{ts}/
        report.pdf                    # 증거 PDF
        summary.txt                   # 콜드메일 #1 첫 줄용 한 줄 요약
        outreach_subject.txt          # 콜드메일 subject 추천 (3가지)
        targets.csv                   # 매칭 store + product 리스트 (Apollo 리드 검증용)

평가제안서 EMAIL #1 정합:
    "사전 X-Ray 결과 첨부" = 본 스크립트 출력 그대로 첨부
    "개인화 핵심 = 그 회사 데이터" = brand_name 1회 입력으로 자동
"""

import argparse
import csv
import os
import sys
from datetime import datetime, timezone
from pathlib import Path


def _setup_paths():
    here = Path(__file__).resolve().parent.parent
    sys.path.insert(0, str(here))
    os.environ.setdefault("DATABASE_URL", "postgresql:///storescope")


def _scan_brand(brand: str, min_stores: int = 3) -> dict:
    """Brand keyword → matches + summary 통계 반환."""
    from services.counterfeit_report import fetch_matches_for_brand

    matches = fetch_matches_for_brand(brand, min_store_count=min_stores)
    if not matches:
        return {"matches": [], "stores": 0, "clusters": 0, "avg_conf": 0.0}

    unique_stores = {m.store_domain for m in matches}
    unique_clusters = {m.cluster_id for m in matches}
    avg_conf = sum(m.confidence for m in matches) / len(matches)
    return {
        "matches": matches,
        "stores": len(unique_stores),
        "clusters": len(unique_clusters),
        "avg_conf": avg_conf,
        "top_stores": sorted(unique_stores)[:10],
    }


def _generate_pdf(brand: str, matches: list, out_path: Path) -> int:
    """PDF 생성 → byte size 반환."""
    from services.counterfeit_report import generate
    pdf = generate(brand_name=brand.upper(), matched_clusters=matches)
    out_path.write_bytes(pdf)
    return len(pdf)


def _write_summary(brand: str, result: dict, out: Path) -> None:
    """콜드메일 #1 첫 줄 자동 생성."""
    n = len(result["matches"])
    s = result["stores"]
    c = result["clusters"]
    text = (
        f"StoreScope identified {n} product listings across {s} Shopify stores "
        f"selling products with imagery matching {brand.upper()}.\n"
        f"Distinct product clusters: {c}. Average confidence: {result['avg_conf']:.0%}.\n"
        f"Top 10 stores (alphabetical):\n"
    )
    for store in result["top_stores"]:
        text += f"  - {store}\n"
    out.write_text(text)


def _write_subjects(brand: str, n_stores: int, out: Path) -> None:
    """평가제안서 분석 기반 A/B/C 3가지 subject — 솔직 vs 호기심 vs 데이터 직격."""
    subjects = [
        f"[{n_stores}] Shopify stores currently selling {brand} products",
        f"Quick {brand} brand audit — found {n_stores} unauthorized listings",
        f"{brand}: {n_stores}-store counterfeit cluster report (5 min read)",
    ]
    out.write_text("\n".join(subjects) + "\n")


def _write_targets_csv(matches: list, out: Path) -> None:
    """Apollo.io 리드 검증용 CSV — store domain, sample product, price."""
    with out.open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["store_domain", "sample_product", "price_min", "price_max", "confidence", "cluster_id"])
        for m in sorted(matches, key=lambda x: x.confidence, reverse=True):
            w.writerow([
                m.store_domain,
                m.product_title[:80],
                f"{m.price_min:.2f}" if m.price_min is not None else "",
                f"{m.price_max:.2f}" if m.price_max is not None else "",
                f"{m.confidence:.3f}",
                m.cluster_id,
            ])


def scan_one(brand: str, report_only: bool = False, min_stores: int = 3) -> Path:
    """단일 brand 스캔 + 출력 디렉토리 반환."""
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    safe_brand = "".join(c if c.isalnum() else "_" for c in brand.lower())
    out_dir = Path(f"/tmp/storescope_scan_{safe_brand}_{ts}")
    out_dir.mkdir(parents=True, exist_ok=True)

    if not report_only:
        print(f"\n=== {brand.upper()} 스캔 시작 ===")
        print(f"output: {out_dir}")

    result = _scan_brand(brand, min_stores=min_stores)
    if not result["matches"]:
        print(f"[{brand}] 매칭 없음 (min_stores={min_stores}) — 보고서 생성 안 함")
        return out_dir

    if not report_only:
        print(f"  matches: {len(result['matches'])}")
        print(f"  unique stores: {result['stores']}")
        print(f"  clusters: {result['clusters']}")
        print(f"  avg confidence: {result['avg_conf']:.0%}")

    pdf_size = _generate_pdf(brand, result["matches"], out_dir / "report.pdf")
    _write_summary(brand, result, out_dir / "summary.txt")
    _write_subjects(brand, result["stores"], out_dir / "outreach_subject.txt")
    _write_targets_csv(result["matches"], out_dir / "targets.csv")

    if not report_only:
        print(f"  PDF: report.pdf ({pdf_size/1024:.1f} KB)")
        print(f"  summary.txt + outreach_subject.txt + targets.csv 생성")
        print(f"\n  → 콜드메일 첨부물 준비 완료. {out_dir}/")

    return out_dir


def main():
    p = argparse.ArgumentParser(description="Brand IP scanner — cold email 무기")
    p.add_argument("brand", nargs="?", help="단일 brand 키워드 (예: LULULEMON)")
    p.add_argument("--batch", help="brand 한 줄당 한 개 들어있는 txt 파일")
    p.add_argument("--min-stores", type=int, default=3, help="cluster 최소 store 수 (기본 3)")
    p.add_argument("--report-only", action="store_true", help="출력 최소화 (PDF만)")
    args = p.parse_args()

    _setup_paths()

    if args.batch:
        targets = [b.strip() for b in Path(args.batch).read_text().splitlines() if b.strip()]
        print(f"\n=== Batch 스캔: {len(targets)} brands ===")
        for b in targets:
            try:
                scan_one(b, report_only=True, min_stores=args.min_stores)
            except Exception as e:
                print(f"[{b}] FAIL: {e}")
        return

    if not args.brand:
        p.print_help()
        sys.exit(2)

    scan_one(args.brand, report_only=args.report_only, min_stores=args.min_stores)


if __name__ == "__main__":
    main()
