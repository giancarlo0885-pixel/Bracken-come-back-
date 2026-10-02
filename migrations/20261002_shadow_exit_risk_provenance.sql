CREATE TABLE IF NOT EXISTS garibaldi_shadow_exit_triggers (
    position_key TEXT NOT NULL,
    model_version TEXT NOT NULL,
    market TEXT NOT NULL,
    symbol TEXT NOT NULL,
    opened_at TIMESTAMPTZ,
    trigger_at TIMESTAMPTZ NOT NULL,
    trigger_snapshot JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY(position_key, model_version)
);

ALTER TABLE garibaldi_shadow_experiments
    ADD COLUMN IF NOT EXISTS entry_time TIMESTAMPTZ;

ALTER TABLE garibaldi_shadow_experiments
    ADD COLUMN IF NOT EXISTS initial_risk_usd NUMERIC(14,6);

ALTER TABLE garibaldi_shadow_experiments
    ADD COLUMN IF NOT EXISTS risk_basis_source TEXT;

COMMENT ON TABLE garibaldi_shadow_exit_triggers IS
    'Transient restart-safe trigger snapshots for the research-only exit challenger; rows are deleted after final experiment settlement.';
