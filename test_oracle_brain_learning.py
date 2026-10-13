from __future__ import annotations

from datetime import datetime, timedelta, timezone
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch
import inspect
import json

import database
import oracle_brain
import oracle_brain_learning as learning


def test_freshness_decays_with_age():
    now = datetime(2026, 9, 20, tzinfo=timezone.utc)
    fresh = learning.freshness_score(now - timedelta(hours=1), now=now, half_life_days=7)
    old = learning.freshness_score(now - timedelta(days=28), now=now, half_life_days=7)
    assert 0.9 < fresh <= 1.0
    assert 0.0 < old < fresh


def test_source_quality_distinguishes_primary_and_social_sources():
    assert learning.source_quality("SEC official") > learning.source_quality("NewsAPI")
    assert learning.source_quality("NewsAPI") > learning.source_quality("Reddit social")


def test_intelligence_source_preserves_provenance_and_verification_boundaries():
    source = learning.intelligence_source_from_row(
        {
            "id": 42,
            "event_key": "event-radar:abc",
            "category": "AI_TECHNOLOGY",
            "provider": "Reuters",
            "symbol": "NVDA",
            "title": "Nvidia capacity agreement",
            "details": {
                "fact": "A capacity agreement was announced.",
                "inference": "Supply may tighten.",
                "affected_symbols": ["NVDA", "AMD"],
                "impact_score": 82,
            },
            "event_time": "2026-09-21T12:00:00+00:00",
            "created_at": "2026-09-21T12:01:00+00:00",
            "source_url": "https://reuters.test/report",
            "verification_status": "reported",
            "confidence": 0.84,
            "metadata": {"themes": ["ai"]},
            "ingest_count": 3,
        },
        now=datetime(2026, 9, 21, 13, 0, tzinfo=timezone.utc),
    )

    assert source["source_key"] == "intel:event-radar:abc"
    assert source["source_ref"] == "https://reuters.test/report"
    assert source["body"] == "A capacity agreement was announced."
    assert source["metadata"]["fact"] == "A capacity agreement was announced."
    assert source["metadata"]["inference"] == "Supply may tighten."
    assert source["metadata"]["affected_symbols"] == ["NVDA", "AMD"]
    assert source["metadata"]["verification_status"] == "reported"
    assert source["confidence"] <= 0.75
    assert source["execution_impact"] == "NONE"

    unverified = learning.intelligence_source_from_row(
        {
            "id": 43,
            "category": "QUANTUM_TECHNOLOGY",
            "provider": "Unknown blog",
            "title": "Unsupported claim",
            "verification_status": "verified",
            "confidence": 1.0,
            "created_at": "2026-09-21T12:00:00+00:00",
        },
        now=datetime(2026, 9, 21, 13, 0, tzinfo=timezone.utc),
    )
    assert unverified["metadata"]["verification_status"] == "unverified"
    assert unverified["confidence"] <= 0.35


def test_episode_requires_exact_entry_provenance():
    base = {
        "trade_id": "T1",
        "market": "crypto",
        "symbol": "BTC-USD",
        "strategy": "oracle_council_v3",
        "regime": "trend_up__high_vol",
        "entry_time": "2026-09-19T00:00:00+00:00",
        "exit_time": "2026-09-19T02:00:00+00:00",
        "entry_price": 100.0,
        "quantity": 2.0,
        "net_pnl": 10.0,
        "gross_pnl": 11.0,
        "fees": 1.0,
        "round_trip_net_pnl": 10.0,
        "round_trip_fees": 1.0,
        "cost_provenance": "exact_lot",
        "exit_price": 106.0,
        "mfe_pct": 8.0,
        "mae_pct": -3.0,
        "excursion_sample_count": 12,
    }
    assert learning.episode_from_row(base) is None
    assert learning.episode_from_row({**base, "entry_signal_id": "S1"}) is None

    episode = learning.episode_from_row(
        {
            **base,
            "entry_signal_id": "S1",
            "entry_decision_id": "D1",
            "entry_forecast_id": "F1",
            "entry_quote_id": "Q1",
            "feature_snapshot": {"momentum_20d": 0.15, "volume_ratio": 1.4},
        }
    )
    assert episode is not None
    assert episode["provenance_status"] == "exact"
    assert episode["return_pct"] == 5.0
    assert episode["outcome_snapshot"]["outcome"] == "positive"
    assert episode["outcome_snapshot"]["entry_signal_id"] == "S1"
    assert episode["outcome_snapshot"]["gross_pnl"] == 11.0
    assert episode["outcome_snapshot"]["net_pnl"] == 10.0
    assert episode["outcome_snapshot"]["fees"] == 1.0
    assert episode["outcome_snapshot"]["entry_price"] == 100.0
    assert episode["outcome_snapshot"]["exit_price"] == 106.0
    assert episode["outcome_snapshot"]["holding_seconds"] == 7200.0
    assert episode["outcome_snapshot"]["mfe_pct"] == 8.0
    assert episode["outcome_snapshot"]["mae_pct"] == -3.0
    assert episode["outcome_snapshot"]["accounting_version"] == "exact_round_trip_v1"
    assert episode["outcome_snapshot"]["cost_provenance"] == "exact_lot"


