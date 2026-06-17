-- StoreScope migration: Paddle webhook idempotency
-- Date: 2026-06-04
-- Purpose: Paddle 재시도 정책으로 동일 webhook 중복 수신 시
--          subscription.activated 가 N회 처리되어 N개의 API 키가 발급되는
--          취약점 차단. event_id 기반 PRIMARY KEY 유니크 제약으로 idempotency 보장.
--
-- 적용:
--   psql storescope -f migrations/2026_06_04_paddle_idempotency.sql
--   (or via render: render-cli database psql -f ...)
--
-- 롤백:
--   DROP TABLE paddle_processed_events;

CREATE TABLE IF NOT EXISTS paddle_processed_events (
    event_id     TEXT PRIMARY KEY,
    event_type   TEXT NOT NULL,
    processed_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- 90일 이상 된 행 정리용 인덱스 (주기적 DELETE WHERE processed_at < NOW()-INTERVAL '90 days')
CREATE INDEX IF NOT EXISTS idx_paddle_events_processed_at
    ON paddle_processed_events (processed_at);

COMMENT ON TABLE paddle_processed_events IS
    'Paddle webhook idempotency. INSERT ON CONFLICT DO NOTHING 후 affected=0이면 중복 → skip.';
