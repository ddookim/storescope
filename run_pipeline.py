"""
StoreScope — 전체 파이프라인 실행 (subprocess 격리)
=====================================================
각 단계를 독립 프로세스로 실행하여 OS 레벨 메모리 반환을 보장한다.
단계 실패 시 후속 단계를 실행하지 않고 즉시 종료한다.

실행:
    python run_pipeline.py              # 전체 실행 (0→5)
    python run_pipeline.py --step 2     # 특정 단계만
    python run_pipeline.py --from 3     # 3단계부터 재개 (Droplet 이미 켜진 경우)
"""

import argparse
import os
import subprocess
import sys
import time

_PY = sys.executable
_ENV = {**os.environ, "PYTHONUNBUFFERED": "1"}

STEPS: list[tuple[str, list[str]]] = [
    ("0. Droplet 기동",                   [_PY, "-m", "scripts.scale_infra", "power_on"]),
    ("1. 스토어 발견 (CDX API)",           [_PY, "-m", "scripts.discover_stores"]),
    ("2. 상품 크롤링 (/products.json)",    [_PY, "-m", "pipeline.crawl_products"]),
    ("3. pHash 클러스터링",                [_PY, "-m", "pipeline.cluster_products"]),
    ("4. DB 적재 + 트렌드 스냅샷",         [_PY, "-m", "pipeline.load_to_db"]),
    ("5. Droplet 셧다운",                  [_PY, "-m", "scripts.scale_infra", "power_off"]),
]


def _run_step(idx: int, name: str, cmd: list[str]) -> None:
    print(f"\n{'='*52}", flush=True)
    print(f"  STEP {idx}: {name}", flush=True)
    print(f"{'='*52}", flush=True)

    t = time.monotonic()
    try:
        subprocess.run(cmd, env=_ENV, check=True)
    except subprocess.CalledProcessError as exc:
        elapsed = time.monotonic() - t
        print(
            f"\n[FAIL] STEP {idx} 실패 (종료코드 {exc.returncode}, {elapsed:.1f}초)",
            flush=True,
        )
        print("파이프라인 중단.", flush=True)
        sys.exit(exc.returncode)

    elapsed = time.monotonic() - t
    print(f"  완료 ({elapsed:.1f}초)", flush=True)


def run_all(from_step: int = 0) -> None:
    total = len(STEPS)
    for idx, (name, cmd) in enumerate(STEPS):
        if idx < from_step:
            print(f"  STEP {idx} 건너뜀 (--from {from_step})", flush=True)
            continue
        _run_step(idx, name, cmd)

    print(f"\n전체 파이프라인 완료 ({from_step}~{total - 1}단계)", flush=True)


def run_single(step: int) -> None:
    if not 0 <= step < len(STEPS):
        print(f"유효한 스텝: 0~{len(STEPS) - 1}", flush=True)
        sys.exit(1)
    name, cmd = STEPS[step]
    _run_step(step, name, cmd)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="StoreScope 파이프라인 실행기")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--step", type=int, metavar="N", help="특정 단계만 실행 (0~5)")
    group.add_argument("--from", type=int, metavar="N", dest="from_step",
                       help="N단계부터 끝까지 실행 (실패 후 재개용)")
    args = parser.parse_args()

    if args.step is not None:
        run_single(args.step)
    else:
        run_all(from_step=args.from_step or 0)
