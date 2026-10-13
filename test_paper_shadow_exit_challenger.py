from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
import math

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
                    "trade_id": f"trade-{episode:02d}-{leg}",
                    "episode_id": episode_id,
                    "exit_time": exit_time + timedelta(minutes=leg),
                    "entry_pattern": "dip_rebound",
                    "regime": "range__high_vol",
                    "actual_realized_r_net": actual,
                    "challenger_counterfactual_r_net": challenger,
                    "challenger_exit_type": "FORWARD_EVIDENCE_EXIT",
                    "risk_basis_source": "entry_stop_price",
                    "initial_risk_usd": 10.0,
                    "trigger_snapshot": {"risk_basis_source": "exact_entry_stop_lots"},
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
                "trade_id": f"harm-trade-{index:02d}",
                "episode_id": f"harm-{index:02d}",
                "exit_time": start + timedelta(days=index),
                "entry_pattern": "momentum_breakout",
                "regime": "trend_up__high_vol",
                "actual_realized_r_net": 0.20,
                "challenger_counterfactual_r_net": 0.10,
                "challenger_exit_type": "FORWARD_EVIDENCE_EXIT",
                "risk_basis_source": "entry_stop_price",
                "initial_risk_usd": 10.0,
                "trigger_snapshot": {"risk_basis_source": "exact_entry_stop_lots"},
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
    assert any("ORDER BY NULLIF(created_at," in " ".join(sql.split()) and "DESC, id DESC" in " ".join(sql.split()) for sql in calls)



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


def test_latest_signal_forecast_cannot_use_post_signal_information():
    decision_time = datetime(2026, 10, 2, 12, 0, tzinfo=timezone.utc)
    queries = []

    class Result:
        def __init__(self, row):
            self.row = row

        def fetchone(self):
            return self.row

    class Conn:
        def execute(self, sql, params=()):
            queries.append((sql, params))
            if "FROM signals" in sql:
                return Result({
                    "id": 101, "market": "crypto", "symbol": "BTC-USD",
                    "price": 100.0, "score": 80.0, "action": "HOLD",
                    "confidence": 0.7, "details": "{}",
                    "created_at": decision_time,
                })
            assert "FROM forecasts" in sql
            assert params[-1] == decision_time
            assert "ORDER BY NULLIF(created_at,'')::timestamptz DESC" in sql
            # No qualifying forecast existed as of the signal decision.
            return Result(None)

    signal = _latest_signal(Conn(), "crypto", "BTC-USD")
    assert signal is not None
    assert signal["expected_edge_pct"] is None
    assert len(queries) == 2


def test_latest_signal_uses_only_preexisting_linked_forecast():
    decision_time = datetime(2026, 10, 2, 12, 0, tzinfo=timezone.utc)

    class Result:
        def __init__(self, row):
            self.row = row

        def fetchone(self):
            return self.row

    class Conn:
        def execute(self, sql, params=()):
            if "FROM signals" in sql:
                return Result({
                    "id": 101, "market": "crypto", "symbol": "BTC-USD",
                    "price": 100.0, "score": 80.0, "action": "HOLD",
                    "confidence": 0.7, "details": "{}",
                    "created_at": decision_time,
                })
            assert params[-1] == decision_time
            return Result({"expected_move_pct": -0.3, "created_at": decision_time - timedelta(seconds=1)})

    signal = _latest_signal(Conn(), "crypto", "BTC-USD")
    assert signal["expected_edge_pct"] == -0.3
    assert signal["payload"]["edge_provenance"] == "exact_signal_forecast_expected_move_pct"


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


