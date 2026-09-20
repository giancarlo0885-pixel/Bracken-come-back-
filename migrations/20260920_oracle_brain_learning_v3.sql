-- Oracle Brain Learning V3
-- Research-only intelligence expansion: versioned memory, source corroboration,
-- counterfactual abstention learning, drift detection, working memory, experiments,
-- and per-stage learner observability. No execution authority is granted.

ALTER TABLE oracle_brain_episodes
    ADD COLUMN IF NOT EXISTS model TEXT,
    ADD COLUMN IF NOT EXISTS model_version TEXT,
    ADD COLUMN IF NOT EXISTS strategy_version TEXT,
    ADD COLUMN IF NOT EXISTS feature_schema_hash TEXT,
    ADD COLUMN IF NOT EXISTS feature_value_hash TEXT;

ALTER TABLE oracle_brain_sources
    ADD COLUMN IF NOT EXISTS cluster_key TEXT,
    ADD COLUMN IF NOT EXISTS adaptive_reputation DOUBLE PRECISION,
    ADD COLUMN IF NOT EXISTS corroboration_count INTEGER NOT NULL DEFAULT 1;

CREATE INDEX IF NOT EXISTS idx_oracle_brain_sources_cluster
ON oracle_brain_sources(cluster_key, observed_at DESC);

CREATE TABLE IF NOT EXISTS oracle_brain_source_clusters (
    cluster_key TEXT PRIMARY KEY,
    canonical_title TEXT NOT NULL,
    symbol TEXT,
    category TEXT,
    source_count INTEGER NOT NULL DEFAULT 1,
    provider_count INTEGER NOT NULL DEFAULT 1,
    providers JSONB NOT NULL DEFAULT '[]'::jsonb,
    first_observed_at TIMESTAMPTZ,
    last_observed_at TIMESTAMPTZ,
    corroboration_score DOUBLE PRECISION NOT NULL DEFAULT 0.0,
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    execution_impact TEXT NOT NULL DEFAULT 'NONE',
    CHECK (source_count >= 1),
    CHECK (provider_count >= 1),
    CHECK (corroboration_score >= 0.0 AND corroboration_score <= 1.0),
    CHECK (execution_impact = 'NONE')
);

CREATE INDEX IF NOT EXISTS idx_oracle_brain_source_clusters_recent
ON oracle_brain_source_clusters(last_observed_at DESC);

CREATE TABLE IF NOT EXISTS oracle_brain_provider_reputation (
    provider TEXT PRIMARY KEY,
    base_quality DOUBLE PRECISION NOT NULL DEFAULT 0.5,
    total_events INTEGER NOT NULL DEFAULT 0,
    corroborated_events INTEGER NOT NULL DEFAULT 0,
    reputation_score DOUBLE PRECISION NOT NULL DEFAULT 0.5,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    execution_impact TEXT NOT NULL DEFAULT 'NONE',
    CHECK (base_quality >= 0.0 AND base_quality <= 1.0),
    CHECK (reputation_score >= 0.0 AND reputation_score <= 1.0),
    CHECK (total_events >= 0),
    CHECK (corroborated_events >= 0),
    CHECK (execution_impact = 'NONE')
);

CREATE TABLE IF NOT EXISTS oracle_brain_counterfactuals (
    id BIGSERIAL PRIMARY KEY,
    decision_audit_id BIGINT NOT NULL,
    horizon_minutes INTEGER NOT NULL,
    market TEXT NOT NULL,
    symbol TEXT NOT NULL,
    strategy TEXT,
    regime TEXT,
    recommendation TEXT,
    approved BOOLEAN NOT NULL DEFAULT FALSE,
    reason TEXT,
    observed_at TIMESTAMPTZ NOT NULL,
    entry_price DOUBLE PRECISION NOT NULL,
    due_at TIMESTAMPTZ NOT NULL,
    resolved_at TIMESTAMPTZ,
    future_price DOUBLE PRECISION,
    gross_return_pct DOUBLE PRECISION,
    estimated_cost_pct DOUBLE PRECISION NOT NULL DEFAULT 0.0,
    net_return_pct DOUBLE PRECISION,
    classification TEXT NOT NULL DEFAULT 'pending',
    feature_snapshot JSONB NOT NULL DEFAULT '{}'::jsonb,
    feature_schema_hash TEXT,
    decision_payload JSONB NOT NULL DEFAULT '{}'::jsonb,
    execution_impact TEXT NOT NULL DEFAULT 'NONE',
    UNIQUE(decision_audit_id, horizon_minutes),
    CHECK (horizon_minutes > 0),
    CHECK (entry_price > 0),
    CHECK (classification IN ('pending','avoided_loss','missed_winner','neutral','unresolved')),
    CHECK (execution_impact = 'NONE')
);

