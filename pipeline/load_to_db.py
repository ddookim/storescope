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
import os
import time
from pathlib import Path
from typing import Optional

import psycopg2
import psycopg2.extras
from psycopg2.extras import execute_values

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

    # 30일 평균 week_delta 조회 (비율 기반 trend_score 계산용)
    # trend_score = 이번 주 delta / 30일 평균 delta
    # > 1.0: 평균보다 빠르게 성장 (급상승 신호), < 1.0: 평균 이하
    cur.execute("""
        SELECT cluster_id, AVG(week_delta) AS avg_delta
        FROM trend_snapshots
        WHERE snapshot_at > NOW() - INTERVAL '30 days'
          AND week_delta > 0
        GROUP BY cluster_id
    """)
    avg_delta_30d: dict[int, float] = {row[0]: float(row[1]) for row in cur.fetchall()}

    snapshot_rows = []
    for chash, prods in clusters_data.items():
        cid         = hash_to_cluster_id[chash]
        store_count = len({p["domain"] for p in prods})
        delta       = store_count - prev.get(cid, 0)

        # 마스터플랜 W8 공식: trend_score = 7일 신규 스토어 수 / 30일 평균 신규 스토어 수
        avg = avg_delta_30d.get(cid, 0.0)
        trend_score = round(delta / avg, 4) if avg > 0 else (1.0 if delta > 0 else 0.0)

        snapshot_rows.append((cid, store_count, delta, trend_score))

    execute_values(cur, """
        INSERT INTO trend_snapshots
            (cluster_id, store_count, week_delta, trend_score, snapshot_at)
        VALUES %s
    """, snapshot_rows, template="(%s, %s, %s, %s, NOW())")


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
