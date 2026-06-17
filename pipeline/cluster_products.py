"""
StoreScope — pHash 기반 상품 클러스터링 (Production-Grade v4)
=================================================================
v4 파이프라인 변경사항 (BK-Tree·트렌드·로드 로직 완전 유지):

  1. curl_cffi 예외로 tenacity 완전 동기화
     - _CFFI_NET_ERROR: curl_cffi.requests.errors.RequestsError 명시적 임포트
     - _is_retryable: 타임아웃·연결오류·429·5xx 모두 올바르게 판별
     - aiohttp 잔재 0

  2. 20만 코루틴 일괄 생성(OOM) 폐기 → 역할 분리 2단계 큐 구조
     ┌─────────────────────────────────────────────────────────────┐
     │  work_queue (unbounded, ProductRecord)                      │
     │      ↓  NUM_DL_WORKERS개 비동기 다운로드 워커 (create_task) │
     │  hash_queue (maxsize=HASH_QUEUE_SIZE, (record, bytes))      │
     │      ↓  CPU_WORKERS개 해시 소비자 워커 (create_task)        │
     │  results                                                    │
     └─────────────────────────────────────────────────────────────┘

  3. Bounded hash_queue 백프레셔
     - hash_queue 포화 시 download_worker의 put()이 자동 대기
     - 메모리 상한 = HASH_QUEUE_SIZE × 평균 이미지 크기 ≈ 500 × 50KB = 25MB
     - 다운로드 속도 > 해시 속도여도 OOM 원천 차단
"""

import asyncio
import io
import json
import logging
import multiprocessing
import time
from collections import defaultdict
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Optional

import imagehash
from PIL import Image
from curl_cffi.requests import AsyncSession
from tenacity import (
    retry,
    retry_if_exception,
    stop_after_attempt,
    wait_exponential,
)

# ── 상수 ──────────────────────────────────────────────────────────────────────

PRODUCTS_DIR  = Path("data/products")
CLUSTERS_FILE = Path("data/clusters.json")
TRENDING_FILE = Path("data/trending.json")
FAILED_FILE   = Path("data/failed_downloads.json")

HASH_DISTANCE_THRESHOLD = 6
NUM_DL_WORKERS          = 12    # 비동기 다운로드 워커 수 (I/O Producer) — CDN 레이트리밋 방지를 위해 낮게 설정
HASH_QUEUE_SIZE         = 200   # Bounded Queue 크기: 포화 시 다운로드 자동 대기
CPU_WORKERS             = max(1, multiprocessing.cpu_count() - 1)  # 해시 소비자 수
DOWNLOAD_TIMEOUT        = 10
MAX_PRODUCTS            = 200_000

_IMG_HEADERS = {
    "Accept": "image/avif,image/webp,image/apng,image/*,*/*;q=0.8",
}

log = logging.getLogger(__name__)


# ── 데이터 모델 ────────────────────────────────────────────────────────────────

@dataclass
class ProductRecord:
    domain: str
    product_id: int
    title: str
    price: str
    image_url: str
    image_hash: str = ""
    handle: str = ""


# ── 1. BK-Tree (변경 없음) ────────────────────────────────────────────────────

class BKTree:
    """Burkhard-Keller Tree for pHash Hamming distance nearest-neighbor search."""

    def __init__(self) -> None:
        self._nodes: list[tuple[int, list[int]]] = []
        self._children: list[dict[int, int]] = []

    @staticmethod
    def _hamming(a: int, b: int) -> int:
        return bin(a ^ b).count("1")

    @staticmethod
    def _hex_to_int(h: str) -> int:
        return int(h, 16)

    def add(self, hex_hash: str, record_idx: int) -> None:
        h = self._hex_to_int(hex_hash)
        if not self._nodes:
            self._nodes.append((h, [record_idx]))
            self._children.append({})
            return
        node_id = 0
        while True:
            node_h, indices = self._nodes[node_id]
            d = self._hamming(h, node_h)
            if d == 0:
                indices.append(record_idx)
                return
            if d in self._children[node_id]:
                node_id = self._children[node_id][d]
            else:
                new_id = len(self._nodes)
                self._nodes.append((h, [record_idx]))
                self._children.append({})
                self._children[node_id][d] = new_id
                return

    def search(self, hex_hash: str, threshold: int) -> list[int]:
        if not self._nodes:
            return []
        h = self._hex_to_int(hex_hash)
        results: list[int] = []
        stack = [0]
        while stack:
            node_id = stack.pop()
            node_h, indices = self._nodes[node_id]
            d = self._hamming(h, node_h)
            if d <= threshold:
                results.extend(indices)
            for edge_dist, child_id in self._children[node_id].items():
                if abs(edge_dist - d) <= threshold:
                    stack.append(child_id)
        return results