CREATE INDEX IF NOT EXISTS idx_oracle_brain_counterfactuals_pending
ON oracle_brain_counterfactuals(market, classification, due_at);

CREATE INDEX IF NOT EXISTS idx_oracle_brain_counterfactuals_symbol
ON oracle_brain_counterfactuals(market, symbol, observed_at DESC);

CREATE TABLE IF NOT EXISTS oracle_brain_drift_events (
    event_key TEXT PRIMARY KEY,
    market TEXT NOT NULL,
    strategy TEXT NOT NULL,
    regime TEXT NOT NULL,
    baseline_samples INTEGER NOT NULL,
    recent_samples INTEGER NOT NULL,
    baseline_expectancy DOUBLE PRECISION,
    recent_expectancy DOUBLE PRECISION,
    baseline_win_rate DOUBLE PRECISION,
    recent_win_rate DOUBLE PRECISION,
    z_score DOUBLE PRECISION,
    sign_flip BOOLEAN NOT NULL DEFAULT FALSE,
    drift_detected BOOLEAN NOT NULL DEFAULT FALSE,
    severity TEXT NOT NULL DEFAULT 'low',
    status TEXT NOT NULL DEFAULT 'active',
    detected_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    last_observed_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    execution_impact TEXT NOT NULL DEFAULT 'NONE',
    CHECK (baseline_samples >= 0),
    CHECK (recent_samples >= 0),
    CHECK (severity IN ('low','medium','high')),
    CHECK (status IN ('active','resolved','retired')),
    CHECK (execution_impact = 'NONE')
);

CREATE INDEX IF NOT EXISTS idx_oracle_brain_drift_active
ON oracle_brain_drift_events(market, status, severity, last_observed_at DESC);

CREATE TABLE IF NOT EXISTS oracle_brain_working_memory (
    memory_key TEXT PRIMARY KEY,
    market TEXT,
    symbol TEXT,
    kind TEXT NOT NULL,
    topic TEXT NOT NULL,
    payload JSONB NOT NULL DEFAULT '{}'::jsonb,
    importance DOUBLE PRECISION NOT NULL DEFAULT 0.5,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    expires_at TIMESTAMPTZ NOT NULL,
    execution_impact TEXT NOT NULL DEFAULT 'NONE',
    CHECK (importance >= 0.0 AND importance <= 1.0),
    CHECK (execution_impact = 'NONE')
);

CREATE INDEX IF NOT EXISTS idx_oracle_brain_working_memory_expiry
ON oracle_brain_working_memory(expires_at);

CREATE INDEX IF NOT EXISTS idx_oracle_brain_working_memory_market
ON oracle_brain_working_memory(market, importance DESC, updated_at DESC);

CREATE TABLE IF NOT EXISTS oracle_brain_experiments (
    experiment_key TEXT PRIMARY KEY,
    market TEXT NOT NULL,
    strategy TEXT NOT NULL,
    regime TEXT NOT NULL,
    hypothesis TEXT NOT NULL,
    trigger_type TEXT NOT NULL,
    baseline_cutoff TIMESTAMPTZ NOT NULL,
    target_samples INTEGER NOT NULL,
    observed_samples INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'collecting',
    priority DOUBLE PRECISION NOT NULL DEFAULT 50.0,
    evidence JSONB NOT NULL DEFAULT '{}'::jsonb,
    result JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    ready_at TIMESTAMPTZ,
    execution_impact TEXT NOT NULL DEFAULT 'NONE',
    CHECK (target_samples > 0),
    CHECK (observed_samples >= 0),
    CHECK (status IN ('collecting','ready_for_review','resolved','retired')),
    CHECK (priority >= 0.0 AND priority <= 100.0),
    CHECK (execution_impact = 'NONE')
);

CREATE INDEX IF NOT EXISTS idx_oracle_brain_experiments_status
ON oracle_brain_experiments(status, priority DESC, updated_at DESC);

CREATE TABLE IF NOT EXISTS oracle_brain_learning_runs (
    run_key TEXT PRIMARY KEY,
    market TEXT NOT NULL,
    started_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    finished_at TIMESTAMPTZ,
    stage TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'running',
    elapsed_ms BIGINT,
    details JSONB NOT NULL DEFAULT '{}'::jsonb,
    execution_impact TEXT NOT NULL DEFAULT 'NONE',
    CHECK (status IN ('running','ok','partial','failed','skipped')),
    CHECK (execution_impact = 'NONE')
);

CREATE INDEX IF NOT EXISTS idx_oracle_brain_learning_runs_market
ON oracle_brain_learning_runs(market, started_at DESC);
