from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

import oracle_brain
from oracle_brain_component import render_oracle_brain_component


def _fetch(query: str, params=()):
    if "FROM oracle_brain_entries" in query:
        return [
            {
                "id": 1,
                "brain_key": "experiment.test",
                "category": "experiment",
                "title": "Test",
                "body": "Shadow only",
                "evidence_type": "experiment",
                "evidence_ref": "test",
                "confidence": 0.8,
                "status": "active",
                "execution_impact": "NONE",
                "supersedes_id": None,
                "metadata": {},
                "created_at": "2026-09-19T00:00:00+00:00",
            }
        ]
    if "FROM paper_regime_trade_metrics" in query:
        return [
            {
                "strategy": "oracle_council_v3",
                "regime": "trend_up__high_vol",
                "samples": 100,
                "net_pnl": -10.0,
                "fees": 2.0,
                "expectancy": -0.10,
                "avg_mfe_pct": 0.4,
                "avg_mae_pct": -0.7,
                "excursion_trades": 100,
            },
            {
                "strategy": "dip_rebound",
                "regime": "range__low_vol",
                "samples": 45,
                "net_pnl": 12.0,
                "fees": 1.0,
                "expectancy": 0.266,
                "avg_mfe_pct": 0.8,
                "avg_mae_pct": -0.2,
                "excursion_trades": 45,
            },
        ]
    if "FROM market_worker_status" in query:
        return [{"market": "crypto", "status": "running", "execution_mode": "paper"}]
    return []


def test_brain_snapshot_is_read_only_and_surfaces_mature_economics(monkeypatch):
    monkeypatch.setenv("EXECUTION_MODE", "paper")
    monkeypatch.setenv("ENABLE_BROKER_SUBMISSION", "false")
    monkeypatch.setenv("LIVE_TRADING_ARMED", "false")

    snapshot = oracle_brain.build_oracle_brain_snapshot(_fetch)

    assert snapshot["read_only"] is True
    assert snapshot["execution_authority"] == "NONE"
    assert snapshot["safety"]["safe_research_boundary"] is True
    assert snapshot["summary"]["active_entries"] == 1
    assert snapshot["summary"]["experiments"] == 1
    assert snapshot["summary"]["mature_negative_regimes"] == 1
    assert snapshot["summary"]["mature_positive_regimes"] == 1
    assert any("negative regime" in item["title"].lower() for item in snapshot["derived_lessons"])


def test_brain_snapshot_queries_are_select_only():
    queries = []

    def capture(query: str, params=()):
        queries.append(query.strip().lower())
        return []

    oracle_brain.build_oracle_brain_snapshot(capture)
    assert queries
    assert all(query.startswith("select") for query in queries)
    assert all("insert " not in query and "update " not in query and "delete " not in query for query in queries)


def test_runtime_boundary_detects_live_state(monkeypatch):
    monkeypatch.setenv("EXECUTION_MODE", "live")
    monkeypatch.setenv("ENABLE_BROKER_SUBMISSION", "true")
    monkeypatch.setenv("LIVE_TRADING_ARMED", "true")
    state = oracle_brain.runtime_safety_state()
    assert state["safe_research_boundary"] is False
    assert state["research_execution_authority"] == "NONE"


def test_doctrine_contains_core_safety_invariants():
    titles = " ".join(item["title"].lower() for item in oracle_brain.CORE_DOCTRINE)
    assert "evidence" in titles
    assert "provenance" in titles
    assert "hybrid" in titles
    assert "promotion" in titles
    assert "live" in titles


def test_brain_page_neural_field_is_evidence_driven_and_read_only():
    page = Path("pages/3_Oracle_Brain.py").read_text(encoding="utf-8")
    component = Path("oracle_brain_component.py").read_text(encoding="utf-8")
    assert "render_oracle_brain_component" in page
    assert 'snapshot["learning_activity"]' in page
    assert "Oracle Brain · evidence monitor" in component
    assert "retained evidence units" in component
    assert "execution authority: NONE" in page
    assert "submit_order(" not in page + component
    assert "ENABLE_BROKER_SUBMISSION=true" not in page + component
    assert "LIVE_TRADING_ARMED=true" not in page + component


