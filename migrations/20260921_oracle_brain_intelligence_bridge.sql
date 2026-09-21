-- Canonical, provenance-preserving intake for Oracle Brain market intelligence.
-- The feed remains research-only and cannot grant execution authority.

ALTER TABLE intelligence_events ADD COLUMN IF NOT EXISTS event_key TEXT;
ALTER TABLE intelligence_events ADD COLUMN IF NOT EXISTS source_url TEXT;
ALTER TABLE intelligence_events ADD COLUMN IF NOT EXISTS verification_status TEXT NOT NULL DEFAULT 'reported';
ALTER TABLE intelligence_events ADD COLUMN IF NOT EXISTS confidence DOUBLE PRECISION NOT NULL DEFAULT 0.5;
ALTER TABLE intelligence_events ADD COLUMN IF NOT EXISTS expires_at TEXT;
ALTER TABLE intelligence_events ADD COLUMN IF NOT EXISTS metadata JSONB NOT NULL DEFAULT '{}'::jsonb;
ALTER TABLE intelligence_events ADD COLUMN IF NOT EXISTS first_seen_at TEXT;
ALTER TABLE intelligence_events ADD COLUMN IF NOT EXISTS last_seen_at TEXT;
ALTER TABLE intelligence_events ADD COLUMN IF NOT EXISTS updated_at TEXT;
ALTER TABLE intelligence_events ADD COLUMN IF NOT EXISTS ingest_count INTEGER NOT NULL DEFAULT 1;
ALTER TABLE intelligence_events ADD COLUMN IF NOT EXISTS execution_impact TEXT NOT NULL DEFAULT 'NONE';

UPDATE intelligence_events
SET event_key = 'legacy:' || id::text
WHERE event_key IS NULL OR BTRIM(event_key) = '';

UPDATE intelligence_events
SET first_seen_at = COALESCE(first_seen_at, created_at),
    last_seen_at = COALESCE(last_seen_at, created_at),
    updated_at = COALESCE(updated_at, created_at)
WHERE first_seen_at IS NULL OR last_seen_at IS NULL OR updated_at IS NULL;

ALTER TABLE intelligence_events ALTER COLUMN event_key SET NOT NULL;

CREATE UNIQUE INDEX IF NOT EXISTS idx_intelligence_events_event_key
ON intelligence_events(event_key);

CREATE INDEX IF NOT EXISTS idx_intelligence_events_symbol_time
ON intelligence_events(symbol, event_time DESC);

CREATE INDEX IF NOT EXISTS idx_intelligence_events_category_time
ON intelligence_events(category, event_time DESC);

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'intelligence_events_verification_status_check'
    ) THEN
        ALTER TABLE intelligence_events
        ADD CONSTRAINT intelligence_events_verification_status_check
        CHECK (verification_status IN ('verified','corroborated','reported','unverified','inference'));
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'intelligence_events_confidence_check'
    ) THEN
        ALTER TABLE intelligence_events
        ADD CONSTRAINT intelligence_events_confidence_check
        CHECK (confidence >= 0.0 AND confidence <= 1.0);
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'intelligence_events_ingest_count_check'
    ) THEN
        ALTER TABLE intelligence_events
        ADD CONSTRAINT intelligence_events_ingest_count_check
        CHECK (ingest_count >= 1);
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'intelligence_events_execution_impact_check'
    ) THEN
        ALTER TABLE intelligence_events
        ADD CONSTRAINT intelligence_events_execution_impact_check
        CHECK (execution_impact = 'NONE');
    END IF;
END $$;
