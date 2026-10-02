from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import database
from paper_shadow_exit_challenger import (
    BrainCohortAnalyzerV4,
    CounterfactualSettlement,
    ShadowExitConstraints,
    _episode_id,
    _latest_signal,
    evaluate_forward_evidence_layer,
)


def test_forward_evidence_requires_distinct_market_observations():
    state = {"evidence_persistence_counter": 0}
    constraints = ShadowExitConstraints(min_exit_ev_advantage_r=0.05, min_confirmations=3)

    def telemetry(observation_id: str, hold: float = 0.0, exit_: float = 0.10):
        return {
            "observation_id": observation_id,
            "hold_ev_r": hold,
            "exit_ev_r": exit_,
            "thesis_decay_confirmed": True,
        }

    triggered, advantage = evaluate_forward_evidence_layer(state, telemetry("bar-1"), constraints)
    assert triggered is False
    assert advantage == 0.10
    assert state["evidence_persistence_counter"] == 1

    triggered, _ = evaluate_forward_evidence_layer(state, telemetry("bar-1"), constraints)
    assert triggered is False
    assert state["evidence_persistence_counter"] == 1

    triggered, _ = evaluate_forward_evidence_layer(state, telemetry("bar-2"), constraints)
    assert triggered is False
    assert state["evidence_persistence_counter"] == 2

    triggered, _ = evaluate_forward_evidence_layer(state, telemetry("bar-3"), constraints)
    assert triggered is True
    assert state["evidence_persistence_counter"] == 3

    triggered, _ = evaluate_forward_evidence_layer(
        state,
        {
            "observation_id": "bar-4",
            "hold_ev_r": 0.20,
            "exit_ev_r": 0.10,
            "thesis_decay_confirmed": True,
        },
        constraints,
    )
    assert triggered is False
    assert state["evidence_persistence_counter"] == 0


def test_counterfactual_settlement_is_adverse_and_uses_explicit_risk():
    challenger_r, fill = CounterfactualSettlement.calculate_immutable_challenger_r(
        {"entry_price": 100.0, "quantity": 2.0},
        market="crypto",
        trigger_price=95.0,
        quote={},
        initial_risk_usd=12.0,
    )

    raw_mark_r = ((95.0 - 100.0) * 2.0) / 12.0
    assert fill["side"] == "SELL"
    assert fill["fill_price"] < 95.0
    assert challenger_r < raw_mark_r


def test_episode_id_clusters_correlated_windows():
    base = datetime(2026, 10, 2, 0, 15, tzinfo=timezone.utc)
    same_window = base + timedelta(hours=2)
    later_window = base + timedelta(hours=5)

    assert _episode_id("crypto", base) == _episode_id("crypto", same_window)
    assert _episode_id("crypto", base) != _episode_id("crypto", later_window)


def _positive_experiments():
    records = []
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    for episode in range(30):
        episode_id = f"episode-{episode:02d}"
        exit_time = start + timedelta(days=episode)
        for leg, (actual, challenger) in enumerate(((-0.20, -0.05), (0.10, 0.20))):
            records.append(
                {
                    "episode_id": episode_id,
                    "exit_time": exit_time + timedelta(minutes=leg),
                    "entry_pattern": "dip_rebound",
                    "regime": "range__high_vol",
                    "actual_realized_r_net": actual,
                    "challenger_counterfactual_r_net": challenger,
                }
            )
    return records


def test_paired_episode_gate_can_recognize_reliably_positive_challenger():
    report = BrainCohortAnalyzerV4(_positive_experiments()).evaluate_portfolio_safety_gate()

    assert report["trade_count"] == 60
    assert report["episode_count"] == 30
    assert report["bootstrap_delta_ci"][0] > 0
    assert report["challenger_profit_factor"] > report["actual_profit_factor"]
    assert report["challenger_max_drawdown_r"] <= report["actual_max_drawdown_r"]
    assert report["conditions"]["no_mature_cohort_materially_harmed"] is True
    assert report["promotion_evidence_ready"] is True
    assert report["promotion_action"] == "NONE"
    assert report["execution_impact"] == "NONE"


def test_mature_harmed_cohort_vetoes_promotion_evidence():
    records = _positive_experiments()
    start = datetime(2026, 6, 1, tzinfo=timezone.utc)
    for index in range(30):
        records.append(
            {
                "episode_id": f"harm-{index:02d}",
                "exit_time": start + timedelta(days=index),
                "entry_pattern": "momentum_breakout",
                "regime": "trend_up__high_vol",
                "actual_realized_r_net": 0.20,
                "challenger_counterfactual_r_net": 0.10,
            }
        )

    report = BrainCohortAnalyzerV4(records).evaluate_portfolio_safety_gate()

    assert report["conditions"]["no_mature_cohort_materially_harmed"] is False
    assert any(item["entry_pattern"] == "momentum_breakout" for item in report["harmed_cohorts"])
    assert report["promotion_evidence_ready"] is False


def test_shadow_tables_are_protected_from_generic_retention_cleanup():
    assert "garibaldi_shadow_exit_epochs" in database.CANONICAL_PROTECTED_TABLES
    assert "garibaldi_shadow_experiments" in database.CANONICAL_PROTECTED_TABLES
    assert "garibaldi_shadow_experiments" not in database.DATABASE_RETENTION_POLICIES


def test_shadow_migration_is_compact_and_privilege_safe():
    sql = Path("migrations/20261002_shadow_exit_experiments.sql").read_text(encoding="utf-8")

    assert "experiment_id BIGSERIAL PRIMARY KEY" in sql
    assert "CREATE EXTENSION" not in sql
    assert "trigger_snapshot JSONB" in sql
    assert "USING gin" not in sql
    assert "UNIQUE (trade_id, generation, model_version)" in sql


def test_shadow_module_has_no_execution_authority():
    source = Path("paper_shadow_exit_challenger.py").read_text(encoding="utf-8")

    assert "_close_position(" not in source
    assert "process_signals(" not in source
    assert "risk_exits(" not in source
    assert 'promotion_action": "NONE"' in source


def test_latest_signal_reads_canonical_details_json_not_nonexistent_payload_column():
    calls = []

    class Result:
        def fetchone(self):
            return {
                "id": 101,
                "market": "crypto",
                "symbol": "BTC-USD",
                "price": 100.0,
                "score": 80.0,
                "action": "HOLD",
                "confidence": 0.7,
                "details": '{"expected_edge_pct": -0.25, "edge_provenance": "test"}',
                "created_at": datetime(2026, 10, 2, tzinfo=timezone.utc),
            }

    class Conn:
        def execute(self, sql, params=()):
            calls.append(sql)
            return Result()

    signal = _latest_signal(Conn(), "crypto", "BTC-USD")

    assert signal is not None
    assert signal["expected_edge_pct"] == -0.25
    assert signal["payload"]["edge_provenance"] == "test"
    assert any("confidence,details" in " ".join(sql.split()) for sql in calls)
    assert all("confidence,payload" not in " ".join(sql.split()) for sql in calls)
