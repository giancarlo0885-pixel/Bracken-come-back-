-- Additive only: no historical backfill or rewrite. PostgreSQL built-in SHA256;
-- no extension, legacy constraint change, or scan/index of existing evidence.
CREATE TABLE IF NOT EXISTS paper_brain_snapshots (
    brain_snapshot_id TEXT PRIMARY KEY,
    source_forecast_row_id BIGINT NOT NULL REFERENCES forecasts(id),
    source_signal_id BIGINT NOT NULL REFERENCES signals(id),
    brain_snapshot_hash TEXT NOT NULL,
    canonical_snapshot TEXT NOT NULL CHECK(octet_length(canonical_snapshot)<=1048576),
    created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    created_xid XID8 NOT NULL DEFAULT pg_current_xact_id(),
    CHECK((canonical_snapshot::jsonb->>'contract') IS NOT DISTINCT FROM 'paper-brain-input-v1'),
    CHECK(brain_snapshot_hash=encode(sha256(convert_to(canonical_snapshot,'UTF8')),'hex'))
);
CREATE INDEX IF NOT EXISTS paper_brain_snapshot_forecast_idx ON paper_brain_snapshots(source_forecast_row_id);
CREATE INDEX IF NOT EXISTS paper_brain_snapshot_signal_idx ON paper_brain_snapshots(source_signal_id);
CREATE TABLE IF NOT EXISTS paper_brain_decisions (
    decision_id TEXT PRIMARY KEY,
    market TEXT NOT NULL CHECK(market IN ('cash','crypto')),
    symbol TEXT NOT NULL,
    signal_id TEXT NOT NULL,
    forecast_id TEXT NOT NULL,
    canonical_input TEXT NOT NULL CHECK(octet_length(canonical_input)<=1048576),
    decision_input_hash TEXT NOT NULL,
    canonical_output TEXT NOT NULL CHECK(octet_length(canonical_output)<=262144),
    decided_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    decision_xid XID8 NOT NULL DEFAULT pg_current_xact_id(),
    CHECK(decision_input_hash=encode(sha256(convert_to(canonical_input,'UTF8')),'hex'))
);
CREATE TABLE IF NOT EXISTS paper_brain_consumption_receipts (
    decision_id TEXT PRIMARY KEY REFERENCES paper_brain_decisions(decision_id) DEFERRABLE INITIALLY DEFERRED,
    forecast_id TEXT NOT NULL,
    brain_snapshot_id TEXT NOT NULL REFERENCES paper_brain_snapshots(brain_snapshot_id),
    brain_snapshot_hash TEXT NOT NULL,
    feature_version TEXT NOT NULL,
    model_version TEXT NOT NULL,
    consumed_at TIMESTAMPTZ NOT NULL,
    decision_input_hash TEXT NOT NULL
);
-- Each decision and its receipt must commit together in either insert order.
DO $$ BEGIN
IF NOT EXISTS(SELECT 1 FROM pg_constraint WHERE conrelid='paper_brain_decisions'::regclass
    AND conname='paper_brain_decision_receipt_fk') THEN
ALTER TABLE paper_brain_decisions ADD CONSTRAINT paper_brain_decision_receipt_fk
    FOREIGN KEY(decision_id) REFERENCES paper_brain_consumption_receipts(decision_id)
    DEFERRABLE INITIALLY DEFERRED;
END IF; END $$;
CREATE OR REPLACE FUNCTION paper_brain_fill_link_valid() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF NOT EXISTS(
        SELECT 1 FROM paper_brain_decisions d
        JOIN paper_fills f ON f.fill_id=NEW.fill_id AND f.market=d.market AND f.symbol=d.symbol AND f.side='BUY'
        JOIN paper_fill_lot_links a ON a.fill_id=f.fill_id AND a.lot_id=NEW.lot_id AND a.ledger_trade_id=NEW.ledger_trade_id
        JOIN position_lots l ON l.lot_id=a.lot_id AND l.entry_decision_id=d.decision_id AND l.entry_forecast_id=d.forecast_id
        JOIN trade_ledger t ON t.trade_id=a.ledger_trade_id AND t.entry_decision_id=d.decision_id AND t.entry_forecast_id=d.forecast_id
        WHERE d.decision_id=NEW.decision_id AND t.market=d.market AND l.market=d.market
          AND t.symbol=d.symbol AND l.symbol=d.symbol
          AND t.entry_signal_id=d.signal_id AND l.entry_signal_id=d.signal_id
          AND t.broker_mode='PAPER' AND t.account_environment='PAPER'
          AND l.broker_mode='PAPER' AND l.account_environment='PAPER'
          AND d.decided_at<=NULLIF(t.entry_time,'')::timestamptz
          AND t.entry_time::timestamptz=l.opened_at::timestamptz
          AND t.entry_time::timestamptz<=f.created_at::timestamptz)
    THEN RAISE EXCEPTION 'Brain receipt does not match exact entry fill/lot/ledger'; END IF;
    RETURN NEW;
