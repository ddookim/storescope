-- StoreScope DB 스키마
-- 실행: psql storescope -f scripts/init_db.sql

CREATE EXTENSION IF NOT EXISTS pg_trgm;

-- 스토어 테이블
CREATE TABLE IF NOT EXISTS stores (
    id          SERIAL PRIMARY KEY,
    domain      TEXT NOT NULL UNIQUE,
    first_seen  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    last_crawled TIMESTAMPTZ,
    is_active   BOOLEAN DEFAULT TRUE,
    product_count INTEGER DEFAULT 0
);

-- 상품 테이블
CREATE TABLE IF NOT EXISTS products (
    id           SERIAL PRIMARY KEY,
    store_id     INTEGER NOT NULL REFERENCES stores(id) ON DELETE CASCADE,
    shopify_id   BIGINT NOT NULL,
    title        TEXT NOT NULL,
    handle       TEXT,
    price_min    NUMERIC(14, 2),
    price_max    NUMERIC(14, 2),
    image_url    TEXT,
    image_hash   CHAR(16),            -- pHash (16자리 hex)
    first_seen   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    last_seen    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (store_id, shopify_id)
);

-- 클러스터 테이블 (pHash 기준 동일 상품 그룹)
CREATE TABLE IF NOT EXISTS clusters (
    id              SERIAL PRIMARY KEY,
    cluster_hash    CHAR(16) NOT NULL UNIQUE,  -- 대표 pHash
    first_seen      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    last_updated    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    store_count     INTEGER DEFAULT 0,
    product_count   INTEGER DEFAULT 0
);

-- 상품-클러스터 연결
CREATE TABLE IF NOT EXISTS product_clusters (
    product_id  INTEGER NOT NULL REFERENCES products(id) ON DELETE CASCADE,
    cluster_id  INTEGER NOT NULL REFERENCES clusters(id) ON DELETE CASCADE,
    PRIMARY KEY (product_id, cluster_id)
);

-- 트렌드 스냅샷 (주간 기록 — 히스토리 해자 핵심)
CREATE TABLE IF NOT EXISTS trend_snapshots (
    id          SERIAL PRIMARY KEY,
    cluster_id  INTEGER NOT NULL REFERENCES clusters(id) ON DELETE CASCADE,
    snapshot_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    store_count INTEGER NOT NULL,
    week_delta  INTEGER DEFAULT 0,   -- 직전 스냅샷 대비 스토어 수 변화
    trend_score FLOAT   DEFAULT 0.0  -- week_delta / 30일 평균 delta (비율 기반)
);

-- API 키 테이블
CREATE TABLE IF NOT EXISTS api_keys (
    id                      SERIAL PRIMARY KEY,
    key_hash                CHAR(64) NOT NULL UNIQUE,   -- SHA-256 hex
    key_prefix              CHAR(12) NOT NULL,           -- 디버깅용 (si_ + 9자)
    email                   TEXT NOT NULL,
    plan                    TEXT NOT NULL DEFAULT 'starter',
    is_active               BOOLEAN NOT NULL DEFAULT TRUE,
    daily_limit             INTEGER,                     -- NULL = 무제한 (Pro)
    customer_id             TEXT UNIQUE,  -- 동시 웹훅 중복 발급 DB 레벨 차단
    subscription_id         TEXT,
    trial_ends_at           TIMESTAMPTZ,  -- Pro trial 종료 시점 (NULL = 즉시 paid)
    created_at              TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- 일별 사용량 테이블
CREATE TABLE IF NOT EXISTS api_usage (
    id            SERIAL PRIMARY KEY,
    key_id        INTEGER NOT NULL REFERENCES api_keys(id) ON DELETE CASCADE,
    used_date     DATE NOT NULL DEFAULT CURRENT_DATE,
    request_count INTEGER NOT NULL DEFAULT 0,
    UNIQUE (key_id, used_date)
);

-- FIX: 무료 툴 이메일 리드 캡처 — 전환 퍼널 복구,
-- 이메일 수집 후 Paddle checkout 유도로 전환율 목표 5-15%
CREATE TABLE IF NOT EXISTS email_leads (
    id         SERIAL PRIMARY KEY,
    email      TEXT NOT NULL UNIQUE,
    source     TEXT NOT NULL DEFAULT 'xray',
    domain     TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- FIX: Pro 웹훅 알림 구독 테이블 — 랜딩 페이지 약속 이행,
-- 웹훅 의존성으로 Pro 유저 해지 마찰 증가 → Churn 방지
CREATE TABLE IF NOT EXISTS webhook_subscriptions (
    id          SERIAL PRIMARY KEY,
    key_id      INTEGER NOT NULL REFERENCES api_keys(id) ON DELETE CASCADE UNIQUE,
    url         TEXT NOT NULL,
    events      TEXT[] NOT NULL DEFAULT '{"cluster.new","cluster.trending"}',
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- 인덱스
CREATE INDEX IF NOT EXISTS idx_products_image_hash       ON products(image_hash);
CREATE INDEX IF NOT EXISTS idx_products_store_id         ON products(store_id);
CREATE INDEX IF NOT EXISTS idx_product_clusters_cluster  ON product_clusters(cluster_id);
CREATE INDEX IF NOT EXISTS idx_trend_cluster_time        ON trend_snapshots(cluster_id, snapshot_at DESC);
CREATE INDEX IF NOT EXISTS idx_clusters_store_count      ON clusters(store_count DESC);
CREATE INDEX IF NOT EXISTS idx_api_keys_hash             ON api_keys(key_hash);
CREATE INDEX IF NOT EXISTS idx_api_usage_date            ON api_usage(key_id, used_date);
CREATE INDEX IF NOT EXISTS idx_stores_domain             ON stores(domain);
CREATE INDEX IF NOT EXISTS idx_products_title_trgm       ON products USING gin(title gin_trgm_ops);