def test_postgres_forecast_cutoff_and_timestamp_order():
    import os
    import pytest
    import psycopg
    from psycopg.rows import dict_row
    url = os.getenv("DATABASE_URL", "")
    if not url:
        pytest.skip("PostgreSQL integration only")
    decision = datetime(2026, 10, 2, 12, 0, tzinfo=timezone.utc)
    with psycopg.connect(url, row_factory=dict_row) as conn:
        conn.execute("CREATE TEMP TABLE signals(id bigint,market text,symbol text,price float8,score float8,action text,confidence float8,details text,created_at text)")
        conn.execute("CREATE TEMP TABLE forecasts(id bigint,market text,symbol text,signal_id bigint,expected_move_pct float8,created_at text)")
        conn.execute("INSERT INTO signals VALUES (101,'crypto','BTC-USD',100,80,'HOLD',0.7,'{}',%s)", (decision.isoformat(),))
        conn.execute("INSERT INTO forecasts VALUES (999,'crypto','BTC-USD',101,9,%s)", ((decision + timedelta(seconds=1)).isoformat(),))
        assert _latest_signal(conn, "crypto", "BTC-USD")["expected_edge_pct"] is None
        conn.execute("INSERT INTO forecasts VALUES (50,'crypto','BTC-USD',101,-0.3,%s),(100,'crypto','BTC-USD',101,5,%s),(1000,'crypto','OTHER-USD',101,8,%s),(1001,'crypto','BTC-USD',102,7,%s)",
                     ((decision - timedelta(seconds=1)).isoformat(), (decision - timedelta(seconds=2)).isoformat(), decision.isoformat(), decision.isoformat()))
        assert _latest_signal(conn, "crypto", "BTC-USD")["expected_edge_pct"] == -0.3


def test_nonfinite_and_invalid_time_rows_cannot_inflate_paired_evidence():
    records = _positive_experiments()
    bad = [
        {**records[0], "trade_id": "bad-inf", "actual_realized_r_net": float("inf")},
        {**records[0], "trade_id": "bad-nan", "challenger_counterfactual_r_net": float("nan")},
        {**records[0], "trade_id": "bad-time", "exit_time": "invalid"},
        {**records[0], "trade_id": "bad-future", "exit_time": datetime.now(timezone.utc) + timedelta(days=1)},
        {**records[0], "trade_id": "", "episode_id": ""},
    ]
    report = BrainCohortAnalyzerV4(records + bad).evaluate_portfolio_safety_gate()
    assert report["observed_trade_count"] == 65
    assert report["trade_count"] == 60
    assert report["episode_count"] == 30
    assert report["exclusions"]["invalid_identity_time_or_return"] == 5
    assert math.isfinite(report["paired_mean_delta_r"])


def test_canonical_trade_replays_count_once_and_conflicts_are_excluded():
    records = _positive_experiments()
    conflicting = {**records[0], "challenger_counterfactual_r_net": 100.0}
    analyzer = BrainCohortAnalyzerV4(records + [records[1], conflicting])
    report = analyzer.evaluate_portfolio_safety_gate()
    assert report["observed_trade_count"] == 62
    assert report["trade_count"] == 59
    assert report["exclusions"]["duplicate_trade_identity"] == 1
    assert report["exclusions"]["conflicting_trade_identity"] == 2
    # Evaluation must not replace the original audit population with its filter.
    assert analyzer.evaluate_portfolio_safety_gate() == report


def test_missing_identity_or_treatment_is_not_implicit_paired_evidence():
    for field in ("trade_id", "challenger_exit_type", "risk_basis_source", "trigger_snapshot", "initial_risk_usd"):
        records = _positive_experiments()
        for row in records:
            row.pop(field)
        report = BrainCohortAnalyzerV4(records).evaluate_portfolio_safety_gate()
        assert report["promotion_evidence_ready"] is False
        assert report["trade_count"] == 0
        assert report["paired_mean_delta_r"] is None


def test_empty_treatment_metrics_are_unavailable_not_zero_or_break_even():
    records = _positive_experiments()
    for row in records:
        row["challenger_exit_type"] = "ACTUAL_EXIT_FALLBACK"
    for sample in ([], records):
        report = BrainCohortAnalyzerV4(sample).evaluate_portfolio_safety_gate()
        assert report["trade_count"] == 0
        assert report["promotion_evidence_ready"] is False
        assert report["bootstrap_delta_ci"] == (None, None)
        for metric in ("paired_mean_delta_r", "actual_profit_factor", "challenger_profit_factor",
                       "actual_max_drawdown_r", "challenger_max_drawdown_r",
                       "challenger_episode_var_05_r", "challenger_episode_cvar_05_r",
                       "actual_post_cost_net_pnl_usd", "challenger_post_cost_net_pnl_usd",
                       "actual_post_cost_expectancy_usd", "challenger_post_cost_expectancy_usd"):
            assert report[metric] is None


