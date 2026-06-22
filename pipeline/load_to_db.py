"""
StoreScope — 클러스터 데이터 DB 적재 (Bulk Upsert, 138k 스케일)
==================================================================
clusters.json + products.json → PostgreSQL

성능 설계:
  - 모든 INSERT를 execute_values Bulk Upsert (1,000건/배치)로 처리
  - product_clusters 매핑은 메모리 내 dict → N+1 SELECT 완전 제거
  - 트랜잭션 분리: 스토어/상품 페이즈와 클러스터 페이즈를 독립 커밋
    → 130k 처리 후 오류 시 완료된 페이즈는 보존, 실패 페이즈만 재실행

실행:
    python -m pipeline.load_to_db
"""

# concurrent.futures / ipaddress / socket / urllib — _deliver_webhooks 삭제로 dead (2026-06-07)
import json
import math
import os
import time
from datetime import datetime, timezone
from pathlib import Path

import psycopg2
import psycopg2.extras
from psycopg2.extras import execute_values

# ── Storm Score V2 알고리즘 상수 ─────────────────────────────────
# D+20 fix: 기존 trend_score = delta/avg_30d 의 6개 결함 fix.
# 공식: S = log₁₀(sc+1) × EMA(δ) × (1 + tanh(v)) / log₁₀(age+2)
#       × 0.3   if store_count < 3   (small count penalty)
_STORM_EMA_ALPHA          = 0.5   # 최근 1 step 50%, 4 step 누적 93.75%
_STORM_SMALL_COUNT_THRESH = 3     # store_count < 이 값이면 penalty
_STORM_SMALL_COUNT_FACTOR = 0.3   # ×0.3 penalty multiplier
_STORM_HISTORY_WEEKS      = 4     # EMA 윈도우 — 4주 데이터 안정 reading
_STORM_WINSOR_K           = 2.0   # outlier clamp: mean × k 초과는 mean × k 로 clip


def _winsorize_history(history: list[int]) -> list[float]:
    """평균 × _STORM_WINSOR_K 초과 outlier 를 cap.

    Kalman filter innovation gating 패턴 — 단발성 spike (e.g. [1,1,50,1]) 가
    EMA dominate 하는 결함 fix. 음수는 0 으로 clamp (week_delta 자체는 음수
    가능하지만 momentum 측정에는 비음수만 사용).
    """
    if not history:
        return []
    valid = [max(int(d), 0) for d in history]
    if all(v == 0 for v in valid):
        return [float(v) for v in valid]
    mean_h = sum(valid) / len(valid)
    threshold = mean_h * _STORM_WINSOR_K
    return [float(min(v, threshold)) for v in valid]


def _storm_score(
    store_count: int,
    delta_t: int,
    delta_history: list[int],
    age_days: float,
) -> float:
    """Storm Score V2 — momentum-based cluster ranking.

    Args:
        store_count: 현재 스토어 수
        delta_t: 이번 주 신규 스토어 증가 (현재 - 7일 전)
        delta_history: 최근 4주 week_delta (가장 오래된 first, 시간순)
        age_days: clusters.first_seen 부터 경과 일수

    Returns:
        Storm score (unbounded, 정렬 목적). 0 이상.

    Properties:
        - log₁₀(sc+1):     HN sublinear, 거대 클러스터 monopoly 차단
        - winsorize+EMA:   outlier clamp + 지수 평활화. 자율운항 Kalman gating 패턴
        - tanh(velocity):  가속도 boost ∈ [0, 2]. 급가속 = ×2, 정체 = ×1
        - log₁₀(age+2):    Reddit gravity, 오래된 클러스터 자연 demotion
        - small_penalty:   sc<3 noise filter (×0.3)
    """
    if store_count < 1 or not delta_history:
        return 0.0

    # Winsorize first (single-step spike → mean×K clamp), then EMA.
    clamped = _winsorize_history(delta_history)
    if not clamped:
        return 0.0

    # EMA momentum — most recent gets highest weight (alpha=0.5)
    ema: float = 0.0
    for d in clamped:
        ema = _STORM_EMA_ALPHA * d + (1.0 - _STORM_EMA_ALPHA) * ema

    # Velocity (acceleration: rate of delta change), with winsorized prev/current
    if len(clamped) >= 2:
        prev = max(clamped[-1], 0.0)
        # delta_t 도 winsor cap 적용 — single-spike 의 velocity 폭주 차단
        mean_h = sum(clamped) / len(clamped) if clamped else 0
        delta_t_w = min(max(float(delta_t), 0.0), max(mean_h * _STORM_WINSOR_K, prev))
        velocity = (delta_t_w - prev) / max(prev, 1.0)
    else:
        velocity = 0.0

    log_sc      = math.log10(store_count + 1)
    log_age     = math.log10(max(age_days, 0.1) + 2.0)
    accel_boost = 1.0 + math.tanh(velocity)  # ∈ [0, 2]

    score = (log_sc * ema * accel_boost) / log_age

    if store_count < _STORM_SMALL_COUNT_THRESH:
        score *= _STORM_SMALL_COUNT_FACTOR

    return round(max(score, 0.0), 4)