# ── 2. CPU 워커 (변경 없음 — 모듈 최상위 필수) ───────────────────────────────

def compute_phash_worker(image_bytes: bytes) -> Optional[str]:
    """ProcessPoolExecutor에서 실행. GIL 해방, pickle 가능하도록 모듈 최상위 정의."""
    try:
        img = Image.open(io.BytesIO(image_bytes)).convert("RGB")
        return str(imagehash.phash(img))
    except Exception:
        return None


# ── 3. 다운로드 레이어: curl_cffi + tenacity (에러 처리 완전 동기화) ───────────
#
# 핵심: curl_cffi.requests.errors.RequestsError를 직접 임포트해
# aiohttp.ClientResponseError 등 이전 라이브러리 잔재를 완전히 제거한다.
# _is_retryable은 세 가지 범주를 정확히 판별:
#   (A) 명시적 HTTP 429·5xx  → _RetryableHttpError
#   (B) curl_cffi 네트워크 에러 → _CFFI_NET_ERROR (RequestsError)
#   (C) OS 레벨 타임아웃·연결 에러 → OSError / TimeoutError

try:
    from curl_cffi.requests.errors import RequestsError as _CFFI_NET_ERROR
except ImportError:
    # curl_cffi 버전에 따라 경로가 다를 수 있음 — fallback
    try:
        from curl_cffi import CurlError as _CFFI_NET_ERROR  # type: ignore[assignment]
    except ImportError:
        _CFFI_NET_ERROR = OSError  # type: ignore[assignment,misc]


class _RetryableHttpError(Exception):
    """429 및 5xx HTTP 상태 코드를 Tenacity가 인식하는 재시도 예외로 래핑."""
    def __init__(self, status: int, retry_after: float = 0.0) -> None:
        self.status = status
        self.retry_after = retry_after  # Retry-After 헤더값(초), 없으면 0
        super().__init__(f"HTTP {status}")


def _is_retryable(exc: BaseException) -> bool:
    """
    Tenacity 재시도 판별 함수.
      (A) _RetryableHttpError  — 429·500·502·503·504
      (B) _CFFI_NET_ERROR      — curl_cffi RequestsError (연결 실패·DNS 오류 등)
      (C) OSError·TimeoutError — OS 레벨 소켓/타임아웃 오류
    """
    if isinstance(exc, _RetryableHttpError):
        return True
    return isinstance(exc, (_CFFI_NET_ERROR, OSError, TimeoutError))


def _wait_with_retry_after(retry_state) -> float:
    """
    ARCH FIX: Retry-After 헤더값이 있으면 그 값을 대기 시간으로 사용.
    없으면 지수 백오프(max=60초).
    """
    exc = retry_state.outcome.exception()
    if isinstance(exc, _RetryableHttpError) and exc.retry_after > 0:
        return min(exc.retry_after, 120.0)
    multiplier = 1
    exp = wait_exponential(multiplier=multiplier, min=2, max=60)
    return exp(retry_state)


@retry(
    retry=retry_if_exception(_is_retryable),
    stop=stop_after_attempt(4),
    wait=_wait_with_retry_after,
    reraise=True,
)
async def _download(session: AsyncSession, url: str) -> bytes:
    """
    curl_cffi AsyncSession(chrome120 TLS)으로 이미지 다운로드.
    429·5xx → _RetryableHttpError 발생 → Retry-After 헤더 존중 후 재시도.
    그 외 4xx → ValueError 발생 → 재시도 없이 즉시 실패 처리.
    """
    resp = await session.get(url, headers=_IMG_HEADERS, timeout=DOWNLOAD_TIMEOUT)
    if resp.status_code == 429:
        retry_after = float(resp.headers.get("retry-after", 0) or 0)
        raise _RetryableHttpError(429, retry_after=retry_after)
    if resp.status_code in {500, 502, 503, 504}:
        raise _RetryableHttpError(resp.status_code)
    if resp.status_code != 200:
        raise ValueError(f"HTTP {resp.status_code}")
    return resp.content


