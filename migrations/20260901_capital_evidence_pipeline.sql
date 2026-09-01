ALTER TABLE forecast_validation ADD COLUMN IF NOT EXISTS evidence_key TEXT;
ALTER TABLE forecast_validation ADD COLUMN IF NOT EXISTS decision_timestamp TEXT;
ALTER TABLE forecast_validation ADD COLUMN IF NOT EXISTS outcome_timestamp TEXT;
ALTER TABLE forecast_validation ADD COLUMN IF NOT EXISTS horizon_bars INTEGER;
ALTER TABLE forecast_validation ADD COLUMN IF NOT EXISTS run_id TEXT;

CREATE UNIQUE INDEX IF NOT EXISTS idx_forecast_validation_evidence_key
    ON forecast_validation(evidence_key)
    WHERE evidence_key IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_forecast_validation_model_created
    ON forecast_validation(model, model_version, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_walk_forward_model_created
    ON walk_forward_validation_runs(model, model_version, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_shadow_broker_status_created
    ON shadow_broker_orders(status, created_at DESC);