END; $$;
CREATE TABLE IF NOT EXISTS paper_brain_fill_links (
    fill_id TEXT PRIMARY KEY REFERENCES paper_fills(fill_id),
    lot_id TEXT NOT NULL UNIQUE REFERENCES position_lots(lot_id),
    ledger_trade_id TEXT NOT NULL UNIQUE REFERENCES trade_ledger(trade_id),
    decision_id TEXT NOT NULL UNIQUE REFERENCES paper_brain_decisions(decision_id)
);
CREATE OR REPLACE TRIGGER paper_brain_exact_fill_link BEFORE INSERT ON paper_brain_fill_links
    FOR EACH ROW EXECUTE FUNCTION paper_brain_fill_link_valid();
CREATE OR REPLACE FUNCTION paper_brain_server_time() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF TG_TABLE_NAME='paper_brain_snapshots' THEN
        IF NEW.created_xid<>pg_current_xact_id() OR NEW.created_at>clock_timestamp()
            THEN RAISE EXCEPTION 'invalid snapshot transaction/time'; END IF;
        NEW.created_at:=clock_timestamp();
    ELSE
        IF NEW.decision_xid<>pg_current_xact_id() OR NEW.decided_at>clock_timestamp()
            THEN RAISE EXCEPTION 'invalid decision transaction/time'; END IF;
        NEW.decided_at:=clock_timestamp();
    END IF;
    RETURN NEW;
END; $$;
CREATE OR REPLACE TRIGGER paper_brain_snapshot_time BEFORE INSERT ON paper_brain_snapshots
    FOR EACH ROW EXECUTE FUNCTION paper_brain_server_time();
CREATE OR REPLACE TRIGGER paper_brain_decision_time BEFORE INSERT ON paper_brain_decisions
    FOR EACH ROW EXECUTE FUNCTION paper_brain_server_time();
CREATE OR REPLACE FUNCTION paper_brain_evidence_append_only() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN RAISE EXCEPTION 'paper Brain evidence is append-only'; END;
$$;
CREATE OR REPLACE TRIGGER paper_brain_snapshots_immutable BEFORE UPDATE OR DELETE OR TRUNCATE ON paper_brain_snapshots
    FOR EACH STATEMENT EXECUTE FUNCTION paper_brain_evidence_append_only();
CREATE OR REPLACE TRIGGER paper_brain_decisions_immutable BEFORE UPDATE OR DELETE OR TRUNCATE ON paper_brain_decisions
    FOR EACH STATEMENT EXECUTE FUNCTION paper_brain_evidence_append_only();
CREATE OR REPLACE TRIGGER paper_brain_receipts_immutable BEFORE UPDATE OR DELETE OR TRUNCATE ON paper_brain_consumption_receipts
    FOR EACH STATEMENT EXECUTE FUNCTION paper_brain_evidence_append_only();
CREATE OR REPLACE TRIGGER paper_brain_links_immutable BEFORE UPDATE OR DELETE OR TRUNCATE ON paper_brain_fill_links
    FOR EACH STATEMENT EXECUTE FUNCTION paper_brain_evidence_append_only();
CREATE OR REPLACE FUNCTION paper_brain_receipt_valid() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE s paper_brain_snapshots; d paper_brain_decisions; b jsonb; i jsonb;
BEGIN
    SELECT * INTO STRICT s FROM paper_brain_snapshots WHERE brain_snapshot_id=NEW.brain_snapshot_id;
    SELECT * INTO STRICT d FROM paper_brain_decisions WHERE decision_id=NEW.decision_id;
    b:=s.canonical_snapshot::jsonb; i:=d.canonical_input::jsonb;
    IF NEW.brain_snapshot_hash<>s.brain_snapshot_hash OR NEW.decision_input_hash<>d.decision_input_hash
       OR NEW.forecast_id<>d.forecast_id
       OR s.created_xid=d.decision_xid
       OR s.created_at>NEW.consumed_at OR NEW.consumed_at>d.decided_at OR d.decided_at>clock_timestamp()
       OR b->>'market' IS DISTINCT FROM d.market OR b->>'symbol' IS DISTINCT FROM d.symbol
       OR b->>'signal_id' IS DISTINCT FROM d.signal_id OR b->>'forecast_id' IS DISTINCT FROM d.forecast_id
       OR i->>'brain_snapshot_id' IS DISTINCT FROM NEW.brain_snapshot_id
       OR i->>'brain_snapshot_hash' IS DISTINCT FROM NEW.brain_snapshot_hash
       OR i->>'feature_version' IS DISTINCT FROM NEW.feature_version
       OR i->>'model_version' IS DISTINCT FROM NEW.model_version
       OR i->'arguments'->'signal'->>'signal_id' IS DISTINCT FROM d.signal_id
       OR i->'arguments'->'signal'->>'forecast_id' IS DISTINCT FROM d.forecast_id
       OR NEW.feature_version='' OR NEW.model_version=''
    THEN RAISE EXCEPTION 'inconsistent or future Brain consumption evidence'; END IF;
    RETURN NEW;
END;
$$;
DO $$ BEGIN
IF NOT EXISTS(SELECT 1 FROM pg_trigger WHERE tgrelid='paper_brain_consumption_receipts'::regclass
    AND tgname='paper_brain_receipt_relationships') THEN
CREATE CONSTRAINT TRIGGER paper_brain_receipt_relationships
    AFTER INSERT ON paper_brain_consumption_receipts DEFERRABLE INITIALLY DEFERRED
    FOR EACH ROW EXECUTE FUNCTION paper_brain_receipt_valid();
END IF; END $$;
