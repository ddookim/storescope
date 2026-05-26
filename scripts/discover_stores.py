"""
StoreScope — Shopify 스토어 URL 수집 (Wayback Machine CDX)
=============================================================
Wayback Machine CDX API로 최근 1년 이내에 products.json이 확인된
스토어만 수집합니다. 오래된 데이터는 현재 비공개/폐쇄 가능성이 높아 제외.

실행:
    python scripts/discover_stores.py

출력:
    data/shopify_stores.txt  — 발견된 스토어 도메인 (중복 제거)
"""

import re
import time
import warnings
from pathlib import Path

import requests
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
    before_sleep_log,
)
import logging

logging.basicConfig(level=logging.INFO)
_log = logging.getLogger(__name__)

warnings.filterwarnings("ignore")  # urllib3 LibreSSL 경고 억제

OUTPUT_FILE = Path("data/shopify_stores.txt")
WAYBACK_CDX = "https://web.archive.org/cdx/search/cdx"
# FIX: Chrome UA 사칭 → 투명한 봇 UA. Wayback CDX API는 공개 API이므로 사칭 불필요.
HEADERS = {
    "User-Agent": "StoreScope/1.0 (https://storescope.com; mailto:dodo32032@gmail.com)"
}

EXCLUDED_DOMAINS = {"www.myshopify.com", "www1.myshopify.com", "cdn.myshopify.com"}

# 최근 1년 단위로 여러 시기 검색 (최신 → 과거 순)
DATE_RANGES = [
    ("20250101", "20260101"),  # 2025
    ("20240101", "20250101"),  # 2024
    ("20230101", "20240101"),  # 2023 (백업용)
]

SEARCH_PATTERNS = [
    "*.myshopify.com/products.json",
    "*.myshopify.com/collections/all/products.json",
]


@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=5, max=30),
    retry=retry_if_exception_type(requests.RequestException),
    before_sleep=before_sleep_log(_log, logging.WARNING),
    reraise=True,
)
def _fetch_raw(pattern: str, from_date: str, to_date: str, limit: int) -> str:
    """Wayback CDX HTTP 요청 — 429/503 시 최대 3회 지수 백오프 재시도."""
    resp = requests.get(
        WAYBACK_CDX,
        params={
            "url": pattern,
            "output": "text",
            "fl": "original",
            "filter": "statuscode:200",
            "collapse": "urlkey",
            "from": from_date,
            "to": to_date,
            "limit": limit,
        },
        headers=HEADERS,
        timeout=60,
    )
    resp.raise_for_status()
    return resp.text


def fetch_domains(pattern: str, from_date: str, to_date: str, limit: int = 50000) -> set[str]:
    """Wayback CDX에서 특정 기간의 myshopify 도메인 수집"""
    try:
        text = _fetch_raw(pattern, from_date, to_date, limit)
    except requests.RequestException as e:
        print(f"  [오류] {pattern} ({from_date}-{to_date}): {e} (3회 재시도 후 최종 실패)")
        return set()

    domains = set()
    for line in text.splitlines():
        m = re.search(r"https?://([a-z0-9][a-z0-9\-]*\.myshopify\.com)", line.lower())
        if not m:
            continue
        domain = m.group(1)
        if domain in EXCLUDED_DOMAINS:
            continue
        domains.add(domain)

    return domains


def main():
    OUTPUT_FILE.parent.mkdir(exist_ok=True)

    existing: set[str] = set()
    if OUTPUT_FILE.exists():
        existing = {l.strip() for l in OUTPUT_FILE.read_text().splitlines() if l.strip()}
        print(f"기존 데이터: {len(existing)}개 (덮어쓰기 예정)")

    all_domains: set[str] = set()

    for from_date, to_date in DATE_RANGES:
        year = from_date[:4]
        for pattern in SEARCH_PATTERNS:
            print(f"\n수집 중: {pattern} [{year}]")
            found = fetch_domains(pattern, from_date, to_date)
            new = found - all_domains
            all_domains.update(found)
            print(f"  발견: {len(found)}개 | 신규: {len(new)}개 | 누적: {len(all_domains)}개")
            time.sleep(1)

        # 2025년에 충분히 수집됐으면 조기 종료
        if from_date.startswith("2025") and len(all_domains) >= 3000:
            print(f"\n목표 달성 ({len(all_domains)}개), 조기 종료")
            break

    OUTPUT_FILE.write_text("\n".join(sorted(all_domains)))
    print(f"\n완료: 총 {len(all_domains)}개 → {OUTPUT_FILE}")


if __name__ == "__main__":
    start = time.time()
    main()
    print(f"소요 시간: {time.time() - start:.1f}초")