def test_brain_attributes_thesis_separately_from_realized_outcome(monkeypatch):
    monkeypatch.setenv("EXECUTION_MODE", "paper")
    monkeypatch.setenv("ENABLE_BROKER_SUBMISSION", "false")
    monkeypatch.setenv("LIVE_TRADING_ARMED", "false")

    def fetch(query: str, params=()):
        if "FROM oracle_brain_episodes" in query:
            return [{
                "episode_key": "trade:42",
                "trade_id": "42",
                "market": "crypto",
                "symbol": "BTC",
                "strategy": "aeve",
                "regime": "trend",
                "entry_time": "2026-09-20T00:00:00+00:00",
                "exit_time": "2026-09-20T01:00:00+00:00",
                "net_pnl": 5.0,
                "fees": 0.2,
                "return_pct": 0.4,
                "mfe_pct": 0.7,
                "mae_pct": -0.2,
                "provenance_status": "exact",
                "source_quality": 1.0,
                "freshness_score": 1.0,
                "confidence": 0.9,
                "feature_snapshot": {
                    "expected_edge_pct": -0.10,
                    "probability_of_profit": 55,
                    "estimated_cost_pct": 0.08,
                },
                "outcome_snapshot": {"entry_signal_id": "sig-42"},
                "tags": [],
            }]
        return []

    snapshot = oracle_brain.build_oracle_brain_snapshot(fetch)
    row = snapshot["outcome_attribution"][0]
    assert row["probability_of_profit"] == 0.55
    assert row["attribution_state"] == "positive_outcome_without_positive_thesis"
    assert snapshot["attribution_counts"]["positive_outcome_without_positive_thesis"] == 1
    assert snapshot["execution_authority"] == "NONE"


def test_brain_page_uses_supported_streamlit_width_and_bounded_nodes():
    page = Path("pages/3_Oracle_Brain.py").read_text(encoding="utf-8")
    component = Path("oracle_brain_component.py").read_text(encoding="utf-8")
    assert "components.html" not in page
    assert "use_container_width=True" not in page
    assert 'width="stretch"' in page
    assert "brainMask" in component
    assert "pointFor" in component
    assert "raw.slice(0,210)" in component


def test_brain_retention_health_is_read_only(monkeypatch):
    monkeypatch.setenv("EXECUTION_MODE", "paper")
    monkeypatch.setenv("ENABLE_BROKER_SUBMISSION", "false")
    monkeypatch.setenv("LIVE_TRADING_ARMED", "false")

    def fetch(query: str, params=()):
        if "COUNT(*)::int AS total, MIN(created_at)" in query:
            return [{"total": 10, "oldest": "2026-09-01", "newest": "2026-09-21"}]
        if "COUNT(*)::int AS total, MIN(observed_at)" in query:
            return [{"total": 20, "oldest": "2026-09-02", "newest": "2026-09-21"}]
        if "COUNT(*)::int AS total, MIN(exit_time)" in query:
            return [{"total": 30, "oldest": "2026-09-03", "newest": "2026-09-21"}]
        if "FROM oracle_brain_learning_state" in query:
            return [{"pipeline_key": "paper", "market": "crypto", "last_source_id": 20,
                     "last_episode_exit_at": "2026-09-21", "last_sync_at": "2026-09-21",
                     "last_result": {}}]
        return []

    snapshot = oracle_brain.build_oracle_brain_snapshot(fetch)
    health = snapshot["retention_health"]
    assert health["persistent_store"] == "PostgreSQL"
    assert health["entries"]["count"] == 10
    assert health["sources"]["count"] == 20
    assert health["episodes"]["count"] == 30
    assert health["learning_pipelines"] == 1
    assert health["read_only"] is True
    assert health["execution_authority"] == "NONE"
    assert snapshot["safety"]["safe_research_boundary"] is True


def test_brain_growth_is_derived_from_persisted_evidence(monkeypatch):
    monkeypatch.setenv("EXECUTION_MODE", "paper")
    monkeypatch.setenv("ENABLE_BROKER_SUBMISSION", "false")
    monkeypatch.setenv("LIVE_TRADING_ARMED", "false")

    def fetch(query: str, params=()):
        if "COUNT(*)::int AS total, MIN(created_at)" in query:
            return [{"total": 10, "oldest": "2026-09-01", "newest": "2026-09-21"}]
        if "COUNT(*)::int AS total, MIN(observed_at)" in query:
            return [{"total": 20, "oldest": "2026-09-02", "newest": "2026-09-21"}]
        if "COUNT(*)::int AS total, MIN(exit_time)" in query:
            return [{"total": 30, "oldest": "2026-09-03", "newest": "2026-09-21"}]
        if "FROM oracle_brain_links" in query:
            return [{"source_key":"a","target_key":"b","relation":"supports","weight":1.0,
                     "evidence_count":3,"confidence":0.8,"last_observed_at":"2026-09-21","metadata":{}}]
        return []

    growth = oracle_brain.build_oracle_brain_snapshot(fetch)["growth"]
    assert growth["knowledge_units"] == 61
    assert growth["durable_lessons"] == 10
    assert growth["observations"] == 20
    assert growth["exact_outcomes"] == 30
    assert growth["relationships"] == 1
    assert growth["execution_authority"] == "NONE"


