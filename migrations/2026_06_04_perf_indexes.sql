-- StoreScope migration: Performance indexes (Step 3-4 acceleration)
-- Date: 2026-06-04
-- Purpose:
--   1. /trending 의 store_count 필터 Seq Scan 제거
--      EXPLAIN ANALYZE 측정: 1595 row 풀스캔 → 인덱스 사용 시 ~10배 빠름
--   2. /export 및 다중 정렬 시 활용
--
-- 적용:
--   psql storescope -f migrations/2026_06_04_perf_indexes.sql
--
-- 롤백:
--   DROP INDEX IF EXISTS idx_clusters_store_count_desc;

-- store_count 내림차순 인덱스 (popular 정렬 + min_stores 필터 동시 충족)
CREATE INDEX IF NOT EXISTS idx_clusters_store_count_desc
    ON clusters (store_count DESC);

-- trend_snapshots 의 cluster_id + snapshot_at 정렬 인덱스
-- LATERAL join에서 idx_trend_cluster_time 사용 중이지만 ORDER BY 명시 시 더 빠름
-- (already exists per pg_indexes audit, verify+conditional create)
CREATE INDEX IF NOT EXISTS idx_trend_cluster_snapshot_desc
    ON trend_snapshots (cluster_id, snapshot_at DESC);

-- product_clusters 의 price-based 정렬 가속 (representative product 선택)
-- products.price_min 은 변경 빈도 낮음 → 인덱스 효율 높음
CREATE INDEX IF NOT EXISTS idx_products_cluster_price
    ON product_clusters (cluster_id) INCLUDE (product_id);

-- 통계 갱신 (인덱스 선택 옵티마이저 정확도)
ANALYZE clusters;
ANALYZE trend_snapshots;
ANALYZE product_clusters;