def _full_cost_trade():
    return {
        "trade_id": "full-cost-trade", "entry_signal_id": "entry-signal",
        "feature_snapshot": {"entry_pattern": "dip_rebound"},
        "market": "cash", "symbol": "MSFT", "strategy": "dip_rebound",
        "regime": "range", "entry_time": "2026-09-19T00:00:00+00:00",
        "exit_time": "2026-09-19T02:00:00+00:00",
        "entry_price": 100.0, "exit_price": 100.3, "quantity": 1.0,
        "net_pnl": 0.1, "fees": 0.2, "gross_pnl": 0.3,
        "round_trip_net_pnl": -0.15, "round_trip_fees": 0.45,
        "cost_provenance": "exact_lot",
    }


def test_episode_accounts_for_entry_fees_that_turn_exit_winner_into_loss():
    episode = learning.episode_from_row(_full_cost_trade())
    assert episode is not None
    assert episode["net_pnl"] == -0.15
    assert episode["fees"] == 0.45
    assert episode["return_pct"] == -0.15
    assert episode["outcome_snapshot"]["outcome"] == "negative"
    assert abs(episode["outcome_snapshot"]["gross_pnl"] - 0.3) < 1e-10
    assert "outcome:negative" in episode["tags"]
    assert "outcome:positive" not in episode["tags"]


def test_episode_rejects_missing_inexact_or_nonfinite_round_trip_accounting():
    trade = _full_cost_trade()
    for provenance in (None, "", "incomplete_entry_provenance", "ambiguous_or_missing_entry_lot"):
        assert learning.episode_from_row({**trade, "cost_provenance": provenance}) is None
    for field in ("round_trip_net_pnl", "round_trip_fees"):
        for invalid in (None, "missing", float("nan"), float("inf"), float("-inf")):
            assert learning.episode_from_row({**trade, field: invalid}) is None
        absent = dict(trade)
        absent.pop(field)
        assert learning.episode_from_row(absent) is None
    assert learning.episode_from_row({**trade, "round_trip_fees": -0.01}) is None
    free = learning.episode_from_row({**trade, "round_trip_fees": 0, "round_trip_net_pnl": 0})
    assert free is not None
    assert free["outcome_snapshot"]["outcome"] == "flat"


def test_episode_excursions_require_forward_samples_and_finite_measurements():
    trade = {**_full_cost_trade(), "mfe_pct": 0.0, "mae_pct": 0.0}
    for sample_count in (None, 0, -1, float("nan"), float("inf")):
        episode = learning.episode_from_row({**trade, "excursion_sample_count": sample_count})
        assert episode is not None
        assert episode["mfe_pct"] is None
        assert episode["mae_pct"] is None
        assert episode["outcome_snapshot"]["mfe_pct"] is None
        assert episode["outcome_snapshot"]["mae_pct"] is None
    for invalid in (None, "missing", float("nan"), float("inf"), float("-inf")):
        episode = learning.episode_from_row({
            **trade, "excursion_sample_count": 3, "mfe_pct": invalid, "mae_pct": invalid,
        })
        assert episode is not None
        assert episode["mfe_pct"] is None
        assert episode["mae_pct"] is None
    measured = learning.episode_from_row({**trade, "excursion_sample_count": 3})
    assert measured["mfe_pct"] == 0.0
    assert measured["mae_pct"] == 0.0


def test_episode_requires_completed_valid_chronological_timestamps():
    trade = _full_cost_trade()
    now = datetime(2026, 9, 20, tzinfo=timezone.utc)
    for field in ("entry_time", "exit_time"):
        for invalid in (None, "", "invalid", "2026-09-21T00:00:00+00:00"):
            assert learning.episode_from_row({**trade, field: invalid}, now=now) is None
        absent = dict(trade)
        absent.pop(field)
        assert learning.episode_from_row(absent, now=now) is None
    assert learning.episode_from_row({**trade, "exit_time": "2026-09-18T00:00:00+00:00"}, now=now) is None
    assert learning.episode_from_row(trade, now=now) is not None