# ── 4. 생산자-소비자 파이프라인 (2단계 큐) ────────────────────────────────────

def _log_progress(
    done: int, total: int, n_hashed: int, n_failed: int, start: float
) -> None:
    if done % 1000 == 0 or done == total:
        elapsed = time.time() - start
        rate = done / elapsed if elapsed else 0
        eta = (total - done) / rate if rate else 0
        print(
            f"  [{done:,}/{total:,}] "
            f"해시완료: {n_hashed:,} | 실패: {n_failed:,} | "
            f"속도: {rate:.0f}개/s | ETA: {eta:.0f}초",
            flush=True,
        )


async def process_all(
    records: list[ProductRecord],
    process_pool: ProcessPoolExecutor,
) -> tuple[list[ProductRecord], list[dict]]:
    """
    2단계 Producer-Consumer 파이프라인:

      Stage 1 — I/O Producer (NUM_DL_WORKERS개 asyncio 태스크)
        work_queue에서 레코드를 꺼내 curl_cffi로 이미지를 다운로드한다.
        다운로드된 (record, bytes)를 hash_queue에 put한다.
        hash_queue가 HASH_QUEUE_SIZE 개를 초과하면 put()이 자동 대기 → 백프레셔.
        다운로드 실패 시 즉시 done_count 증가 + failed 기록.

      Stage 2 — CPU Consumer (CPU_WORKERS개 asyncio 태스크)
        hash_queue에서 (record, bytes)를 꺼내 ProcessPoolExecutor로 pHash를 계산한다.
        run_in_executor는 이벤트 루프를 블로킹하지 않으므로 Stage 1과 진짜 병렬 실행.
        완료 시 done_count 증가 + hashed/failed 기록.

      종료 프로토콜:
        1. records 전부 work_queue에 투입
        2. 다운로드 워커당 None 센티넬 1개 → 워커 루프 탈출
        3. asyncio.gather로 모든 다운로드 완료 대기
        4. 해시 소비자당 None 센티넬 1개 → 소비자 루프 탈출
        5. asyncio.gather로 모든 해시 계산 완료 대기
    """
    # work_queue: records 배분 (크기 작음, unbounded 허용)
    work_queue: asyncio.Queue[Optional[ProductRecord]] = asyncio.Queue()

    # hash_queue: 다운로드된 이미지 바이트 버퍼 (BOUNDED → 백프레셔 핵심)
    hash_queue: asyncio.Queue[Optional[tuple]] = asyncio.Queue(maxsize=HASH_QUEUE_SIZE)

    lock  = asyncio.Lock()
    loop  = asyncio.get_running_loop()
    hashed: list[ProductRecord] = []
    failed: list[dict] = []
    done_count = [0]   # list로 감싸 클로저 내 재할당 없이 수정
    start = time.time()
    total = len(records)

    async with AsyncSession(impersonate="chrome120") as session:

        # ── Stage 2: 해시 소비자 (CPU Bound) ─────────────────────────────
        async def hash_consumer() -> None:
            while True:
                item = await hash_queue.get()
                if item is None:          # 종료 센티넬
                    return

                record, img_bytes = item
                try:
                    hash_val: Optional[str] = await loop.run_in_executor(
                        process_pool, compute_phash_worker, img_bytes
                    )
                except Exception as exc:
                    async with lock:
                        done_count[0] += 1
                        failed.append({
                            "domain": record.domain,
                            "url": record.image_url,
                            "error": f"phash_error: {exc}",
                        })
                        _log_progress(done_count[0], total, len(hashed), len(failed), start)
                    continue

                async with lock:
                    done_count[0] += 1
                    if hash_val:
                        record.image_hash = hash_val
                        hashed.append(record)
                    else:
                        failed.append({
                            "domain": record.domain,
                            "url": record.image_url,
                            "error": "phash_returned_none",
                        })
                    _log_progress(done_count[0], total, len(hashed), len(failed), start)

        # ── Stage 1: 다운로드 워커 (I/O Bound) ───────────────────────────
        async def download_worker() -> None:
            while True:
                record = await work_queue.get()
                if record is None:        # 종료 센티넬
                    return

                try:
                    img_bytes = await _download(session, record.image_url)
                    # hash_queue 포화 시 이 지점에서 자동 대기 → 백프레셔
                    await hash_queue.put((record, img_bytes))
                except Exception as exc:
                    # 다운로드 실패: hash 단계를 거치지 않으므로 여기서 카운트
                    async with lock:
                        done_count[0] += 1
                        failed.append({
                            "domain": record.domain,
                            "url": record.image_url,
                            "error": str(exc)[:150],
                        })
                        _log_progress(done_count[0], total, len(hashed), len(failed), start)

        # ── 워커 기동 ─────────────────────────────────────────────────────
        # 해시 소비자 먼저 대기 상태로 시작 (hash_queue가 비어있어 즉시 대기)
        hash_consumers = [asyncio.create_task(hash_consumer())   for _ in range(CPU_WORKERS)]
        # 다운로드 워커 시작 (work_queue가 채워지기 전까지 대기)
        dl_workers     = [asyncio.create_task(download_worker()) for _ in range(NUM_DL_WORKERS)]

        # ── 레코드 투입 (work_queue는 unbounded이므로 블로킹 없음) ─────────
        for record in records:
            await work_queue.put(record)

        # ── 다운로드 워커 종료 신호: 워커당 None 1개 ─────────────────────
        for _ in range(NUM_DL_WORKERS):
            await work_queue.put(None)

        # ── 모든 다운로드 완료 대기 ───────────────────────────────────────
        await asyncio.gather(*dl_workers)

        # ── 해시 소비자 종료 신호: 소비자당 None 1개 ─────────────────────
        # 다운로드 워커가 모두 끝난 후에만 실행 → hash_queue에 잔여 데이터 없음 보장
        for _ in range(CPU_WORKERS):
            await hash_queue.put(None)

        # ── 모든 해시 계산 완료 대기 ──────────────────────────────────────
        await asyncio.gather(*hash_consumers)

    return hashed, failed