PRODUCTS_DIR  = Path("data/products")
CLUSTERS_FILE = Path("data/clusters.json")

DB_URL     = os.environ.get("DATABASE_URL", "postgresql://localhost/storescope")
BATCH_SIZE = 1_000  # execute_values 배치 크기


def get_conn():
    return psycopg2.connect(DB_URL)


# ── Phase 1: 도메인 수집 (경량 1차 패스) ────────────────────────
def _collect_domains(clusters_data: dict) -> set[str]:
    """products.json 파일에서 도메인만 수집. 상품 데이터는 읽지 않음."""
    domains: set[str] = set()
    for chash, prods in clusters_data.items():
        for p in prods:
            domains.add(p["domain"])
    for file in PRODUCTS_DIR.glob("*.json"):
        try:
            raw = file.read_text()
            domain = json.loads(raw[:200]).get("domain")  # 앞 200바이트만 파싱
            if domain:
                domains.add(domain)
        except Exception:
            pass
    return domains


def _iter_product_rows(clusters_data: dict):
    """
    ARCH FIX: 전체 로드 → 파일별 스트리밍 제너레이터.
    메모리: O(파일 1개) — 10k 스토어×2.5k 상품=25M rows OOM 방지.
    """
    hash_by_key: dict[tuple, str] = {}
    for prods in clusters_data.values():
        for p in prods:
            hash_by_key[(p["product_id"], p["domain"])] = p["image_hash"]

    for file in sorted(PRODUCTS_DIR.glob("*.json")):
        try:
            data = json.loads(file.read_text())
        except Exception as exc:
            print(f"  [경고] 스킵: {file.name} — {exc}", flush=True)
            continue
        domain = data["domain"]
        for p in data.get("products", []):
            variants  = p.get("variants", [])
            prices    = [float(v["price"]) for v in variants if v.get("price")]
            image_url = (p.get("images") or [{}])[0].get("src", "") or None
            img_hash  = hash_by_key.get((p["id"], domain)) or None
            yield (
                domain,
                p["id"],
                p.get("title", ""),
                p.get("handle", ""),
                min(prices) if prices else None,
                max(prices) if prices else None,
                image_url,
                img_hash,
            )


# ── Phase 2: 스토어 Bulk Upsert ─────────────────────────────────
def _bulk_upsert_stores(cur, domains: set[str]) -> dict[str, int]:
    """domain → store_id 매핑 반환."""
    rows = execute_values(cur, """
        INSERT INTO stores (domain)
        VALUES %s
        ON CONFLICT (domain) DO UPDATE SET last_crawled = NOW()
        RETURNING id, domain
    """, [(d,) for d in sorted(domains)], fetch=True)
    return {domain: sid for sid, domain in rows}


