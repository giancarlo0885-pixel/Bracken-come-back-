CREATE TABLE IF NOT EXISTS opportunity_rankings (
 id BIGSERIAL PRIMARY KEY, market TEXT NOT NULL, symbol TEXT NOT NULL, rank INTEGER NOT NULL,
 opportunity_score DOUBLE PRECISION NOT NULL, payload JSONB, created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_opportunity_market_created ON opportunity_rankings(market, created_at DESC);
CREATE TABLE IF NOT EXISTS portfolio_rotations (
 id BIGSERIAL PRIMARY KEY, market TEXT NOT NULL, sold_symbol TEXT, bought_symbol TEXT,
 score_gap DOUBLE PRECISION, reason TEXT, status TEXT NOT NULL DEFAULT 'proposed', created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS provider_health (
 provider TEXT PRIMARY KEY, configured BOOLEAN NOT NULL, status TEXT NOT NULL,
 latency_ms DOUBLE PRECISION, message TEXT, checked_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS backtest_runs (
 id BIGSERIAL PRIMARY KEY, market TEXT, symbol TEXT NOT NULL, strategy TEXT NOT NULL,
 parameters JSONB, metrics JSONB NOT NULL, created_at TEXT NOT NULL
);
ALTER TABLE portfolios ADD COLUMN IF NOT EXISTS peak_equity DOUBLE PRECISION;
ALTER TABLE portfolios ADD COLUMN IF NOT EXISTS risk_state TEXT DEFAULT 'normal';