# ── 5. BK-Tree 기반 클러스터링 (변경 없음) ────────────────────────────────────

def cluster_by_bktree(records: list[ProductRecord]) -> dict[str, list[dict]]:
    n = len(records)
    tree = BKTree()
    parent = list(range(n))

    def find(x: int) -> int:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(x: int, y: int) -> None:
        parent[find(x)] = find(y)

    print(f"  BK-Tree 구축 중 ({n:,}개)...", flush=True)
    t = time.time()
    for i, r in enumerate(records):
        tree.add(r.image_hash, i)
    print(f"  완료: {time.time() - t:.1f}초", flush=True)

    print(f"  근접 이웃 검색 중 (Hamming ≤ {HASH_DISTANCE_THRESHOLD})...", flush=True)
    t = time.time()
    merge_count = 0
    for i, r in enumerate(records):
        for j in tree.search(r.image_hash, HASH_DISTANCE_THRESHOLD):
            if j != i and find(i) != find(j):
                union(i, j)
                merge_count += 1
        if (i + 1) % 10_000 == 0:
            print(f"  [{i+1:,}/{n:,}] 누적 병합: {merge_count:,}개", flush=True)
    print(f"  완료: {merge_count:,}개 병합 | {time.time() - t:.1f}초", flush=True)

    clusters_raw: dict[int, list[int]] = defaultdict(list)
    for i in range(n):
        clusters_raw[find(i)].append(i)

    result: dict[str, list[dict]] = {}
    for root, members in clusters_raw.items():
        if len(members) < 2:
            continue
        if len({records[i].domain for i in members}) < 2:
            continue
        result[records[root].image_hash] = [asdict(records[i]) for i in members]

    return result


# ── 6. 트렌드 계산 (변경 없음) ────────────────────────────────────────────────

def compute_trending(clusters: dict) -> list[dict]:
    trending = []
    for cluster_hash, products in clusters.items():
        domains = list({p["domain"] for p in products})
        prices = [float(p["price"] or 0) for p in products]
        rep = min(products, key=lambda p: float(p["price"] or 0))
        trending.append({
            "cluster_id": cluster_hash,
            "store_count": len(domains),
            "product_count": len(products),
            "domains": domains,
            "representative_title": rep["title"],
            "representative_price": rep["price"],
            "representative_image": rep["image_url"],
            "price_range": {"min": min(prices), "max": max(prices)},
        })
    return sorted(trending, key=lambda x: x["store_count"], reverse=True)


# ── 7. 데이터 로드 (변경 없음) ────────────────────────────────────────────────