# ── Phase 3: 상품 Bulk Upsert (스트리밍) ────────────────────────
def _bulk_upsert_products(
    cur,
    product_rows,  # Iterable — 제너레이터 또는 리스트 모두 허용
    domain_to_id: dict[str, int],
) -> dict[tuple, int]:
    """(store_id, shopify_id) → product_id 매핑 반환. 스트리밍 입력 처리."""
    all_product_ids: dict[tuple, int] = {}
    batch: list[tuple] = []
    total_done = 0

    def _flush(b: list[tuple]) -> None:
        db_rows = execute_values(cur, """
            INSERT INTO products
                (store_id, shopify_id, title, handle,
                 price_min, price_max, image_url, image_hash)
            VALUES %s
            ON CONFLICT (store_id, shopify_id) DO UPDATE SET
                title      = EXCLUDED.title,
                price_min  = EXCLUDED.price_min,
                price_max  = EXCLUDED.price_max,
                image_hash = EXCLUDED.image_hash,
                last_seen  = NOW()
            RETURNING id, store_id, shopify_id
        """, [
            (domain_to_id[r[0]], r[1], r[2], r[3], r[4], r[5], r[6], r[7])
            for r in b if r[0] in domain_to_id
        ], fetch=True)
        for pid, sid, shopify_id in db_rows:
            all_product_ids[(sid, shopify_id)] = pid

    for row in product_rows:
        batch.append(row)
        if len(batch) >= BATCH_SIZE:
            _flush(batch)
            total_done += len(batch)
            batch = []
            if total_done % (BATCH_SIZE * 10) == 0:
                print(f"  상품 {total_done:,}건 처리 중...")

    if batch:
        _flush(batch)
        total_done += len(batch)

    print(f"  상품 {total_done:,}건 완료")
    return all_product_ids


# ── Phase 4: 클러스터 Bulk Upsert ───────────────────────────────
def _bulk_upsert_clusters(cur, clusters_data: dict) -> dict[str, int]:
    """cluster_hash → cluster_id 매핑 반환."""
    rows = execute_values(cur, """
        INSERT INTO clusters (cluster_hash, store_count, product_count, last_updated)
        VALUES %s
        ON CONFLICT (cluster_hash) DO UPDATE SET
            store_count   = EXCLUDED.store_count,
            product_count = EXCLUDED.product_count,
            last_updated  = NOW()
        RETURNING id, cluster_hash
    """, [
        (chash, len({p["domain"] for p in prods}), len(prods))
        for chash, prods in clusters_data.items()
    ], template="(%s, %s, %s, NOW())", fetch=True)
    return {chash: cid for cid, chash in rows}


