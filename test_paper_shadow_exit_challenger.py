from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import database
from paper_shadow_exit_challenger import (
    BrainCohortAnalyzerV4,
    COST_MODEL_VERSION,
    CounterfactualSettlement,
    ShadowExitConstraints,
    _episode_id,
    _counterfactual_net_r,
    _latest_signal,
    _lot_risk_context,
    _open_position_risk_context,
    evaluate_forward_evidence_layer,
)


def test_counterfactual_net_r_attributes_partial_entry_fee_once():
    # A partial lot close: $0.40 entry fee plus $0.20 actual exit fee.
    item = {"entry_price": 100, "quantity": 2,
            "round_trip_fees": 0.60, "actual_exit_fees": 0.20}
    # Simulated execution price excludes its separately persisted exit fee.
    result = _counterfactual_net_r(item, {"fill_price": 101,
        "fee_contract": COST_MODEL_VERSION, "fee_pct": 0.20 / 202}, 10)
    assert abs(result - 0.14) < 1e-12


def test_counterfactual_missing_or_inconsistent_costs_remain_unqualified():
    item = {"entry_price": 100, "quantity": 2,
            "round_trip_fees": 0.60, "actual_exit_fees": 0.20}
    for invalid in (None, float("nan"), -1, 0.1):
        assert _counterfactual_net_r(
            {**item, "round_trip_fees": invalid}, {"fill_price": 101}, 10
        ) is None
    assert _counterfactual_net_r(item, {}, 10) is None
    assert _counterfactual_net_r(item, {"fill_price": 101}, 0) is None


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



def test_lot_risk_context_prefers_immutable_entry_stop_snapshot():
    risk_usd, risk_pct, source = _lot_risk_context(
        {
            "entry_price": 100.0,
            "quantity": 2.0,
            "risk_snapshot": {
                "quantity": 4.0,
                "initial_risk_usd": 24.0,
                "risk_basis_source": "entry_stop_price",
            },
        }
    )

    assert risk_usd == 12.0
    assert risk_pct == 6.0
    assert source == "entry_stop_price"


def test_open_position_risk_aggregates_exact_and_legacy_lots():
    class Result:
        def fetchall(self):
            return [
                {
                    "quantity_opened": 2.0,
                    "quantity_remaining": 2.0,
                    "entry_price": 100.0,
                    "risk_snapshot": {
                        "quantity": 2.0,
                        "initial_risk_usd": 8.0,
                        "risk_basis_source": "entry_stop_price",
                    },
                },
                {
                    "quantity_opened": 1.0,
                    "quantity_remaining": 1.0,
                    "entry_price": 50.0,
                    "risk_snapshot": {},
                },
            ]

    class Conn:
        def execute(self, sql, params=()):
            assert "position_lots" in sql
            return Result()

    risk_usd, source = _open_position_risk_context(
        Conn(),
        "crypto",
        "BTC-USD",
        {"entry_price": 100.0, "quantity": 3.0},
    )

    assert risk_usd == 11.0  # $8 exact + $3 legacy 6% fallback
    assert source == "mixed_entry_stop_and_configured_fallback"


def test_risk_provenance_migration_is_restart_safe_without_high_churn_indexing():
    sql = Path("migrations/20261002_shadow_exit_risk_provenance.sql").read_text(encoding="utf-8")

    assert "garibaldi_shadow_exit_triggers" in sql
    assert "PRIMARY KEY(position_key, model_version)" in sql
    assert "initial_risk_usd" in sql
    assert "risk_basis_source" in sql
    assert "USING gin" not in sql


def test_entry_path_persists_exact_initial_risk_without_changing_execution_rules():
    source = Path("oracle_bot.py").read_text(encoding="utf-8")

    assert '"initial_risk_usd": stop_distance * quantity' in source
    assert '"risk_basis_source": (' in source
    assert "risk_snapshot=entry_risk_snapshot" in source


