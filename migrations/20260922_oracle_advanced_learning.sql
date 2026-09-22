CREATE TABLE IF NOT EXISTS oracle_decision_replays (
    decision_id BIGINT PRIMARY KEY,
    market TEXT NOT NULL,
    symbol TEXT NOT NULL,
    decision_time TIMESTAMPTZ NOT NULL,
    observation_count INTEGER NOT NULL DEFAULT 0,
    replay_snapshot JSONB NOT NULL DEFAULT '{}'::jsonb,
    lineage_hash TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    execution_impact TEXT NOT NULL DEFAULT 'NONE',
    CHECK (execution_impact='NONE')
);

CREATE TABLE IF NOT EXISTS oracle_setup_validation (
    cohort_key TEXT PRIMARY KEY,
    market TEXT NOT NULL,
    strategy TEXT NOT NULL,
    regime TEXT NOT NULL,
    samples INTEGER NOT NULL,
    wins INTEGER NOT NULL,
    losses INTEGER NOT NULL,
    expectancy DOUBLE PRECISION,
    profit_factor DOUBLE PRECISION,
    avg_mfe_pct DOUBLE PRECISION,
    avg_mae_pct DOUBLE PRECISION,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    execution_impact TEXT NOT NULL DEFAULT 'NONE',
    CHECK (execution_impact='NONE')
);

CREATE TABLE IF NOT EXISTS oracle_failure_taxonomy (
    episode_key TEXT PRIMARY KEY,
    market TEXT NOT NULL,
    strategy TEXT,
    regime TEXT,
    failure_class TEXT NOT NULL,
    evidence JSONB NOT NULL DEFAULT '{}'::jsonb,
    classified_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    execution_impact TEXT NOT NULL DEFAULT 'NONE',
    CHECK (execution_impact='NONE')
);

CREATE TABLE IF NOT EXISTS oracle_challenger_validation (
    candidate_key TEXT NOT NULL,
    market TEXT NOT NULL,
    validation_window TEXT NOT NULL,
    train_end TIMESTAMPTZ,
    test_start TIMESTAMPTZ,
    samples INTEGER NOT NULL DEFAULT 0,
    expectancy DOUBLE PRECISION,
    profit_factor DOUBLE PRECISION,
    max_drawdown DOUBLE PRECISION,
    state TEXT NOT NULL DEFAULT 'research',
    evidence JSONB NOT NULL DEFAULT '{}'::jsonb,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    execution_impact TEXT NOT NULL DEFAULT 'NONE',
    PRIMARY KEY(candidate_key,market,validation_window),
    CHECK (state IN ('research','shadow','paper_qualified')),
    CHECK (execution_impact='NONE')
);

CREATE TABLE IF NOT EXISTS oracle_data_quality_evidence (
    evidence_key TEXT PRIMARY KEY,
    market TEXT NOT NULL,
    symbol TEXT,
    observed_at TIMESTAMPTZ NOT NULL,
    freshness_score DOUBLE PRECISION,
    agreement_score DOUBLE PRECISION,
    provider_health_score DOUBLE PRECISION,
    quality_score DOUBLE PRECISION NOT NULL,
    evidence JSONB NOT NULL DEFAULT '{}'::jsonb,
    execution_impact TEXT NOT NULL DEFAULT 'NONE',
    CHECK (execution_impact='NONE')
);
