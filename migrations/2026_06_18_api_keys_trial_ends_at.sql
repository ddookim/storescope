-- StoreScope migration: api_keys.trial_ends_at 컬럼 추가
-- Date: 2026-06-18
-- Purpose:
--   api/auth.py:114 의 create_api_key() 가 trial_ends_at 컬럼에 INSERT 시도.
--   로컬 prod DB에는 수동 ALTER로 존재하지만 scripts/init_db.sql + 신규 배포 DB에는 부재.
--   첫 paid 결제 webhook → _handle_new_subscription → create_api_key 호출 → UndefinedColumn 예외.
--   결과: subscription.activated 이벤트 처리 실패 → 고객 결제 후 API 키 미발급 → 환불 직격.
--
-- 검출: tests/test_subscription_activation.py 3/3 FAIL (2026-06-18)
--   psycopg2.errors.UndefinedColumn: column "trial_ends_at" of relation "api_keys" does not exist
--
-- 적용:
--   psql storescope -f migrations/2026_06_18_api_keys_trial_ends_at.sql
--
-- 멱등: IF NOT EXISTS — 재실행 안전.
-- 롤백: ALTER TABLE api_keys DROP COLUMN IF EXISTS trial_ends_at;

BEGIN;

ALTER TABLE api_keys
    ADD COLUMN IF NOT EXISTS trial_ends_at TIMESTAMPTZ;

COMMIT;

-- 검증
SELECT EXISTS (
    SELECT 1 FROM information_schema.columns
    WHERE table_schema='public'
      AND table_name='api_keys'
      AND column_name='trial_ends_at'
) AS column_exists;  -- 기대값: t
