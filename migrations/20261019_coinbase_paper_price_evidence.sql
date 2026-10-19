CREATE TABLE IF NOT EXISTS coinbase_paper_price_state (
    id SMALLINT PRIMARY KEY CHECK(id=1),
    updated_at TIMESTAMPTZ NOT NULL,
    payload JSONB NOT NULL
);
CREATE TABLE IF NOT EXISTS paper_fill_price_evidence (
    fill_id TEXT PRIMARY KEY REFERENCES paper_fills(fill_id),
    model_version TEXT NOT NULL,
    quote_id TEXT NOT NULL,
    evidence JSONB NOT NULL CHECK(octet_length(evidence::text)<=32768),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    execution_authority TEXT NOT NULL CHECK(execution_authority='PAPER_SIMULATION_ONLY')
);
