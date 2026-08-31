ALTER TABLE position_lots ADD COLUMN IF NOT EXISTS entry_signal_id TEXT;
ALTER TABLE position_lots ADD COLUMN IF NOT EXISTS entry_forecast_id TEXT;
ALTER TABLE position_lots ADD COLUMN IF NOT EXISTS entry_quote_id TEXT;
ALTER TABLE position_lots ADD COLUMN IF NOT EXISTS entry_model TEXT;
ALTER TABLE position_lots ADD COLUMN IF NOT EXISTS entry_model_version TEXT;
ALTER TABLE position_lots ADD COLUMN IF NOT EXISTS entry_quote_timestamp TEXT;
ALTER TABLE position_lots ADD COLUMN IF NOT EXISTS entry_provider TEXT;
ALTER TABLE position_lots ADD COLUMN IF NOT EXISTS entry_correlation_id TEXT;
ALTER TABLE position_lots ADD COLUMN IF NOT EXISTS entry_feature_snapshot JSONB;
ALTER TABLE position_lots ADD COLUMN IF NOT EXISTS entry_decision_snapshot JSONB;

CREATE INDEX IF NOT EXISTS idx_position_lots_entry_signal
    ON position_lots(market, symbol, entry_signal_id);

CREATE OR REPLACE FUNCTION capture_position_lot_entry_provenance()
RETURNS TRIGGER
LANGUAGE plpgsql
AS $$
DECLARE
    decision_payload JSONB;
    forecast_id_value TEXT;
    forecast_model_value TEXT;
    forecast_model_version_value TEXT;
    forecast_quote_timestamp_value TEXT;
BEGIN
    -- decision_id is the existing paper-attribution field and currently carries
    -- the exact signal id. Copy it into an explicit immutable provenance field.
    NEW.entry_signal_id := COALESCE(NULLIF(NEW.entry_signal_id, ''), NULLIF(NEW.decision_id, ''));
    IF NEW.entry_signal_id IS NULL THEN
        RETURN NEW;
    END IF;

    -- The ranked Oracle payload is the canonical immutable feature/decision
    -- snapshot. An absent audit row must NEVER block the paper order itself;
    -- it only means the eventual trade is ineligible for learning until exact
    -- provenance can be proven.
    SELECT oda.payload
      INTO decision_payload
      FROM oracle_decision_audit oda
     WHERE oda.market = NEW.market
       AND oda.symbol = NEW.symbol
       AND oda.payload->>'signal_id' = NEW.entry_signal_id
     ORDER BY oda.id ASC
     LIMIT 1;

    SELECT f.forecast_id::TEXT, f.model::TEXT, f.model_version::TEXT, f.source_quote_timestamp::TEXT
      INTO forecast_id_value, forecast_model_value, forecast_model_version_value, forecast_quote_timestamp_value
      FROM forecasts f
     WHERE f.market = NEW.market
       AND f.symbol = NEW.symbol
       AND f.signal_id::TEXT = NEW.entry_signal_id
     ORDER BY f.id ASC
     LIMIT 1;

    NEW.entry_forecast_id := COALESCE(NULLIF(NEW.entry_forecast_id, ''), NULLIF(forecast_id_value, ''));
    NEW.entry_model := COALESCE(NULLIF(NEW.entry_model, ''), NULLIF(forecast_model_value, ''));
    NEW.entry_model_version := COALESCE(NULLIF(NEW.entry_model_version, ''), NULLIF(forecast_model_version_value, ''));
    NEW.entry_quote_timestamp := COALESCE(
        NULLIF(NEW.entry_quote_timestamp, ''),
        NULLIF(forecast_quote_timestamp_value, ''),
        decision_payload->>'quote_timestamp',
        decision_payload->>'source_quote_timestamp'
    );
    NEW.entry_quote_id := COALESCE(
        NULLIF(NEW.entry_quote_id, ''),
        decision_payload->>'decision_correlation_id',
        decision_payload->>'correlation_id'
    );
    NEW.entry_correlation_id := COALESCE(NULLIF(NEW.entry_correlation_id, ''), NEW.entry_quote_id);
    NEW.entry_provider := COALESCE(
        NULLIF(NEW.entry_provider, ''),
        decision_payload->>'provider'
    );
    NEW.entry_feature_snapshot := COALESCE(
        NEW.entry_feature_snapshot,
        CASE
            WHEN decision_payload IS NOT NULL AND decision_payload ? 'features'
            THEN decision_payload->'features'
            ELSE NULL
        END
    );

    IF decision_payload IS NOT NULL OR NEW.entry_forecast_id IS NOT NULL OR NEW.entry_feature_snapshot IS NOT NULL THEN
        NEW.entry_decision_snapshot := COALESCE(
            NEW.entry_decision_snapshot,
            jsonb_strip_nulls(
                jsonb_build_object(
                    'signal_id', NEW.entry_signal_id,
                    'forecast_id', NEW.entry_forecast_id,
                    'quote_id', NEW.entry_quote_id,
                    'correlation_id', NEW.entry_correlation_id,
                    'model', NEW.entry_model,
                    'model_version', NEW.entry_model_version,
                    'quote_timestamp', NEW.entry_quote_timestamp,
                    'provider', NEW.entry_provider,
                    'features', NEW.entry_feature_snapshot,
                    'oracle_decision', decision_payload
                )
            )
        );
    END IF;

    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS trg_capture_position_lot_entry_provenance ON position_lots;
CREATE TRIGGER trg_capture_position_lot_entry_provenance
BEFORE INSERT ON position_lots
FOR EACH ROW
EXECUTE FUNCTION capture_position_lot_entry_provenance();