# ── Phase 5: 트렌드 스냅샷 Bulk Insert ──────────────────────────
def _bulk_insert_snapshots(
    cur,
    clusters_data: dict,
    hash_to_cluster_id: dict[str, int],
) -> None:
    # ARCH FIX: 7일 전 스냅샷과 비교 — "직전 실행" 기준이 아닌 진짜 week_delta
    # DISTINCT ON으로 각 cluster_id의 7일 전~14일 전 구간 중 가장 최근 스냅샷 사용
    cur.execute("""
        SELECT DISTINCT ON (cluster_id) cluster_id, store_count
        FROM trend_snapshots
        WHERE snapshot_at <= NOW() - INTERVAL '7 days'
        ORDER BY cluster_id, snapshot_at DESC
    """)
    prev: dict[int, int] = {row[0]: row[1] for row in cur.fetchall()}

    # 30일 평균 week_delta 조회 (기존 trend_score backwards-compat 용)
    cur.execute("""
        SELECT cluster_id, AVG(week_delta) AS avg_delta
        FROM trend_snapshots
        WHERE snapshot_at > NOW() - INTERVAL '30 days'
          AND week_delta > 0
        GROUP BY cluster_id
    """)
    avg_delta_30d: dict[int, float] = {row[0]: float(row[1]) for row in cur.fetchall()}

    # Storm Score V2: 최근 4주 delta 시계열 (가장 오래된 first)
    # ARRAY_AGG with ORDER BY → window function 없이 single pass
    cur.execute("""
        SELECT cluster_id,
               ARRAY_AGG(week_delta ORDER BY snapshot_at ASC) AS deltas
        FROM trend_snapshots
        WHERE snapshot_at > NOW() - INTERVAL '%s weeks'
        GROUP BY cluster_id
    """ % _STORM_HISTORY_WEEKS)
    delta_history: dict[int, list[int]] = {row[0]: list(row[1]) for row in cur.fetchall()}

    # cluster age (clusters.first_seen → days)
    cur.execute("SELECT id, first_seen FROM clusters")
    now_utc = datetime.now(timezone.utc)
    cluster_age_days: dict[int, float] = {}
    for row in cur.fetchall():
        fs = row[1]
        if fs is None:
            cluster_age_days[row[0]] = 0.0
        else:
            if fs.tzinfo is None:
                fs = fs.replace(tzinfo=timezone.utc)
            cluster_age_days[row[0]] = (now_utc - fs).total_seconds() / 86400.0

    snapshot_rows = []
    for chash, prods in clusters_data.items():
        cid         = hash_to_cluster_id[chash]
        store_count = len({p["domain"] for p in prods})
        delta       = store_count - prev.get(cid, 0)

        # backwards-compat trend_score (delta / 30d-avg) — weekly_digest 이메일 의존
        avg = avg_delta_30d.get(cid, 0.0)
        trend_score = round(delta / avg, 4) if avg > 0 else (1.0 if delta > 0 else 0.0)

        # Storm Score V2 — /trending API 의 rising sort
        momentum = _storm_score(
            store_count=store_count,
            delta_t=delta,
            delta_history=delta_history.get(cid, []),
            age_days=cluster_age_days.get(cid, 0.0),
        )

        snapshot_rows.append((cid, store_count, delta, trend_score, momentum))

    execute_values(cur, """
        INSERT INTO trend_snapshots
            (cluster_id, store_count, week_delta, trend_score, momentum_score, snapshot_at)
        VALUES %s
    """, snapshot_rows, template="(%s, %s, %s, %s, %s, NOW())")


# ── Phase 6: product_clusters Bulk Insert ───────────────────────
def _bulk_insert_product_clusters(
    cur,
    clusters_data: dict,
    domain_to_id: dict[str, int],
    all_product_ids: dict[tuple, int],
    hash_to_cluster_id: dict[str, int],
) -> None:
    pc_rows: list[tuple] = []
    missing = 0

    for chash, prods in clusters_data.items():
        cid = hash_to_cluster_id[chash]
        for p in prods:
            sid = domain_to_id.get(p["domain"])
            pid = all_product_ids.get((sid, p["product_id"])) if sid else None
            if pid is None:
                missing += 1
                continue
            pc_rows.append((pid, cid))

    if missing:
        print(f"  [경고] 클러스터 매핑 누락 {missing}건 (크롤 실패 스토어 — 정상)")

    for i in range(0, len(pc_rows), BATCH_SIZE):
        execute_values(cur, """
            INSERT INTO product_clusters (product_id, cluster_id)
            VALUES %s
            ON CONFLICT DO NOTHING
        """, pc_rows[i : i + BATCH_SIZE])

    print(f"  product_clusters {len(pc_rows):,}건 적재")


