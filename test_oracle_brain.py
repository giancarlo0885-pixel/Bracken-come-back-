from __future__ import annotations

from pathlib import Path

import oracle_brain


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
    source = Path("pages/3_Oracle_Brain.py").read_text(encoding="utf-8")
    assert "ORACLE NEURAL FIELD" in source
    assert 'snapshot["entries"]' in source
    assert 'snapshot["regime_economics"]' in source
    assert "execution authority: NONE" in source
    assert "submit_order(" not in source
    assert "ENABLE_BROKER_SUBMISSION=true" not in source
    assert "LIVE_TRADING_ARMED=true" not in source


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
    source = Path("pages/3_Oracle_Brain.py").read_text(encoding="utf-8")
    assert "components.html" not in source
    assert "use_container_width=True" not in source
    assert 'width="stretch"' in source
    assert "Math.max(14,Math.min(W-14,n.x))" in source
    assert "Math.max(14,Math.min(H-14,n.y))" in source