def test_brain_visual_density_tracks_evidence_without_fake_neurons():
    component = Path("oracle_brain_component.py").read_text(encoding="utf-8")
    page = Path("pages/3_Oracle_Brain.py").read_text(encoding="utf-8")
    assert "brainPath" in component
    assert "brainMask" in component
    assert "D.entries||[]" in component
    assert "D.sources||[]" in component
    assert "D.episodes||[]" in component
    assert "D.regimes||[]" in component
    assert "Math.max(18" not in component
    assert "awaiting evidence" not in component
    assert "Brain growth:" in page


def test_brain_learning_activity_reports_new_and_revised_evidence(monkeypatch):
    monkeypatch.setenv("EXECUTION_MODE", "paper")
    monkeypatch.setenv("ENABLE_BROKER_SUBMISSION", "false")
    monkeypatch.setenv("LIVE_TRADING_ARMED", "false")

    from datetime import datetime, timezone
    now = datetime.now(timezone.utc).isoformat()

    def fetch(query: str, params=()):
        if "FROM oracle_brain_learning_state" in query:
            return [
                {
                    "pipeline_key": "intelligence",
                    "market": "global",
                    "last_sync_at": now,
                    "last_result": {"new": 3, "updated": 2},
                },
                {
                    "pipeline_key": "episodes",
                    "market": "crypto",
                    "last_sync_at": now,
                    "last_result": {"new_exact_episodes": 1, "skipped_missing_exact_provenance": 4},
                },
                {
                    "pipeline_key": "brain_v2",
                    "market": "crypto",
                    "last_sync_at": now,
                    "last_result": {"lessons_updated": 1},
                },
            ]
        return []

    activity = oracle_brain.build_oracle_brain_snapshot(fetch)["learning_activity"]
    assert activity["status"] == "LEARNING"
    assert activity["new_sources"] == 3
    assert activity["revised_sources"] == 2
    assert activity["new_exact_episodes"] == 1
    assert activity["lessons_updated"] == 1
    assert activity["learned_this_cycle"] == 7
    assert activity["skipped_missing_exact_provenance"] == 4
    assert activity["execution_authority"] == "NONE"


def test_brain_component_has_anatomical_hemispheres_and_truthful_states():
    source = Path("oracle_brain_component.py").read_text(encoding="utf-8")
    assert 'brainPath("left")' not in source  # loop supplies left/right dynamically
    assert 'for(const side of ["left","right"])' in source
    assert "central" not in source.lower() or "ctx.bezierCurveTo" in source
    assert "SYNCED — NO NEW EVIDENCE" in source
    assert "STALE" in source
    assert 'String(A.status||"")==="LEARNING"' in source
    assert "No completed Brain learning sync is recorded yet." in source


def test_oracle_brain_component_serializes_database_datetime_values():
    rendered = render_oracle_brain_component({
        "generated_at": datetime(2026, 9, 22, 4, 30, tzinfo=timezone.utc),
        "growth": {
            "knowledge_units": 12,
            "relationships": 3,
            "exact_outcomes": 4,
            "last_learning_sync": datetime(2026, 9, 22, 4, 29, tzinfo=timezone.utc),
        },
        "learning_activity": {
            "status": "LEARNING",
            "last_sync_at": datetime(2026, 9, 22, 4, 29, tzinfo=timezone.utc),
            "learned_this_cycle": 2,
        },
        "entries": [{
            "brain_key": "lesson:test",
            "category": "test",
            "title": "Datetime regression",
            "confidence": Decimal("0.75"),
        }],
        "sources": [],
        "episodes": [],
        "regime_economics": [],
        "concept_links": [],
    })
    assert "2026-09-22T04:30:00+00:00" in rendered
    assert "2026-09-22T04:29:00+00:00" in rendered
    assert '"confidence":0.75' in rendered
    assert "datetime is not JSON serializable" not in rendered
