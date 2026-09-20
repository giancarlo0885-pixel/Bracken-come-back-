-- Oracle Brain Learning V2: durable sources, episodes, concept links, contradictions,
-- and research queue. These tables are research-only and have no execution authority.

CREATE TABLE IF NOT EXISTS oracle_brain_sources (
    source_key TEXT PRIMARY KEY,
    source_type TEXT NOT NULL,
    provider TEXT,
    category TEXT,
    symbol TEXT,
    title TEXT NOT NULL,
    body TEXT,
    source_ref TEXT,
    observed_at TIMESTAMPTZ,
    ingested_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    source_quality DOUBLE PRECISION NOT NULL DEFAULT 0.5,
    freshness_score DOUBLE PRECISION NOT NULL DEFAULT 1.0,
    confidence DOUBLE PRECISION NOT NULL DEFAULT 0.5,
    stale_after TIMESTAMPTZ,
    status TEXT NOT NULL DEFAULT 'active',
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    execution_impact TEXT NOT NULL DEFAULT 'NONE',
    CHECK (source_quality >= 0.0 AND source_quality <= 1.0),
    CHECK (freshness_score >= 0.0 AND freshness_score <= 1.0),
    CHECK (confidence >= 0.0 AND confidence <= 1.0),
    CHECK (status IN ('active','stale','superseded','retired')),
    CHECK (execution_impact = 'NONE')
);

CREATE INDEX IF NOT EXISTS idx_oracle_brain_sources_symbol_time
ON oracle_brain_sources(symbol, observed_at DESC);

CREATE INDEX IF NOT EXISTS idx_oracle_brain_sources_category_time
ON oracle_brain_sources(category, observed_at DESC);

CREATE TABLE IF NOT EXISTS oracle_brain_episodes (
    episode_key TEXT PRIMARY KEY,
    trade_id TEXT UNIQUE,
    market TEXT NOT NULL,
    symbol TEXT NOT NULL,
    strategy TEXT NOT NULL,
    regime TEXT NOT NULL,
    entry_time TIMESTAMPTZ,
    exit_time TIMESTAMPTZ,
    net_pnl DOUBLE PRECISION NOT NULL DEFAULT 0,
    fees DOUBLE PRECISION NOT NULL DEFAULT 0,
    return_pct DOUBLE PRECISION,
    mfe_pct DOUBLE PRECISION,
    mae_pct DOUBLE PRECISION,
    provenance_status TEXT NOT NULL,
    source_quality DOUBLE PRECISION NOT NULL DEFAULT 1.0,
    freshness_score DOUBLE PRECISION NOT NULL DEFAULT 1.0,
    confidence DOUBLE PRECISION NOT NULL DEFAULT 0.9,
    feature_snapshot JSONB NOT NULL DEFAULT '{}'::jsonb,
    outcome_snapshot JSONB NOT NULL DEFAULT '{}'::jsonb,
    tags JSONB NOT NULL DEFAULT '[]'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    execution_impact TEXT NOT NULL DEFAULT 'NONE',
    CHECK (provenance_status IN ('exact','partial','unknown')),
    CHECK (source_quality >= 0.0 AND source_quality <= 1.0),
    CHECK (freshness_score >= 0.0 AND freshness_score <= 1.0),
    CHECK (confidence >= 0.0 AND confidence <= 1.0),
    CHECK (execution_impact = 'NONE')
);

CREATE INDEX IF NOT EXISTS idx_oracle_brain_episodes_context
ON oracle_brain_episodes(market, strategy, regime, exit_time DESC);

CREATE INDEX IF NOT EXISTS idx_oracle_brain_episodes_symbol
ON oracle_brain_episodes(market, symbol, exit_time DESC);

CREATE TABLE IF NOT EXISTS oracle_brain_links (
    id BIGSERIAL PRIMARY KEY,
    source_key TEXT NOT NULL,
    target_key TEXT NOT NULL,
    relation TEXT NOT NULL,
    weight DOUBLE PRECISION NOT NULL DEFAULT 0.0,
    evidence_count INTEGER NOT NULL DEFAULT 1,
    confidence DOUBLE PRECISION NOT NULL DEFAULT 0.0,
    first_observed_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    last_observed_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    execution_impact TEXT NOT NULL DEFAULT 'NONE',
    UNIQUE(source_key, target_key, relation),
    CHECK (weight >= -1.0 AND weight <= 1.0),
    CHECK (evidence_count >= 1),
    CHECK (confidence >= 0.0 AND confidence <= 1.0),
    CHECK (execution_impact = 'NONE')
);

CREATE INDEX IF NOT EXISTS idx_oracle_brain_links_source
ON oracle_brain_links(source_key, confidence DESC, evidence_count DESC);

CREATE INDEX IF NOT EXISTS idx_oracle_brain_links_target
ON oracle_brain_links(target_key, confidence DESC, evidence_count DESC);

CREATE TABLE IF NOT EXISTS oracle_brain_contradictions (
    contradiction_key TEXT PRIMARY KEY,
    subject_key TEXT NOT NULL,
    prior_entry_id BIGINT REFERENCES oracle_brain_entries(id),
    current_entry_id BIGINT REFERENCES oracle_brain_entries(id),
    prior_polarity TEXT,
    current_polarity TEXT,
    reason TEXT NOT NULL,
    severity TEXT NOT NULL DEFAULT 'medium',
    status TEXT NOT NULL DEFAULT 'active',
    detected_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    resolved_at TIMESTAMPTZ,
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    execution_impact TEXT NOT NULL DEFAULT 'NONE',
    CHECK (severity IN ('low','medium','high')),
    CHECK (status IN ('active','resolved','retired')),
    CHECK (execution_impact = 'NONE')
);

CREATE INDEX IF NOT EXISTS idx_oracle_brain_contradictions_subject
ON oracle_brain_contradictions(subject_key, status, detected_at DESC);

CREATE TABLE IF NOT EXISTS oracle_brain_research_queue (
    topic_key TEXT PRIMARY KEY,
    topic TEXT NOT NULL,
    market TEXT,
    strategy TEXT,
    regime TEXT,
    priority DOUBLE PRECISION NOT NULL DEFAULT 0.0,
    reason TEXT NOT NULL,
    evidence JSONB NOT NULL DEFAULT '{}'::jsonb,
    status TEXT NOT NULL DEFAULT 'queued',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    execution_impact TEXT NOT NULL DEFAULT 'NONE',
    CHECK (priority >= 0.0 AND priority <= 100.0),
    CHECK (status IN ('queued','in_review','resolved','retired')),
    CHECK (execution_impact = 'NONE')
);

CREATE INDEX IF NOT EXISTS idx_oracle_brain_research_queue_priority
ON oracle_brain_research_queue(status, priority DESC, updated_at DESC);

CREATE TABLE IF NOT EXISTS oracle_brain_learning_state (
    pipeline_key TEXT NOT NULL,
    market TEXT NOT NULL,
    last_source_id BIGINT,
    last_episode_exit_at TIMESTAMPTZ,
    last_sync_at TIMESTAMPTZ,
    last_result JSONB NOT NULL DEFAULT '{}'::jsonb,
    PRIMARY KEY(pipeline_key, market)
);
