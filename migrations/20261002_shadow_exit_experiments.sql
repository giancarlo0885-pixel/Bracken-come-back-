CREATE TABLE IF NOT EXISTS garibaldi_shadow_exit_epochs (
    model_version TEXT PRIMARY KEY,
    generation INTEGER NOT NULL,
    started_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    note TEXT NOT NULL,
    execution_impact TEXT NOT NULL DEFAULT 'NONE'
);

CREATE TABLE IF NOT EXISTS garibaldi_shadow_experiments (
    experiment_id BIGSERIAL PRIMARY KEY,
    trade_id TEXT NOT NULL,
    episode_id UUID NOT NULL,
    generation INTEGER NOT NULL,
    market TEXT NOT NULL,
    symbol TEXT NOT NULL,
    entry_pattern TEXT NOT NULL,
    regime TEXT NOT NULL,
    confidence_bucket TEXT NOT NULL,
    actual_exit_type TEXT NOT NULL,
    challenger_exit_type TEXT NOT NULL,
    actual_realized_r_net NUMERIC(12,6) NOT NULL,
    challenger_counterfactual_r_net NUMERIC(12,6) NOT NULL,
    delta_r NUMERIC(12,6) GENERATED ALWAYS AS
        (challenger_counterfactual_r_net - actual_realized_r_net) STORED,
    mfe_r NUMERIC(12,6),
    mae_r NUMERIC(12,6),
    holding_time BIGINT,
    exit_time TIMESTAMPTZ NOT NULL,
    model_version TEXT NOT NULL,
    cost_model_version TEXT NOT NULL,
    trigger_snapshot JSONB,
    execution_impact TEXT NOT NULL DEFAULT 'NONE',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT unique_shadow_exit_trade_experiment
        UNIQUE (trade_id, generation, model_version)
);

CREATE INDEX IF NOT EXISTS idx_shadow_exit_episode
    ON garibaldi_shadow_experiments (episode_id);

CREATE INDEX IF NOT EXISTS idx_shadow_exit_cohorts
    ON garibaldi_shadow_experiments (entry_pattern, regime, confidence_bucket);

CREATE INDEX IF NOT EXISTS idx_shadow_exit_market_time
    ON garibaldi_shadow_experiments (market, exit_time DESC);

COMMENT ON TABLE garibaldi_shadow_experiments IS
    'Research-only paired exit challenger evidence. One durable row per exact-provenance completed trade; no execution authority.';