def test_drawdown_is_chronological_and_simultaneous_closes_are_order_independent():
    base = _positive_experiments()[0]
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    rows = [
        {**base, "trade_id": "loss-first", "exit_time": start, "actual_realized_r_net": -2},
        {**base, "trade_id": "recover", "exit_time": start + timedelta(days=1), "actual_realized_r_net": 3},
        {**base, "trade_id": "same-time-loss", "exit_time": start + timedelta(days=1), "actual_realized_r_net": -2},
        {**base, "trade_id": "late-loss", "exit_time": start + timedelta(days=2), "actual_realized_r_net": -3},
    ]
    ordered = BrainCohortAnalyzerV4(rows).evaluate_portfolio_safety_gate()
    reversed_ = BrainCohortAnalyzerV4(list(reversed(rows))).evaluate_portfolio_safety_gate()
    assert ordered["actual_max_drawdown_r"] == 4
    assert reversed_["actual_max_drawdown_r"] == 4


def test_entry_after_exit_and_overflowing_delta_are_excluded():
    base = _positive_experiments()[0]
    invalid = {**base, "entry_time": base["exit_time"] + timedelta(seconds=1)}
    report = BrainCohortAnalyzerV4([invalid]).evaluate_portfolio_safety_gate()
    assert report["trade_count"] == 0
    overflow = {**base, "actual_realized_r_net": -1e308, "challenger_counterfactual_r_net": 1e308}
    report = BrainCohortAnalyzerV4([overflow]).evaluate_portfolio_safety_gate()
    assert report["trade_count"] == 0
    assert report["exclusions"]["nonfinite_paired_delta"] == 1


def test_finalizer_never_turns_missing_outcomes_or_excursions_into_zero(monkeypatch):
    from contextlib import contextmanager
    import paper_shadow_exit_challenger as shadow
    writes = []
    started = datetime(2026, 1, 1, tzinfo=timezone.utc)
    base = dict(trade_id="closed-1", symbol="BTC-USD", entry_price=100, quantity=2,
                entry_time=started, exit_time=started + timedelta(days=1),
                round_trip_net_pnl=1, round_trip_fees=0.6, actual_exit_fees=0.2,
                risk_snapshot={"quantity": 2, "initial_risk_usd": 10, "risk_basis_source": "entry_stop_price"},
                mfe_pct=0, mae_pct=float("nan"), excursion_sample_count=0)

    class Result:
        def __init__(self, rows):
            self.rows = rows
        def fetchall(self):
            return self.rows
        def fetchone(self):
            return self.rows[0] if self.rows else None

    class Conn:
        def execute(self, sql, params=()):
            if "SELECT started_at" in sql:
                return Result([{"started_at": started}])
            if "FROM paper_regime_trade_metrics" in sql:
                return Result([base, {**base, "trade_id": "nan-close", "round_trip_net_pnl": float("nan")}])
            if "INSERT INTO garibaldi_shadow_experiments" in sql:
                writes.append(params)
            return Result([])

    @contextmanager
    def connect():
        yield Conn()

    monkeypatch.setattr(database, "connect", connect)
    monkeypatch.setattr(shadow, "active", lambda: True)
    monkeypatch.setattr(shadow, "_matching_trigger", lambda *args: None)
    assert shadow.finalize_closed_trades("crypto") == 1
    assert len(writes) == 1
    assert writes[0][10] == 0.1
    assert writes[0][12:14] == (None, None)