def test_retrieved_brain_context_excludes_legacy_exit_only_cost_claims():
    verified = {"episode_key": "trade:verified", "net_pnl": -0.15, "fees": 0.45}
    legacy = {"episode_key": "trade:legacy", "net_pnl": 0.1, "fees": 0.2}
    queries = []

    class Conn:
        def execute(self, query, params=()):
            queries.append((query, params))
            if "FROM oracle_brain_episodes" in query:
                gated = (
                    "outcome_snapshot->>'accounting_version'='exact_round_trip_v1'" in query
                    and "outcome_snapshot->>'cost_provenance'='exact_lot'" in query
                )
                return _LearningResult([verified] if gated else [verified, legacy])
            return _LearningResult()

    @contextmanager
    def connect():
        yield Conn()

    with patch.object(database, "connect", connect):
        context = learning.retrieve_brain_context(
            market="cash", symbol="msft", strategy="dip_rebound", regime="range", limit=2,
        )
    assert context["episodes"] == [verified]
    assert context["execution_impact"] == "NONE"
    assert queries[0][1] == ("cash", "MSFT", "dip_rebound", "range", 2)
    assert all(query.strip().upper().startswith("SELECT") for query, _ in queries)


class _LearningResult:
    def __init__(self, rows=()):
        self.rows = list(rows)

    def fetchall(self):
        return self.rows

    def fetchone(self):
        return self.rows[0] if self.rows else None


class _TradeLearningConnection:
    def __init__(self, trades, existing=None):
        self.trades = trades
        self.existing = existing
        self.writes = []
        self.queries = []

    def execute(self, query, params=()):
        self.queries.append((query, params))
        if "FROM paper_regime_trade_metrics m" in query:
            assert "m.round_trip_net_pnl,m.round_trip_fees" in query
            assert "m.net_pnl,m.fees" not in query
            assert params == ("cash", 1)
            return _LearningResult(self.trades)
        if query.startswith("SELECT net_pnl,fees,return_pct,outcome_snapshot"):
            return _LearningResult([self.existing] if self.existing else [])
        self.writes.append((query, params))
        if "INSERT INTO oracle_brain_episodes(" in query:
            self.existing = {
                "net_pnl": params[8], "fees": params[9], "return_pct": params[10],
                "outcome_snapshot": json.loads(params[17]),
            }
        return _LearningResult()


def test_episode_sync_uses_canonical_costs_without_duplicate_links():
    conn = _TradeLearningConnection([_full_cost_trade()])
    assert learning._sync_trade_episodes(conn, "cash", limit=1) == (1, 0, 1)
    assert conn.existing["net_pnl"] == -0.15
    assert conn.existing["fees"] == 0.45
    outcome_links = [params for query, params in conn.writes if "INSERT INTO oracle_brain_links(" in query]
    assert len(outcome_links) == 3
    produced = [params for params in outcome_links if params[2] == "produced"]
    assert produced[0][1] == "outcome:negative"
    assert learning._sync_trade_episodes(conn, "cash", limit=1) == (1, 0, 0)
    assert len([1 for query, _ in conn.writes if "INSERT INTO oracle_brain_links(" in query]) == 3


def test_bounded_episode_sync_preserves_prior_accounting_snapshot_on_correction():
    previous = {"outcome": "positive", "net_pnl": 0.1, "fees": 0.2, "entry_signal_id": "entry-signal"}
    conn = _TradeLearningConnection([_full_cost_trade()], {
        "net_pnl": 0.1, "fees": 0.2, "return_pct": 0.1, "outcome_snapshot": previous,
    })
    assert learning._sync_trade_episodes(conn, "cash", limit=1) == (1, 0, 0)
    revised = conn.existing["outcome_snapshot"]
    revision = revised["accounting_revision"]
    assert revised["outcome"] == "negative"
    assert revision["previous_outcome_snapshot"] == previous
    assert revision["previous_net_pnl"] == 0.1
    assert revision["previous_fees"] == 0.2
    assert revision["previous_return_pct"] == 0.1
    assert revision["source_ref"] == "paper_regime_trade_metrics:full-cost-trade"
    assert learning._sync_trade_episodes(conn, "cash", limit=1) == (1, 0, 0)
    assert conn.existing["outcome_snapshot"]["accounting_revision"] == revision
    assert not any("DELETE" in query for query, _ in conn.queries)
    assert not any("INSERT INTO oracle_brain_links(" in query for query, _ in conn.writes)


