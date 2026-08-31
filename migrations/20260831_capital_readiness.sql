CREATE TABLE IF NOT EXISTS broker_order_journal (
    client_order_id TEXT PRIMARY KEY,
    broker_order_id TEXT,
    proposal_id TEXT,
    approval_hash TEXT,
    symbol TEXT NOT NULL,
    market TEXT NOT NULL DEFAULT 'crypto',
    side TEXT NOT NULL,
    quantity DOUBLE PRECISION NOT NULL,
    notional DOUBLE PRECISION NOT NULL DEFAULT 0,
    order_type TEXT NOT NULL DEFAULT 'market',
    time_in_force TEXT NOT NULL DEFAULT 'gtc',
    state TEXT NOT NULL,
    broker_state TEXT,
    submitted_at TEXT,
    last_checked_at TEXT,
    filled_quantity DOUBLE PRECISION NOT NULL DEFAULT 0,
    average_fill_price DOUBLE PRECISION,
    fees DOUBLE PRECISION NOT NULL DEFAULT 0,
    reject_reason TEXT,
    correlation_id TEXT,
    payload JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_broker_journal_broker_order
    ON broker_order_journal(broker_order_id)
    WHERE broker_order_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_broker_journal_unfinished
    ON broker_order_journal(state, updated_at DESC);
CREATE INDEX IF NOT EXISTS idx_broker_journal_symbol
    ON broker_order_journal(market, symbol, side, updated_at DESC);
CREATE INDEX IF NOT EXISTS idx_broker_journal_proposal
    ON broker_order_journal(proposal_id)
    WHERE proposal_id IS NOT NULL;

CREATE TABLE IF NOT EXISTS broker_reconciliation_runs (
    id BIGSERIAL PRIMARY KEY,
    status TEXT NOT NULL,
    account_number_present BOOLEAN NOT NULL DEFAULT FALSE,
    local_unfinished INTEGER NOT NULL DEFAULT 0,
    remote_orders INTEGER NOT NULL DEFAULT 0,
    discrepancies INTEGER NOT NULL DEFAULT 0,
    payload JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_broker_reconciliation_created
    ON broker_reconciliation_runs(created_at DESC);

CREATE TABLE IF NOT EXISTS shadow_broker_orders (
    shadow_order_id TEXT PRIMARY KEY,
    decision_id TEXT,
    proposal_id TEXT,
    symbol TEXT NOT NULL,
    market TEXT NOT NULL DEFAULT 'crypto',
    side TEXT NOT NULL,
    quantity DOUBLE PRECISION NOT NULL,
    notional DOUBLE PRECISION NOT NULL DEFAULT 0,
    oracle_reference_price DOUBLE PRECISION NOT NULL,
    paper_fill_price DOUBLE PRECISION,
    broker_bid DOUBLE PRECISION,
    broker_ask DOUBLE PRECISION,
    broker_mid DOUBLE PRECISION,
    broker_spread_pct DOUBLE PRECISION,
    broker_quote_at TEXT,
    hypothetical_fill_price DOUBLE PRECISION,
    followup_price DOUBLE PRECISION,
    outcome_return_pct DOUBLE PRECISION,
    paper_vs_broker_error_pct DOUBLE PRECISION,
    status TEXT NOT NULL DEFAULT 'OPEN',
    payload JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TEXT NOT NULL,
    evaluated_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_shadow_broker_status
    ON shadow_broker_orders(status, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_shadow_broker_symbol
    ON shadow_broker_orders(market, symbol, created_at DESC);

CREATE TABLE IF NOT EXISTS walk_forward_validation_runs (
    run_id TEXT PRIMARY KEY,
    model TEXT NOT NULL,
    model_version TEXT,
    market TEXT,
    asset_class TEXT,
    symbol TEXT NOT NULL,
    source_interval TEXT NOT NULL,
    horizon_bars INTEGER NOT NULL,
    minimum_history_bars INTEGER NOT NULL,
    sample_count INTEGER NOT NULL,
    fold_count INTEGER NOT NULL,
    metrics JSONB NOT NULL DEFAULT '{}'::jsonb,
    regime_metrics JSONB NOT NULL DEFAULT '{}'::jsonb,
    benchmarks JSONB NOT NULL DEFAULT '{}'::jsonb,
    leakage_checks JSONB NOT NULL DEFAULT '{}'::jsonb,
    status TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_walk_forward_model_created
    ON walk_forward_validation_runs(model, model_version, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_walk_forward_symbol_created
    ON walk_forward_validation_runs(symbol, source_interval, created_at DESC);

CREATE TABLE IF NOT EXISTS oracle_readiness_runs (
    run_id TEXT PRIMARY KEY,
    overall_status TEXT NOT NULL,
    report JSONB NOT NULL,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_oracle_readiness_created
    ON oracle_readiness_runs(created_at DESC);
