CREATE TABLE IF NOT EXISTS oracle_brain_observations (
    id BIGSERIAL PRIMARY KEY,
    event_key TEXT NOT NULL UNIQUE,
    source_table TEXT NOT NULL,
    source_id BIGINT,
    observation_type TEXT NOT NULL,
    market TEXT NOT NULL DEFAULT 'global',
    symbol TEXT,
    event_time TEXT NOT NULL,
    ingested_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    payload JSONB NOT NULL DEFAULT '{}'::jsonb,
    execution_impact TEXT NOT NULL DEFAULT 'NONE',
    CHECK (execution_impact = 'NONE')
);

CREATE INDEX IF NOT EXISTS idx_oracle_brain_observations_time
ON oracle_brain_observations(event_time DESC);

CREATE INDEX IF NOT EXISTS idx_oracle_brain_observations_market_symbol_time
ON oracle_brain_observations(market,symbol,event_time DESC);

CREATE INDEX IF NOT EXISTS idx_oracle_brain_observations_source
ON oracle_brain_observations(source_table,source_id);