def test_episode_sync_does_not_publish_unknown_costs_as_exact():
    conn = _TradeLearningConnection([{**_full_cost_trade(), "round_trip_net_pnl": None}])
    assert learning._sync_trade_episodes(conn, "cash", limit=1) == (0, 1, 0)
    assert conn.existing is None
    assert not any("INSERT INTO oracle_brain_episodes(" in query for query, _ in conn.writes)


def test_regime_lessons_exclude_unmarked_historical_exit_only_outcomes():
    class Conn:
        def execute(self, query, params=()):
            assert "outcome_snapshot->>'accounting_version'='exact_round_trip_v1'" in query
            assert "outcome_snapshot->>'cost_provenance'='exact_lot'" in query
            return _LearningResult()

    assert learning._sync_regime_lessons_and_queue(Conn(), "cash") == (0, 0)


def test_mature_lesson_supersedes_old_exit_only_claim_without_erasing_it():
    writes = []

    class Conn:
        def execute(self, query, params=()):
            if "SELECT id,metadata,created_at FROM oracle_brain_entries" in query:
                return _LearningResult([{"id": 7, "metadata": {"samples": 30, "claim_polarity": "positive"}}])
            writes.append((query, params))
            return _LearningResult([{"id": 8}]) if "RETURNING id" in query else _LearningResult()

    summary = learning.regime_summary([
        {"net_pnl": 1.0, "fees": 0.45, "freshness_score": 1.0}
    ] * learning.MIN_MATURE_SAMPLES)
    assert learning._insert_regime_lesson(Conn(), "cash", "dip_rebound", "range", summary) == 8
    assert any("SET status='superseded'" in query and params == (7,) for query, params in writes)
    entry = next(params for query, params in writes if "INSERT INTO oracle_brain_entries(" in query)
    metadata = json.loads(entry[5])
    assert entry[4] == 7
    assert metadata["accounting_version"] == "exact_round_trip_v1"
    assert metadata["cost_provenance"] == "exact_lot"
    assert not any("DELETE" in query for query, _ in writes)


def test_regime_summary_requires_mature_sample_depth():
    small = [{"net_pnl": 1.0, "fees": 0.1, "freshness_score": 1.0}] * (learning.MIN_MATURE_SAMPLES - 1)
    assert learning.regime_summary(small)["polarity"] == "insufficient"

    positive = [{"net_pnl": 1.0, "fees": 0.1, "freshness_score": 0.9}] * learning.MIN_MATURE_SAMPLES
    summary = learning.regime_summary(positive)
    assert summary["polarity"] == "positive"
    assert summary["samples"] == learning.MIN_MATURE_SAMPLES
    assert summary["expectancy"] > 0


def test_contradiction_priority_is_high():
    normal = learning.research_priority(5)
    contradiction = learning.research_priority(100, contradictory=True)
    assert contradiction >= 92.0
    assert contradiction > normal