def test_partial_close_retains_in_memory_trigger_until_durable_cleanup(monkeypatch):
    import paper_shadow_exit_challenger as shadow
    pending_key = "crypto:BTC-USD:original-position"
    state = {pending_key: {"trigger_snapshot": {"counterfactual_fill": {"fill_price": 101}}}}
    monkeypatch.setattr(shadow, "_POSITION_STATE", state)

    class Result:
        def __init__(self, row):
            self.row = row
        def fetchone(self):
            return self.row

    class Conn:
        removed = None
        def execute(self, sql, params):
            assert "COALESCE(p.quantity,0)>0" in sql
            assert "IS NOT DISTINCT FROM t.opened_at" in sql
            assert "e.trade_id=l.trade_id AND e.generation=%s" in sql
            assert "FROM trade_ledger l" in sql
            assert "paper_regime_trade_metrics" not in sql
            assert "RETURNING t.position_key" in sql
            assert params == (pending_key, shadow.MODEL_VERSION, shadow.GENERATION)
            return Result(self.removed)

    conn = Conn()
    shadow._cleanup_consumed_triggers(conn, {pending_key})
    assert pending_key in state
    conn.removed = {"position_key": pending_key}
    shadow._cleanup_consumed_triggers(conn, {pending_key})
    assert pending_key not in state


def test_postgres_partial_close_keeps_durable_trigger_until_original_inventory_closes(monkeypatch):
    import os
    import pytest
    import psycopg
    from psycopg.rows import dict_row
    import paper_shadow_exit_challenger as shadow
    url = os.getenv("DATABASE_URL", "")
    if not url:
        pytest.skip("PostgreSQL integration only")
    opened = datetime(2026, 1, 1, tzinfo=timezone.utc)
    key = "crypto:BTC-USD:original-position"
    monkeypatch.setattr(shadow, "_POSITION_STATE", {key: {"trigger_snapshot": {"fill_price": 101}}})
    with psycopg.connect(url, row_factory=dict_row) as conn:
        conn.execute("CREATE TEMP TABLE positions(market text,symbol text,quantity numeric,opened_at text)")
        conn.execute("CREATE TEMP TABLE garibaldi_shadow_exit_triggers(position_key text,model_version text,market text,symbol text,opened_at timestamptz,trigger_at timestamptz)")
        conn.execute("CREATE TEMP TABLE trade_ledger(trade_id text,broker_mode text,account_environment text,market text,symbol text,side text,entry_time text,exit_time text)")
        conn.execute("CREATE TEMP TABLE garibaldi_shadow_experiments(trade_id text,generation int,model_version text)")
        conn.execute("INSERT INTO positions VALUES ('crypto','BTC-USD',2,%s)", (opened.isoformat(),))
        conn.execute("INSERT INTO garibaldi_shadow_exit_triggers VALUES (%s,%s,'crypto','BTC-USD',%s,%s)", (key, shadow.MODEL_VERSION, opened, opened + timedelta(hours=1)))
        shadow._cleanup_consumed_triggers(conn, {key})
        assert conn.execute("SELECT COUNT(*) AS n FROM garibaldi_shadow_exit_triggers").fetchone()["n"] == 1
        assert key in shadow._POSITION_STATE
        # A later partial close still has the same frozen exit price available.
        conn.execute("UPDATE positions SET quantity=1")
        shadow._cleanup_consumed_triggers(conn, {key})
        assert conn.execute("SELECT COUNT(*) AS n FROM garibaldi_shadow_exit_triggers").fetchone()["n"] == 1
        conn.execute("UPDATE positions SET quantity=0")
        # A bounded finalization batch may settle one close while another close
        # from the same fully closed position waits for the next batch.
        # Metrics may not yet exist: cleanup must check canonical closes.
        for trade_id in ("batch-1", "batch-2"):
            conn.execute("INSERT INTO trade_ledger VALUES (%s,'PAPER','PAPER','crypto','BTC-USD','SELL',%s,%s)", (trade_id, opened.isoformat(), (opened + timedelta(hours=2)).isoformat()))
        conn.execute("INSERT INTO garibaldi_shadow_experiments VALUES ('batch-1',%s,%s)", (shadow.GENERATION, shadow.MODEL_VERSION))
        shadow._cleanup_consumed_triggers(conn, {key})
        assert conn.execute("SELECT COUNT(*) AS n FROM garibaldi_shadow_exit_triggers").fetchone()["n"] == 1
        assert key in shadow._POSITION_STATE
        conn.execute("INSERT INTO garibaldi_shadow_experiments VALUES ('batch-2',%s,%s)", (shadow.GENERATION, shadow.MODEL_VERSION))
        # Missing canonical event timestamps are unresolved evidence.
        conn.execute("INSERT INTO trade_ledger VALUES ('incomplete-close','PAPER','PAPER','crypto','BTC-USD','SELL',NULL,NULL)")
        shadow._cleanup_consumed_triggers(conn, {key})
        assert conn.execute("SELECT COUNT(*) AS n FROM garibaldi_shadow_exit_triggers").fetchone()["n"] == 1
        conn.execute("INSERT INTO garibaldi_shadow_experiments VALUES ('incomplete-close',%s,%s)", (shadow.GENERATION, shadow.MODEL_VERSION))
        shadow._cleanup_consumed_triggers(conn, {key})
        assert conn.execute("SELECT COUNT(*) AS n FROM garibaldi_shadow_exit_triggers").fetchone()["n"] == 0
        assert key not in shadow._POSITION_STATE


