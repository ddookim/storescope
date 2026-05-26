-- Migration: stripe_customer_id → customer_id, stripe_subscription_id → subscription_id
-- Apply to live DB: psql storescope -f scripts/migrate_column_rename.sql
ALTER TABLE api_keys RENAME COLUMN stripe_customer_id TO customer_id;
ALTER TABLE api_keys RENAME COLUMN stripe_subscription_id TO subscription_id;