def test_brain_snapshot_surfaces_learning_memory_select_only(monkeypatch):
    monkeypatch.setenv("EXECUTION_MODE", "paper")
    monkeypatch.setenv("ENABLE_BROKER_SUBMISSION", "false")
    monkeypatch.setenv("LIVE_TRADING_ARMED", "false")

    queries: list[str] = []

    def fetch(query: str, params=()):
        queries.append(query.strip().lower())
        if "from oracle_brain_sources" in query.lower():
            return [{
                "source_key": "intel:1", "source_type": "intelligence_event",
                "provider": "SEC official", "category": "filing", "symbol": "ABC",
                "title": "Filing", "source_ref": "intelligence_events:1",
                "observed_at": "2026-09-20T00:00:00+00:00", "source_quality": 0.97,
                "freshness_score": 0.9, "confidence": 0.95, "status": "active", "metadata": {},
            }]
        if "from oracle_brain_episodes" in query.lower():
            return [{
                "episode_key": "trade:T1", "trade_id": "T1", "market": "cash", "symbol": "ABC",
                "strategy": "dip_rebound", "regime": "trend_up", "entry_time": None,
                "exit_time": "2026-09-20T00:00:00+00:00", "net_pnl": 5.0, "fees": 0.2,
                "return_pct": 1.0, "mfe_pct": 2.0, "mae_pct": -0.5,
                "provenance_status": "exact", "source_quality": 1.0,
                "freshness_score": 0.95, "confidence": 0.98,
                "outcome_snapshot": {"accounting_version": "exact_round_trip_v1", "cost_provenance": "exact_lot"}, "tags": [],
            }]
        if "from oracle_brain_links" in query.lower():
            return [{
                "source_key": "strategy:dip-rebound", "target_key": "regime:trend-up",
                "relation": "observed_in", "weight": 0.0, "evidence_count": 8,
                "confidence": 0.85, "last_observed_at": "2026-09-20T00:00:00+00:00", "metadata": {},
            }]
        if "from oracle_brain_contradictions" in query.lower():
            return [{
                "contradiction_key": "flip:x", "subject_key": "regime:x",
                "prior_polarity": "positive", "current_polarity": "negative",
                "reason": "new evidence", "severity": "high", "status": "active",
                "detected_at": "2026-09-20T00:00:00+00:00", "metadata": {},
            }]
        if "from oracle_brain_research_queue" in query.lower():
            return [{
                "topic_key": "cohort:x", "topic": "dip / trend", "market": "cash",
                "strategy": "dip", "regime": "trend", "priority": 92.0,
                "reason": "contradiction", "evidence": {}, "status": "queued",
                "updated_at": "2026-09-20T00:00:00+00:00",
            }]
        return []

    snapshot = oracle_brain.build_oracle_brain_snapshot(fetch)
    assert snapshot["summary"]["knowledge_sources"] == 1
    assert snapshot["summary"]["exact_episodes"] == 1
    assert snapshot["summary"]["concept_links"] == 1
    assert snapshot["summary"]["high_confidence_links"] == 1
    assert snapshot["summary"]["active_contradictions"] == 1
    assert snapshot["summary"]["research_topics"] == 1
    assert all(query.startswith("select") for query in queries)


def test_learning_schema_and_runtime_are_research_only():
    migration = Path("migrations/20260920_oracle_brain_learning_v2.sql").read_text(encoding="utf-8")
    source = Path("oracle_brain_learning.py").read_text(encoding="utf-8")
    worker = Path("market_worker.py").read_text(encoding="utf-8")

    for table in (
        "oracle_brain_sources",
        "oracle_brain_episodes",
        "oracle_brain_links",
        "oracle_brain_contradictions",
        "oracle_brain_research_queue",
        "oracle_brain_learning_state",
    ):
        assert table in migration
    assert "CHECK (execution_impact = 'NONE')" in migration
    assert "submit_order(" not in source
    assert "process_signals(" not in source
    assert "ENABLE_BROKER_SUBMISSION=true" not in source
    assert "LIVE_TRADING_ARMED=true" not in source
    assert "brain_learning_executor" in worker
    assert "_run_brain_learning_sync" in worker
    assert "next_brain_learning_due = time.monotonic()" in worker
    assert "next_brain_learning_due = time.monotonic() + 20.0" not in worker
    assert "execution_impact=NONE" in worker
    assert "existing_episode" in source
    assert "if not existing_episode" in source
    assert "new_exact_episodes" in source
    assert "_sync_curated_crypto_history" in source
    for table in (
        "oracle_brain_entries",
        "oracle_brain_sources",
        "oracle_brain_episodes",
        "oracle_brain_links",
        "oracle_brain_contradictions",
        "oracle_brain_research_queue",
        "oracle_brain_learning_state",
    ):
        assert table in database.CANONICAL_PROTECTED_TABLES


def test_brain_page_visualizes_sources_episodes_links_and_queue():
    source = Path("pages/3_Oracle_Brain.py").read_text(encoding="utf-8")
    assert 'snapshot["sources"]' in source
    assert 'snapshot["episodes"]' in source
    assert 'snapshot["concept_links"]' in source
    assert 'snapshot["research_queue"]' in source
    assert "Active contradictions" in source
    assert "Exact trade episodes" in source
    assert "execution authority: NONE" in source


def test_brain_lifecycle_provenance_joins_canonical_trade_ledger():
    source = Path("pages/3_Oracle_Brain.py").read_text(encoding="utf-8")
    assert "FROM paper_regime_trade_metrics m" in source
    assert "LEFT JOIN trade_ledger t ON t.trade_id=m.trade_id" in source
    assert "t.entry_signal_id IS NOT NULL" in source
    assert "t.feature_snapshot IS NOT NULL" in source
    assert "WHERE entry_signal_id IS NOT NULL AND feature_snapshot IS NOT NULL" not in source
