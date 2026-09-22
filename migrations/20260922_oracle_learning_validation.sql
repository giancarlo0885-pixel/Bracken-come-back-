CREATE TABLE IF NOT EXISTS oracle_counterfactual_outcomes (
    decision_id BIGINT PRIMARY KEY,
    market TEXT NOT NULL,
    symbol TEXT NOT NULL,
    decision_time TIMESTAMPTZ NOT NULL,
    horizon_minutes INTEGER NOT NULL,
    entry_price DOUBLE PRECISION NOT NULL,
    horizon_price DOUBLE PRECISION NOT NULL,
    return_pct DOUBLE PRECISION NOT NULL,
    outcome_class TEXT NOT NULL,
    source_signal_id BIGINT NOT NULL,
    evaluated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    execution_impact TEXT NOT NULL DEFAULT 'NONE',
    CHECK (outcome_class IN ('avoided_loss','missed_winner','flat')),
    CHECK (execution_impact='NONE')
);
CREATE INDEX IF NOT EXISTS idx_oracle_counterfactual_market_symbol_time
ON oracle_counterfactual_outcomes(market,symbol,decision_time DESC);

CREATE TABLE IF NOT EXISTS oracle_calibration_buckets (
    market TEXT NOT NULL,
    probability_floor DOUBLE PRECISION NOT NULL,
    probability_ceiling DOUBLE PRECISION NOT NULL,
    samples INTEGER NOT NULL,
    predicted_probability DOUBLE PRECISION,
    realized_win_rate DOUBLE PRECISION,
    calibration_error DOUBLE PRECISION,
    avg_expected_edge_pct DOUBLE PRECISION,
    avg_realized_return_pct DOUBLE PRECISION,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    execution_impact TEXT NOT NULL DEFAULT 'NONE',
    PRIMARY KEY(market,probability_floor,probability_ceiling),
    CHECK (execution_impact='NONE')
);

CREATE TABLE IF NOT EXISTS oracle_validation_weaknesses (
    weakness_key TEXT PRIMARY KEY,
    market TEXT NOT NULL,
    strategy TEXT NOT NULL,
    regime TEXT NOT NULL,
    samples INTEGER NOT NULL,
    expectancy DOUBLE PRECISION,
    profit_factor DOUBLE PRECISION,
    avg_mfe_pct DOUBLE PRECISION,
    avg_mae_pct DOUBLE PRECISION,
    state TEXT NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    execution_impact TEXT NOT NULL DEFAULT 'NONE',
    CHECK (execution_impact='NONE')
);

CREATE TABLE IF NOT EXISTS oracle_paper_promotion_evidence (
    candidate_key TEXT PRIMARY KEY,
    market TEXT NOT NULL,
    samples INTEGER NOT NULL,
    expectancy DOUBLE PRECISION,
    profit_factor DOUBLE PRECISION,
    calibration_error DOUBLE PRECISION,
    max_drawdown DOUBLE PRECISION,
    eligible BOOLEAN NOT NULL DEFAULT FALSE,
    reasons JSONB NOT NULL DEFAULT '[]'::jsonb,
    evaluated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    execution_impact TEXT NOT NULL DEFAULT 'NONE',
    CHECK (execution_impact='NONE')
);
