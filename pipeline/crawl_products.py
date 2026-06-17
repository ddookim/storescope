"""
StoreScope — /products.json 크롤러 (curl-cffi AsyncSession + asyncio)
========================================================================
ThreadPoolExecutor 제거 → asyncio 비동기 큐 기반으로 전환.
- curl_cffi AsyncSession: Chrome TLS 핑거프린트 + 스레드 오버헤드 0
- asyncio.Semaphore: CONCURRENCY 개 동시 요청 상한 제어
- asyncio.gather: 3,413개 태스크를 이벤트 루프 내에서 병렬 처리

실행:
    python -m pipeline.crawl_products

출력:
    data/products/  — 스토어별 JSON 파일
    data/crawl_report.json — 크롤 결과 요약
"""

import asyncio
import json
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from curl_cffi.requests import AsyncSession

STORES_FILE = Path("data/shopify_stores.txt")
OUTPUT_DIR  = Path("data/products")
REPORT_FILE = Path("data/crawl_report.json")

CONCURRENCY     = 15   # 동시 요청 상한 — Semaphore로 제어
REQUEST_TIMEOUT = 15
# FIX: chrome120 TLS impersonation → 투명한 봇 UA로 교체.
# CFAA 및 Shopify ToS 관점에서 TLS 레벨 스푸핑은 명시적 회피 의도 증거.
# /products.json은 공개 엔드포인트이므로 정직한 봇 UA로도 접근 가능.
IMPERSONATE     = None
BOT_UA = "StoreScope/1.0 (https://storescope.com; mailto:dodo32032@gmail.com)"


@dataclass
class CrawlResult:
    domain: str
    success: bool
    product_count: int = 0
    error: str = ""
    crawled_at: str = ""


@dataclass
class CrawlReport:
    total: int = 0
    success: int = 0
    failed: int = 0
    total_products: int = 0
    started_at: str = ""
    finished_at: str = ""
    results: list = field(default_factory=list)


MAX_PAGES    = 10   # 스토어당 최대 페이지 (250 * 10 = 최대 2,500개)
PAGE_LIMIT   = 250  # Shopify API 최대 page size

# ── robots.txt 준수 캐시 (법적 방어선) ──────────────────────────
_ROBOTS_CACHE: dict[str, bool] = {}
_ROBOTS_CACHE_MAX = 5_000              # 1,400 stores × 3 정도 여유. 초과 시 30% 절단
_ROBOTS_TXT_MAX_BYTES = 256 * 1024     # 256KB cap — 일부 사이트 100MB+ robots.txt 메모리 폭주 방어


async def _is_crawl_allowed(session: AsyncSession, domain: str) -> bool:
    """
    robots.txt Disallow: /products 또는 Disallow: / 가 있으면 크롤 건너뜀.
    결과 캐시하여 도메인당 1회만 요청. 캐시 cap 5,000.
    법적 근거: robots.txt 준수는 웹 크롤링 선의(good faith)의 표준 증거.
    """
    if domain in _ROBOTS_CACHE:
        return _ROBOTS_CACHE[domain]
    try:
        url = f"https://{domain}/robots.txt"
        resp = await session.get(url, timeout=5, allow_redirects=True)
        if resp.status_code == 200:
            # SEC/DoS: 거대 robots.txt 메모리 폭주 방어 (256KB cap).
            content = resp.content if hasattr(resp, "content") else resp.text.encode()[:_ROBOTS_TXT_MAX_BYTES]
            if isinstance(content, bytes):
                content = content[:_ROBOTS_TXT_MAX_BYTES]
                text = content.decode("utf-8", errors="ignore").lower()
            else:
                text = content[:_ROBOTS_TXT_MAX_BYTES].lower()
            lines = text.splitlines()
            # FIX 2026-06-07: user-agent 블록 정확히 추적 — 이전: 다른 UA 블록 만나면 reset 안 됨.
            # robots.txt 규약: 빈 줄 또는 새 user-agent 만나면 이전 블록 종료.
            applies_to_us = False
            for line in lines:
                line = line.strip()
                if not line or line.startswith("#"):
                    # 빈 줄 또는 주석 = 블록 경계 candidate (RFC 9309 spec)
                    continue
                if line.startswith("user-agent:"):
                    ua = line.split(":", 1)[1].strip()
                    # 명시: 새 UA 블록 시작 → 이전 컨텍스트 정확히 재평가
                    applies_to_us = ua in ("*", "storescope")
                elif applies_to_us and line.startswith("disallow:"):
                    path = line.split(":", 1)[1].strip()
                    if path in ("/", "/products", "/products.json"):
                        _cache_set(_ROBOTS_CACHE, domain, False)
                        return False
    except Exception as exc:
        # robots.txt 접근 실패 = 제한 없음으로 간주 (보수적 fallback).
        # 단 unexpected 에러는 로깅 — DNS / SSL / 비정상 응답 추적용.
        logging.debug("robots.txt fetch failed %s: %s", domain, type(exc).__name__)
    _cache_set(_ROBOTS_CACHE, domain, True)
    return True


