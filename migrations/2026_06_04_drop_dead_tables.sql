-- StoreScope migration: Drop dead tables (Step 2)
-- Date: 2026-06-04
-- Purpose:
--   webhook_subscriptions: 7일간 구독자 0명, 마스터플랜 Path A 22% 확률 + Pro 미실증.
--   API 코드와 함께 동시 제거 (단일 트랜잭션 보장).
--
-- 적용:
--   psql storescope -f migrations/2026_06_04_drop_dead_tables.sql
--
-- 롤백 (필요 시):
--   git revert <SHA> + 본 파일 + api/main.py 의 /webhook/subscribe 블록 복원

BEGIN;
DROP TABLE IF EXISTS webhook_subscriptions CASCADE;
COMMIT;

-- 검증
SELECT EXISTS (
    SELECT 1 FROM information_schema.tables
    WHERE table_schema='public' AND table_name='webhook_subscriptions'
) AS table_still_exists;  -- 기대값: f