# ── Phase 7: TOP 5 확인 쿼리 ────────────────────────────────────
def _print_top5(conn) -> None:
    print("\n=== DB 트렌드 TOP 5 ===")
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        # DISTINCT ON (c.cluster_hash): JOIN products 중복 뻥튀기 방지
        # 내부 ORDER BY → 클러스터당 최저가 대표 상품 1개 선택
        # 외부 ORDER BY → store_count 내림차순 정렬
        cur.execute("""
            SELECT *
            FROM (
                SELECT DISTINCT ON (c.cluster_hash)
                    c.cluster_hash,
                    c.store_count,
                    c.product_count,
                    ts.week_delta,
                    p.title     AS rep_title,
                    p.price_min AS rep_price
                FROM clusters c
                JOIN trend_snapshots ts  ON ts.cluster_id = c.id
                JOIN product_clusters pc ON pc.cluster_id = c.id
                JOIN products p          ON p.id = pc.product_id
                WHERE c.store_count >= 2
                ORDER BY c.cluster_hash, p.price_min ASC NULLS LAST
            ) sub
            ORDER BY store_count DESC, product_count DESC
            LIMIT 5
        """)
        rows = cur.fetchall()

    for i, row in enumerate(rows, 1):
        delta_str = f"+{row['week_delta']}" if row["week_delta"] > 0 else str(row["week_delta"])
        print(f"#{i} [{row['store_count']}개 스토어 | delta {delta_str}]")
        print(f"   {row['rep_title'][:55]} — ${row['rep_price']}")


# DELETED 2026-06-07: _deliver_webhooks (구 Pro tier webhook subscription delivery)
# 의존성: webhook_subscriptions 테이블 — migrations/2026_06_04_drop_dead_tables.sql 로 삭제됨.
# 호출 유지 시 다음 파이프라인 실행에서 RuntimeError (relation does not exist).
# 첫 Pro 고객 명시 요청 시 복구 (git revert <SHA> + webhook_subscriptions 테이블 재생성).


# ── 진입점 ──────────────────────────────────────────────────────
def run():
    if not CLUSTERS_FILE.exists():
        print("clusters.json 없음. pipeline.cluster_products 먼저 실행하세요.")
        return

    t0 = time.time()
    clusters_data = json.loads(CLUSTERS_FILE.read_text())

    print("도메인 수집 중...")
    domains = _collect_domains(clusters_data)
    print(f"  스토어 {len(domains)}개 | 클러스터 {len(clusters_data)}개")

    conn = get_conn()
    try:
        # ── 트랜잭션 1: 스토어 + 상품 ───────────────────────
        print("[ 트랜잭션 1 ] 스토어 + 상품")
        with conn:
            with conn.cursor() as cur:
                print("  스토어 적재 중...")
                domain_to_id = _bulk_upsert_stores(cur, domains)
                print(f"  {len(domain_to_id)}개 완료")

                print("  상품 스트리밍 적재 중...")
                all_product_ids = _bulk_upsert_products(
                    cur, _iter_product_rows(clusters_data), domain_to_id
                )

                print(f"  {len(all_product_ids):,}개 완료")
                print("  store product_count 업데이트 중...")
                cur.execute("""
                    UPDATE stores s SET product_count = sub.cnt
                    FROM (
                        SELECT store_id, COUNT(*) AS cnt
                        FROM products
                        GROUP BY store_id
                    ) sub
                    WHERE s.id = sub.store_id
                """)
        print("  커밋 완료\n")

        # ── 트랜잭션 2: 클러스터 + 스냅샷 + 매핑 ───────────
        # 트랜잭션 1이 커밋된 이후 실행. 이 페이즈 실패 시 상품 데이터는 보존됨.
        print("[ 트랜잭션 2 ] 클러스터 + 스냅샷 + 매핑")
        with conn:
            with conn.cursor() as cur:
                print("  클러스터 적재 중...")
                hash_to_cluster_id = _bulk_upsert_clusters(cur, clusters_data)
                print(f"  {len(hash_to_cluster_id)}개 완료")

                print("  트렌드 스냅샷 적재 중...")
                _bulk_insert_snapshots(cur, clusters_data, hash_to_cluster_id)

                print("  클러스터 매핑 중...")
                _bulk_insert_product_clusters(
                    cur, clusters_data, domain_to_id, all_product_ids, hash_to_cluster_id
                )
        print("  커밋 완료\n")

        _print_top5(conn)
        # _deliver_webhooks() 호출 제거 (2026-06-07) — 함수 정의 자체 삭제됨.
        elapsed = time.time() - t0
        print(f"완료: {elapsed:.1f}초")

    finally:
        conn.close()


if __name__ == "__main__":
    run()
