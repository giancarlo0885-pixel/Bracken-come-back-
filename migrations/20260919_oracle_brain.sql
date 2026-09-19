-- Oracle Brain: append-only engineering/research knowledge ledger.
-- This table stores durable lessons and experiment context. It has no trading
-- execution authority and is deliberately separate from order/execution tables.

CREATE TABLE IF NOT EXISTS oracle_brain_entries (
    id BIGSERIAL PRIMARY KEY,
    brain_key TEXT NOT NULL,
    category TEXT NOT NULL,
    title TEXT NOT NULL,
    body TEXT NOT NULL,
    evidence_type TEXT NOT NULL DEFAULT 'engineering',
    evidence_ref TEXT,
    confidence DOUBLE PRECISION NOT NULL DEFAULT 1.0,
    status TEXT NOT NULL DEFAULT 'active',
    execution_impact TEXT NOT NULL DEFAULT 'NONE',
    supersedes_id BIGINT REFERENCES oracle_brain_entries(id),
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CHECK (confidence >= 0.0 AND confidence <= 1.0),
    CHECK (execution_impact = 'NONE'),
    CHECK (status IN ('active','superseded','retired'))
);

CREATE INDEX IF NOT EXISTS idx_oracle_brain_category_created
ON oracle_brain_entries(category, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_oracle_brain_key_status
ON oracle_brain_entries(brain_key, status, created_at DESC);

CREATE UNIQUE INDEX IF NOT EXISTS uq_oracle_brain_active_key
ON oracle_brain_entries(brain_key)
WHERE status='active';

INSERT INTO oracle_brain_entries(
    brain_key, category, title, body, evidence_type, evidence_ref, confidence
)
SELECT
    'core.architecture.pipeline',
    'architecture',
    'Canonical Oracle decision pipeline',
    'Market data and provider evidence feed features, Market Memory, scenario/radar/global intelligence and Super Hybrid. Council and downstream capital/risk/execution-capacity gates remain authoritative. Outcomes must return through canonical trade provenance before they are used as learning evidence.',
    'code',
    'oracle_intelligence.py;hybrid_confluence.py;trade_ledger',
    1.0
WHERE NOT EXISTS (
    SELECT 1 FROM oracle_brain_entries
    WHERE brain_key='core.architecture.pipeline' AND status='active'
);

INSERT INTO oracle_brain_entries(
    brain_key, category, title, body, evidence_type, evidence_ref, confidence
)
SELECT
    'core.evidence.no_invention',
    'invariant',
    'Never invent missing evidence',
    'Unknown, missing, stale or unproven evidence must remain unknown. Do not fabricate feature values, trade attribution, provenance, price history, fills, profitability, or model validation to complete a decision path.',
    'engineering',
    'CODEX_ORACLE_DEEP_READINESS_AUDIT.md;market_memory.py',
    1.0
WHERE NOT EXISTS (
    SELECT 1 FROM oracle_brain_entries
    WHERE brain_key='core.evidence.no_invention' AND status='active'
);

INSERT INTO oracle_brain_entries(
    brain_key, category, title, body, evidence_type, evidence_ref, confidence
)
SELECT
    'core.hybrid.bounded',
    'invariant',
    'Super Hybrid is a bounded evidence layer',
    'Super Hybrid may shape paper opportunity quality through bounded adjustments, but it cannot approve a trade on its own, bypass explicit vetoes, override capital/risk/execution gates, or arm live trading.',
    'code',
    'hybrid_confluence.py;oracle_intelligence.py',
    1.0
WHERE NOT EXISTS (
    SELECT 1 FROM oracle_brain_entries
    WHERE brain_key='core.hybrid.bounded' AND status='active'
);

INSERT INTO oracle_brain_entries(
    brain_key, category, title, body, evidence_type, evidence_ref, confidence
)
SELECT
    'core.learning.provenance',
    'invariant',
    'Learning requires exact entry provenance',
    'Closed-trade learning should use immutable entry-time signal, feature, quote, forecast, decision and lot provenance. Later decisions must never be substituted for missing entry evidence.',
    'code',
    'market_memory.py;trade_ledger;position_lots',
    1.0
WHERE NOT EXISTS (
    SELECT 1 FROM oracle_brain_entries
    WHERE brain_key='core.learning.provenance' AND status='active'
);

INSERT INTO oracle_brain_entries(
    brain_key, category, title, body, evidence_type, evidence_ref, confidence
)
SELECT
    'core.research.promotion',
    'promotion',
    'Promote only demonstrated incremental value',
    'New signals, parameters, models and agents should begin in shadow or paper measurement. Promotion requires sufficient samples, positive post-cost out-of-sample contribution, stable provenance, and no regression in accounting, execution integrity or existing safety boundaries.',
    'engineering',
    'paper_regime_economics_shadow.py;paper_strategy_economics.py',
    1.0
WHERE NOT EXISTS (
    SELECT 1 FROM oracle_brain_entries
    WHERE brain_key='core.research.promotion' AND status='active'
);

INSERT INTO oracle_brain_entries(
    brain_key, category, title, body, evidence_type, evidence_ref, confidence
)
SELECT
    'core.live.separation',
    'invariant',
    'Paper research and live-money activation remain separate',
    'Research, visualization, learning and paper optimization must not implicitly enable broker submission or live trading. Live-money activation is a separate operational state with its own controls and evidence requirements.',
    'engineering',
    'config.py;paper_strategy_execution_guard.py',
    1.0
WHERE NOT EXISTS (
    SELECT 1 FROM oracle_brain_entries
    WHERE brain_key='core.live.separation' AND status='active'
);