def extract_records(products_dir: Path) -> list[ProductRecord]:
    records: list[ProductRecord] = []
    skipped_files: list[tuple[str, str]] = []
    for file in sorted(products_dir.glob("*.json")):
        try:
            data = json.loads(file.read_text())
            domain = data["domain"]
            for p in data.get("products", []):
                images = p.get("images", [])
                if not images:
                    continue
                image_url = images[0].get("src", "")
                if not image_url:
                    continue
                image_url = image_url.split("?")[0]
                variants = p.get("variants", [])
                price = variants[0].get("price", "0") if variants else "0"
                records.append(ProductRecord(
                    domain=domain,
                    product_id=p["id"],
                    title=p.get("title", ""),
                    price=price,
                    image_url=image_url,
                    handle=p.get("handle", ""),
                ))
                if len(records) >= MAX_PRODUCTS:
                    return records
        except (json.JSONDecodeError, KeyError, TypeError) as exc:
            # FIX 2026-06-07: silent data loss 방지 → 명시 누적 + 로깅.
            # 이전: bare `except Exception: continue` = 스토어 전체 silently drop.
            skipped_files.append((file.name, type(exc).__name__))
            logging.warning("extract_records skip %s: %s: %s", file.name, type(exc).__name__, exc)
    if skipped_files:
        logging.warning("extract_records skipped %d files: %s",
                        len(skipped_files), [f for f, _ in skipped_files[:10]])
    return records


# ── 8. 진입점 ────────────────────────────────────────────────────────────────

async def async_main() -> None:
    logging.basicConfig(level=logging.WARNING)
    print("=== StoreScope: pHash 클러스터링 (Production-Grade v4) ===\n")

    records = extract_records(PRODUCTS_DIR)
    print(f"로드된 상품: {len(records):,}개\n")
    if not records:
        print("처리할 상품이 없습니다. crawl_products를 먼저 실행하세요.")
        return

    print(
        f"이미지 다운로드 + pHash 계산 시작\n"
        f"  다운로드 워커: {NUM_DL_WORKERS}개 (asyncio I/O, create_task)\n"
        f"  해시 소비자:   {CPU_WORKERS}개 (ProcessPoolExecutor CPU)\n"
        f"  hash_queue:    maxsize={HASH_QUEUE_SIZE} (백프레셔)\n"
        f"  TLS:           chrome120 (curl_cffi)\n"
    )

    t_start = time.time()
    with ProcessPoolExecutor(max_workers=CPU_WORKERS) as process_pool:
        hashed_records, failed_records = await process_all(records, process_pool)
    elapsed = time.time() - t_start

    print(
        f"\n파이프라인 완료: {elapsed:.1f}초\n"
        f"  해시 성공: {len(hashed_records):,}개\n"
        f"  실패:      {len(failed_records):,}개"
    )

    if failed_records:
        FAILED_FILE.parent.mkdir(parents=True, exist_ok=True)
        FAILED_FILE.write_text(json.dumps(failed_records, ensure_ascii=False, indent=2))
        print(f"  → Dead Letter Queue: {FAILED_FILE} ({len(failed_records):,}건)")

    if not hashed_records:
        print("해시된 상품이 없어 클러스터링을 건너뜁니다.")
        return

    print(f"\n클러스터링 중 ({len(hashed_records):,}개 상품)...")
    clusters = cluster_by_bktree(hashed_records)
    print(f"멀티스토어 클러스터: {len(clusters):,}개")

    trending = compute_trending(clusters)

    CLUSTERS_FILE.parent.mkdir(parents=True, exist_ok=True)
    CLUSTERS_FILE.write_text(json.dumps(clusters, ensure_ascii=False, indent=2))
    TRENDING_FILE.write_text(json.dumps(trending, ensure_ascii=False, indent=2))

    print(f"\n=== 상위 트렌드 상품 ===")
    for i, item in enumerate(trending[:5], 1):
        stores_preview = ", ".join(item["domains"][:3])
        if len(item["domains"]) > 3:
            stores_preview += f" ... (+{len(item['domains']) - 3}개)"
        print(
            f"\n#{i} [{item['store_count']}개 스토어 | {item['product_count']}개 상품]\n"
            f"   제목:   {item['representative_title'][:60]}\n"
            f"   가격대: ${item['price_range']['min']:.2f} ~ ${item['price_range']['max']:.2f}\n"
            f"   스토어: {stores_preview}"
        )

    print(f"\n저장 완료\n  {CLUSTERS_FILE}\n  {TRENDING_FILE}")


def main() -> None:
    asyncio.run(async_main())


if __name__ == "__main__":
    main()
