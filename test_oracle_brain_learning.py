from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
import inspect

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
        "exit_price": 106.0,
        "exit_reason": "profit_target",
        "risk_snapshot": {"spread_pct": 0.001, "slippage_pct": 0.0005, "market_impact_pct": 0.0002},
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
    assert episode["outcome_snapshot"]["exit_reason"] == "profit_target"
    assert episode["outcome_snapshot"]["execution_costs"]["spread_pct"] == 0.001
    assert episode["outcome_snapshot"]["execution_costs"]["slippage_pct"] == 0.0005
    assert episode["outcome_snapshot"]["execution_costs"]["market_impact_pct"] == 0.0002


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
                "freshness_score": 0.95, "confidence": 0.98, "outcome_snapshot": {}, "tags": [],
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


def test_similar_episode_evidence_is_exact_entry_research_only():
    episodes = [
        {"episode_key": "a", "trade_id": "A", "strategy": "dip_rebound", "regime": "trend_up", "provenance_status": "exact", "feature_snapshot": {"momentum_20d": 0.20, "volume_ratio": 1.4}, "net_pnl": 5.0, "return_pct": 0.02, "mfe_pct": 3.0, "mae_pct": -1.0},
        {"episode_key": "b", "trade_id": "B", "strategy": "dip_rebound", "regime": "trend_up", "provenance_status": "exact", "feature_snapshot": {"momentum_20d": -0.50, "volume_ratio": 3.5}, "net_pnl": -2.0, "return_pct": -0.01, "mfe_pct": 0.5, "mae_pct": -3.0},
        {"episode_key": "bad", "trade_id": "BAD", "strategy": "dip_rebound", "regime": "trend_up", "provenance_status": "unknown", "feature_snapshot": {"momentum_20d": 0.21, "volume_ratio": 1.41}, "net_pnl": 99.0},
    ]
    result = learning.similar_episode_evidence({"momentum_20d": 0.21, "volume_ratio": 1.45}, episodes, strategy="dip_rebound", regime="trend_up")
    assert [row["trade_id"] for row in result] == ["A", "B"]
    assert result[0]["similarity"] > result[1]["similarity"]
    source = inspect.getsource(learning.similar_episode_evidence)
    assert "submit_order" not in source
    assert "process_signals" not in source
