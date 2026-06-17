-- StoreScope migration: Stripe 잔재 컬럼명 → 결제 무관 generic 이름
-- Date: 2026-06-07
-- Purpose:
--   api_keys 테이블에 stripe_customer_id / stripe_subscription_id 컬럼이 있으나
--   코드(api/auth.py + api/paddle_routes.py)는 customer_id / subscription_id 로 참조.
--   → 구독 취소 시 deactivate_by_customer() UndefinedColumn 에러 = 매출 누수.
--
--   Paddle 사용 중이라 stripe_ prefix는 historical artifact.
--   해결: 컬럼명을 결제 게이트웨이 무관한 generic 이름으로 정규화.
--
-- 검출: tests/test_paddle_webhook_integration.py::test_subscription_canceled_runs_deactivate
--
-- 적용:
--   psql storescope -f migrations/2026_06_07_rename_stripe_to_customer.sql
--
-- 롤백:
--   ALTER TABLE api_keys RENAME COLUMN customer_id TO stripe_customer_id;
--   ALTER TABLE api_keys RENAME COLUMN subscription_id TO stripe_subscription_id;

-- FIX 2026-06-07: 멱등 처리 — 이미 rename된 상태에서 재실행 시 에러 없이 통과.
-- apply_migrations.sh 가 모든 마이그레이션을 재실행할 수 있는 환경 보장.
BEGIN;

DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name = 'api_keys' AND column_name = 'stripe_customer_id'
    ) THEN
        ALTER TABLE api_keys RENAME COLUMN stripe_customer_id TO customer_id;
    END IF;

    IF EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name = 'api_keys' AND column_name = 'stripe_subscription_id'
    ) THEN
        ALTER TABLE api_keys RENAME COLUMN stripe_subscription_id TO subscription_id;
    END IF;
END $$;

COMMIT;

-- 검증
SELECT column_name FROM information_schema.columns
WHERE table_name = 'api_keys' AND column_name IN ('customer_id', 'subscription_id', 'stripe_customer_id');
-- 기대: customer_id, subscription_id (stripe_* 0건)
