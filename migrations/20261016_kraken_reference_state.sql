-- Overwritten singleton, not a tick/history ledger. No canonical evidence touched.
CREATE TABLE IF NOT EXISTS kraken_reference_state (
    id SMALLINT PRIMARY KEY CHECK (id=1),
    updated_at TIMESTAMPTZ NOT NULL,
    payload JSONB NOT NULL
);