def test_configured_legacy_and_mixed_risk_are_audit_only_not_exact_pairs():
    for field, values in (
        ("risk_basis_source", ("configured_stop_loss_pct", "configured_stop_loss_pct_legacy_fallback", "unknown")),
        ("trigger_snapshot", ({"risk_basis_source": "mixed_entry_stop_and_configured_fallback"},
                              {"risk_basis_source": "configured_stop_loss_pct"}, {})),
    ):
        for value in values:
            records = [{**row, field: value} for row in _positive_experiments()]
            report = BrainCohortAnalyzerV4(records).evaluate_portfolio_safety_gate()
            assert report["observed_trade_count"] == 60
            assert report["trade_count"] == 0
            assert report["episode_count"] == 0
            assert report["exclusions"]["unqualified_risk_provenance"] == 60
            assert report["promotion_evidence_ready"] is False


def test_immutable_configured_risk_is_not_reported_as_exact_entry_stop_lots():
    class Result:
        def fetchall(self):
            return [{"entry_price": 100, "quantity_opened": 2, "quantity_remaining": 1,
                     "risk_snapshot": {"quantity": 2, "initial_risk_usd": 12,
                                       "risk_basis_source": "configured_stop_loss_pct"}}]
    class Conn:
        def execute(self, sql, params):
            return Result()
    risk, source = _open_position_risk_context(Conn(), "crypto", "BTC-USD", {})
    assert risk == 6
    assert source == "mixed_entry_stop_and_configured_fallback"


def test_relative_improvement_is_not_positive_post_cost_expectancy():
    records = _positive_experiments()
    for row in records:
        row["actual_realized_r_net"] = -0.2 if row["actual_realized_r_net"] < 0 else 0.02
        row["challenger_counterfactual_r_net"] = -0.1 if row["challenger_counterfactual_r_net"] < 0 else 0.04
    report = BrainCohortAnalyzerV4(records).evaluate_portfolio_safety_gate()
    assert report["conditions"]["paired_episode_ci_positive"] is True
    assert report["conditions"]["profit_factor_improving"] is True
    assert report["challenger_post_cost_expectancy_r"] < 0
    assert report["challenger_profit_factor"] < 1
    assert report["conditions"]["post_cost_expectancy_positive"] is False
    assert report["promotion_evidence_ready"] is False
    assert report["promotion_action"] == "NONE"


