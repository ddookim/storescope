-- Storm Score V2 — momentum-based trend ranking 알고리즘 컬럼 추가
--
-- D+20 (2026-06-21) 자율 사냥 후속.
-- 기존 trend_score (delta / avg_30d) 의 6개 결함 fix:
--   1) age decay 부재         → log₁₀(age_days + 2) gravity
--   2) velocity (가속도) 무시  → tanh((δ_t - δ_{t-1}) / max(δ_{t-1}, 1))
--   3) noise 미감쇄           → EMA(α=0.5, 4 weeks)
--   4) log scaling 무시        → log₁₀(store_count + 1) HN sublinear
--   5) small_count bias        → store_count < 3 시 ×0.3 penalty
--   6) 분모 0 fallback         → max(prev, 1) clamping
--
-- ORDER BY 정합: api/main.py:_SORT_CLAUSES["rising"] = "momentum_score DESC, week_delta DESC"
-- backwards-compat: trend_score 컬럼은 유지 (weekly_digest 이메일 normalization 의존).

ALTER TABLE trend_snapshots
    ADD COLUMN IF NOT EXISTS momentum_score DECIMAL(10, 4) DEFAULT 0.0;

-- /trending API LATERAL JOIN — 각 cluster 의 latest snapshot 1행 lookup 가속.
-- (snapshot_at DESC) 가 leading column 이므로 cluster_id 별 latest 가져올 때 index-only scan.
CREATE INDEX IF NOT EXISTS idx_trend_snapshots_cluster_recent
    ON trend_snapshots(cluster_id, snapshot_at DESC);

-- pipeline 4-week history bulk lookup 가속.
CREATE INDEX IF NOT EXISTS idx_trend_snapshots_recent_window
    ON trend_snapshots(snapshot_at DESC)
    WHERE snapshot_at > NOW() - INTERVAL '5 weeks';