def test_shadow_trigger_state_is_durable_but_deleted_after_final_settlement():
    source = Path("paper_shadow_exit_challenger.py").read_text(encoding="utf-8")

    assert "INSERT INTO garibaldi_shadow_exit_triggers" in source
    assert "ON CONFLICT(position_key,model_version) DO NOTHING" in source
    assert "DELETE FROM garibaldi_shadow_exit_triggers" in source
    assert '"_pending_position_key": key' in source


def test_fallback_rows_do_not_inflate_challenger_promotion_evidence():
    records = _positive_experiments()
    for record in records:
        record["challenger_exit_type"] = "ACTUAL_EXIT_FALLBACK"
        record["challenger_counterfactual_r_net"] = record["actual_realized_r_net"]

    report = BrainCohortAnalyzerV4(records).evaluate_portfolio_safety_gate()

    assert report["observed_trade_count"] == 60
    assert report["trade_count"] == 0
    assert report["episode_count"] == 0
    assert report["promotion_evidence_ready"] is False


def test_triggered_rows_remain_eligible_for_promotion_evidence():
    records = _positive_experiments()
    for record in records:
        record["challenger_exit_type"] = "FORWARD_EVIDENCE_EXIT"

    report = BrainCohortAnalyzerV4(records).evaluate_portfolio_safety_gate()

    assert report["observed_trade_count"] == 60
    assert report["trade_count"] == 60
    assert report["episode_count"] == 30
    assert report["promotion_evidence_ready"] is True


def test_future_quote_is_not_forward_evidence():
    from paper_shadow_exit_challenger import _trigger_telemetry
    future = datetime.now(timezone.utc) + timedelta(days=1)
    signal = dict(price=101, expected_edge_pct=0.5, created_at=future,
                  payload={"quote_verified": True, "quote_timestamp": future.isoformat(), "price": 101})
    assert _trigger_telemetry("crypto", {"entry_price": 100, "quantity": 1}, signal,
                              initial_risk_usd=6, risk_basis_source="test") is None


def test_replayed_observation_cannot_complete_three_confirmations():
    state = {"evidence_persistence_counter": 0}
    constraints = ShadowExitConstraints(min_confirmations=3)
    for observation in ["a", "b", "a"]:
        triggered, _ = evaluate_forward_evidence_layer(state, dict(observation_id=observation,
            hold_ev_r=0, exit_ev_r=0.2, thesis_decay_confirmed=True), constraints)
        assert triggered is False
    assert state["evidence_persistence_counter"] == 2


def test_research_fill_contract_is_independent_of_runtime_monkeypatch(monkeypatch):
    import paper_execution_reality
    def unexpected(**kwargs):
        raise AssertionError("research must use its frozen explicit-fee contract")
    monkeypatch.setattr(paper_execution_reality, "simulate_fill", unexpected)
    result, fill = CounterfactualSettlement.calculate_immutable_challenger_r(
        {"entry_price": 100, "quantity": 2}, market="crypto",
        trigger_price=101, quote={"paper_fee_pct": 0.001}, initial_risk_usd=10,
    )
    assert fill["fee_contract"] == COST_MODEL_VERSION
    assert fill["exit_fee_usd"] == fill["fill_price"] * 2 * fill["fee_pct"]
    assert abs(result - ((fill["fill_price"] - 100) * 2 - fill["exit_fee_usd"]) / 10) < 1e-12
    item = {"entry_price": 100, "quantity": 2,
            "round_trip_fees": 0.60, "actual_exit_fees": 0.20}
    assert abs(_counterfactual_net_r(item, fill, 10) - (result - 0.04)) < 1e-12
    assert _counterfactual_net_r(item, {"fill_price": 101}, 10) is None


def test_simulated_exit_fee_is_attributed_to_partial_quantity():
    item = {"entry_price": 100, "quantity": 1,
            "round_trip_fees": 0.30, "actual_exit_fees": 0.10}
    fill = {"fill_price": 101, "fee_pct": 0.001,
            "fee_contract": COST_MODEL_VERSION, "exit_fee_usd": 0.202}
    assert abs(_counterfactual_net_r(item, fill, 10) - 0.0699) < 1e-12