def test_unequal_risk_positive_r_expectancy_cannot_hide_dollar_losses():
    base = _positive_experiments()[0]
    records = []
    for episode in range(30):
        for leg in range(10):
            winner = leg < 9
            records.append({**base, "trade_id": f"unequal-{episode}-{leg}",
                "episode_id": f"unequal-episode-{episode}",
                "exit_time": base["exit_time"] + timedelta(days=episode, minutes=leg),
                "initial_risk_usd": 1.0 if winner else 100.0,
                "actual_realized_r_net": 0.9 if winner else -0.6,
                "challenger_counterfactual_r_net": 1.0 if winner else -0.5})
    report = BrainCohortAnalyzerV4(records).evaluate_portfolio_safety_gate()
    assert report["trade_count"] == 300
    assert report["episode_count"] == 30
    assert report["conditions"]["paired_episode_ci_positive"] is True
    assert report["conditions"]["profit_factor_improving"] is True
    assert report["challenger_post_cost_expectancy_r"] > 0
    assert abs(report["challenger_profit_factor"] - 9 / 50) < 1e-12
    assert abs(report["challenger_post_cost_net_pnl_usd"] - (-41 * 30)) < 1e-10
    assert abs(report["challenger_post_cost_expectancy_usd"] - (-4.1)) < 1e-12
    assert report["profit_factor_basis"] == "post_cost_pnl_usd"
    assert report["conditions"]["post_cost_expectancy_positive"] is False
    assert report["promotion_evidence_ready"] is False
    assert report["promotion_action"] == "NONE"
    assert report["execution_impact"] == "NONE"


def test_post_cost_dollar_totals_undo_risk_normalization_without_charging_costs_again():
    report = BrainCohortAnalyzerV4(_positive_experiments()).evaluate_portfolio_safety_gate()
    assert abs(report["actual_post_cost_net_pnl_usd"] - (-30)) < 1e-12
    assert abs(report["challenger_post_cost_net_pnl_usd"] - 45) < 1e-12
    assert abs(report["actual_post_cost_expectancy_usd"] - (-0.5)) < 1e-12
    assert abs(report["challenger_post_cost_expectancy_usd"] - 0.75) < 1e-12
    assert report["actual_profit_factor"] == 0.5
    assert report["challenger_profit_factor"] == 4.0
    assert report["actual_profit_factor_status"] == "available"
    assert report["challenger_profit_factor_status"] == "available"


def test_missing_nonfinite_or_nonpositive_initial_risk_cannot_make_dollar_evidence():
    base = _positive_experiments()[0]
    invalid = (None, float("nan"), float("inf"), -float("inf"), 0, -1, "missing")
    rows = [{**base, "trade_id": f"bad-risk-{index}", "initial_risk_usd": risk}
            for index, risk in enumerate(invalid)]
    report = BrainCohortAnalyzerV4(rows).evaluate_portfolio_safety_gate()
    assert report["observed_trade_count"] == len(invalid)
    assert report["trade_count"] == 0
    assert report["exclusions"]["invalid_initial_risk_usd"] == len(invalid)
    assert report["challenger_post_cost_net_pnl_usd"] is None
    assert report["challenger_post_cost_expectancy_usd"] is None
    assert report["promotion_evidence_ready"] is False
    base.pop("initial_risk_usd")
    assert BrainCohortAnalyzerV4([base]).evaluate_portfolio_safety_gate()["trade_count"] == 0


def test_no_losses_profit_factor_is_unavailable_with_explicit_status():
    records = [{**row, "actual_realized_r_net": 0.1, "challenger_counterfactual_r_net": 0.2}
               for row in _positive_experiments()]
    report = BrainCohortAnalyzerV4(records).evaluate_portfolio_safety_gate()
    assert report["actual_profit_factor"] is None
    assert report["challenger_profit_factor"] is None
    assert report["actual_profit_factor_status"] == "no_losses"
    assert report["challenger_profit_factor_status"] == "no_losses"
    assert report["challenger_post_cost_net_pnl_usd"] == 120
    assert report["promotion_evidence_ready"] is False


def test_nonfinite_dollar_product_is_unavailable_even_with_finite_r_and_risk():
    base = {**_positive_experiments()[0], "initial_risk_usd": 100,
            "actual_realized_r_net": 1e308, "challenger_counterfactual_r_net": 1e308}
    report = BrainCohortAnalyzerV4([base]).evaluate_portfolio_safety_gate()
    assert report["trade_count"] == 0
    assert report["exclusions"]["nonfinite_post_cost_usd"] == 1
    assert report["challenger_post_cost_net_pnl_usd"] is None
    assert report["promotion_evidence_ready"] is False
