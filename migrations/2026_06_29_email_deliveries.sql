-- D+29 (2026-06-29): Weekly digest 발송 wiring — Gemini review_v1 반영
--
-- 1. email_deliveries: at-most-once 상태머신 (pending → sent). DB 일시 장애 시 중복 발송 차단.
-- 2. api_keys.unsubscribed_at: 정보통신망법 §50 / CAN-SPAM 컴플라이언스. unsubscribe 처리.
--
-- 적용:
--   psql "$DATABASE_URL" -f migrations/2026_06_29_email_deliveries.sql
-- 또는 run_migrations.py (있다면)

BEGIN;

-- ────────────────────────────────────────────────────────────────────────────
-- email_deliveries — 발송 로그 + 중복 차단
-- ────────────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS email_deliveries (
    id              BIGSERIAL    PRIMARY KEY,
    subscriber_id   INTEGER      NOT NULL REFERENCES api_keys(id) ON DELETE CASCADE,
    week_label      TEXT         NOT NULL,                   -- ISO 'YYYY-WNN'
    delivery_type   TEXT         NOT NULL DEFAULT 'weekly_digest',
    status          TEXT         NOT NULL DEFAULT 'pending', -- pending | sent | failed
    message_id      TEXT,                                    -- SMTP Message-Id (있으면)
    error_summary   TEXT,                                    -- 실패 시 err repr (≤200chars)
    attempted_at    TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    sent_at         TIMESTAMPTZ,
    UNIQUE (subscriber_id, week_label, delivery_type)
);

CREATE INDEX IF NOT EXISTS idx_email_deliveries_week_status
    ON email_deliveries (week_label, status);

CREATE INDEX IF NOT EXISTS idx_email_deliveries_subscriber
    ON email_deliveries (subscriber_id, sent_at DESC);

COMMENT ON TABLE email_deliveries IS
'At-most-once 발송 로그. pending INSERT → SMTP 발송 → sent UPDATE. UNIQUE 가 중복 차단.';

COMMENT ON COLUMN email_deliveries.status IS
'pending: INSERT 완료, SMTP 시도 전. sent: SMTP 성공. failed: SMTP 실패 (재시도 가능).';

-- ────────────────────────────────────────────────────────────────────────────
-- api_keys.unsubscribed_at — Unsubscribe 컴플라이언스
-- ────────────────────────────────────────────────────────────────────────────
ALTER TABLE api_keys
    ADD COLUMN IF NOT EXISTS unsubscribed_at TIMESTAMPTZ;

CREATE INDEX IF NOT EXISTS idx_api_keys_active_subscribed
    ON api_keys (is_active, unsubscribed_at)
    WHERE is_active = TRUE AND unsubscribed_at IS NULL;

COMMENT ON COLUMN api_keys.unsubscribed_at IS
'정보통신망법 §50 / CAN-SPAM: 수신거부 시각. NULL = 수신 동의. 발송 query: is_active AND unsubscribed_at IS NULL.';

COMMIT;