def _cache_set(cache: dict, key: str, value: bool) -> None:
    """캐시 사이즈 cap. 초과 시 가장 오래된 30% 절단 (FIFO 근사 — dict 삽입 순서)."""
    if len(cache) >= _ROBOTS_CACHE_MAX:
        # dict 삽입 순서 보장 (Python 3.7+) → 첫 30% 제거
        prune_n = int(_ROBOTS_CACHE_MAX * 0.3)
        for k in list(cache.keys())[:prune_n]:
            cache.pop(k, None)
    cache[key] = value


async def _fetch_page(
    session: AsyncSession,
    domain: str,
    page: int,
    semaphore: asyncio.Semaphore,
) -> tuple[list, int]:
    """단일 페이지 요청. 세마포어를 요청 단위로 취득/반납해 실효 동시 HTTP 수를 CONCURRENCY로 유지."""
    url = f"https://{domain}/products.json?limit={PAGE_LIMIT}&page={page}"
    async with semaphore:
        resp = await session.get(url, timeout=REQUEST_TIMEOUT, allow_redirects=True)
    return resp.json().get("products", []) if resp.status_code == 200 else [], resp.status_code


async def _fetch_one(
    session: AsyncSession,
    domain: str,
    semaphore: asyncio.Semaphore,
) -> CrawlResult:
    """페이지네이션 전체를 처리. 각 페이지 요청마다 세마포어를 독립적으로 경쟁해
    대형 스토어가 슬롯을 장기 점유하는 문제를 제거."""
    all_products: list = []
    try:
        if not await _is_crawl_allowed(session, domain):
            return CrawlResult(domain=domain, success=False, error="robots.txt disallow")

        for page in range(1, MAX_PAGES + 1):
            batch, status = await _fetch_page(session, domain, page, semaphore)

            if not batch:
                if page == 1:
                    return CrawlResult(domain=domain, success=False, error=f"HTTP {status}")
                break  # 첫 페이지 이후 빈 응답이면 수집 완료

            all_products.extend(batch)

            if len(batch) < PAGE_LIMIT:
                break  # 마지막 페이지 도달

        payload = json.dumps(
            {
                "domain": domain,
                "crawled_at": datetime.now(timezone.utc).isoformat(),
                "product_count": len(all_products),
                "products": all_products,
            },
            ensure_ascii=False,
            indent=2,
        )
        out_path = OUTPUT_DIR / f"{domain}.json"
        await asyncio.to_thread(out_path.write_text, payload)

        return CrawlResult(
            domain=domain,
            success=True,
            product_count=len(all_products),
            crawled_at=datetime.now(timezone.utc).isoformat(),
        )

    except Exception as exc:
        msg = str(exc)
        if "timed out" in msg.lower() or "timeout" in msg.lower():
            return CrawlResult(domain=domain, success=False, error="timeout")
        if "connection" in msg.lower():
            return CrawlResult(domain=domain, success=False, error=f"conn_err: {msg[:60]}")
        return CrawlResult(domain=domain, success=False, error=msg[:100])


async def _crawl_all_async(domains: list[str]) -> CrawlReport:
    """
    asyncio.Semaphore + AsyncSession 기반 병렬 크롤.
    asyncio.as_completed로 완료 순서대로 진행률 출력.
    """
    report = CrawlReport(
        total=len(domains),
        started_at=datetime.now(timezone.utc).isoformat(),
    )
    semaphore = asyncio.Semaphore(CONCURRENCY)
    done = 0

    async with AsyncSession(headers={"User-Agent": BOT_UA}) as session:
        tasks = [_fetch_one(session, d, semaphore) for d in domains]

        # as_completed: 완료된 순서대로 결과 수집 → 진행률 실시간 출력
        for coro in asyncio.as_completed(tasks):
            result = await coro
            report.results.append(asdict(result))

            done += 1
            if result.success:
                report.success += 1
                report.total_products += result.product_count
            else:
                report.failed += 1

            if done % 50 == 0 or done == len(domains):
                pct = done / len(domains) * 100
                print(
                    f"  [{done}/{len(domains)}] {pct:.0f}% | "
                    f"성공: {report.success} | 실패: {report.failed} | "
                    f"상품: {report.total_products:,}개"
                )

    report.finished_at = datetime.now(timezone.utc).isoformat()
    return report


def load_domains() -> list[str]:
    if not STORES_FILE.exists():
        raise FileNotFoundError(f"{STORES_FILE} 없음. discover_stores.py 먼저 실행하세요.")
    return [l.strip() for l in STORES_FILE.read_text().splitlines() if l.strip()]


def main():
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    domains = load_domains()
    print(f"크롤링 시작: {len(domains)}개 스토어")
    print(f"동시 요청: {CONCURRENCY}개 | 타임아웃: {REQUEST_TIMEOUT}초 | TLS: {IMPERSONATE}\n")

    start = time.time()
    report = asyncio.run(_crawl_all_async(domains))
    elapsed = time.time() - start

    REPORT_FILE.write_text(json.dumps(asdict(report), ensure_ascii=False, indent=2))

    rate = report.success / report.total * 100 if report.total else 0
    print(f"\n크롤링 완료 ({elapsed:.1f}초)")
    print(f"  성공: {report.success}/{report.total} ({rate:.1f}%)")
    print(f"  수집 상품 총계: {report.total_products:,}개")
    print(f"  평균 상품 수/스토어: {report.total_products / max(report.success, 1):.0f}개")
    print(f"  리포트: {REPORT_FILE}")


if __name__ == "__main__":
    main()
