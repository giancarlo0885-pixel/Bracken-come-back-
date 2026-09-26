from types import SimpleNamespace

import global_adaptive_engine as adaptive
import runtime_integrity_patch as patch
import strategic_rebalance_optimizer_bridge as bridge
from core_rebalance_optimizer_trace import _trace_state_changed
from strategic_rebalance_optimizer_bridge import (
    _strategic_rebalance_gate,
    install_strategic_rebalance_optimizer_bridge,
)


class _Log:
    def __init__(self):
        self.info_records = []
        self.debug_records = []

    def info(self, *args, **kwargs):
        self.info_records.append((args, kwargs))

    def debug(self, *args, **kwargs):
        self.debug_records.append((args, kwargs))


def _candidate(**overrides):
    item = {
        "symbol": "BTC-USD",
        "asset_class": "crypto",
        "qualified_for_capital": True,
        "core_rebalance_candidate": True,
        "portfolio_intent": patch.CORE_REBALANCE_CANDIDATE_INTENT,
        "tactical_action": "HOLD",
        "opportunity_score": 95.0,
        "avg_dollar_volume": 1_000_000_000.0,
        "spread_pct": 0.01,
        "sector": "Crypto",
    }
    item.update(overrides)
    return item


def _worker():
    return SimpleNamespace(adaptive_portfolio_optimizer=adaptive.adaptive_portfolio_optimizer, log=_Log())


def test_explicit_strategic_rebalance_separates_only_tactical_authorization(monkeypatch):
    monkeypatch.setattr(
        adaptive,
        "hard_risk_gate",
        lambda item: {
            "allowed": False,
            "reasons": [
                "at least three core signals must support the trade",
                "confidence below trade threshold",
            ],
            "core_signals_supporting": 2,
            "confidence_score": 61.05,
            "reward_risk_ratio": 2.596,
        },
    )

    gate = _strategic_rebalance_gate(_candidate())

    assert gate["allowed"] is True
    assert gate["reasons"] == []
    assert gate["authorization_basis"] == "explicit_core_rebalance_target_gap"
    assert len(gate["tactical_authorization_reasons"]) == 2


def test_execution_safety_reason_still_blocks_strategic_rebalance(monkeypatch):
    monkeypatch.setattr(
        adaptive,
        "hard_risk_gate",
        lambda item: {
            "allowed": False,
            "reasons": [
                "at least three core signals must support the trade",
                "confidence below trade threshold",
                "quote verification failed",
            ],
        },
    )

    gate = _strategic_rebalance_gate(_candidate())

    assert gate["allowed"] is False
    assert gate["reasons"] == ["quote verification failed"]
    assert gate["authorization_basis"] == "blocked_by_execution_safety"


def test_ordinary_candidate_never_gets_strategic_exception(monkeypatch):
    monkeypatch.setattr(
        adaptive,
        "hard_risk_gate",
        lambda item: {
            "allowed": False,
            "reasons": ["confidence below trade threshold"],
        },
    )

    ordinary = _candidate(core_rebalance_candidate=False, portfolio_intent="", tactical_action="BUY")
    gate = _strategic_rebalance_gate(ordinary)

    assert gate["allowed"] is False
    assert gate["reasons"] == ["confidence below trade threshold"]


def test_crypto_optimizer_allocates_explicit_rebalance_without_using_broker_capital(monkeypatch):
    worker = _worker()
    monkeypatch.setattr(
        adaptive,
        "hard_risk_gate",
        lambda item: {
            "allowed": False,
            "reasons": [
                "at least three core signals must support the trade",
                "confidence below trade threshold",
            ],
        },
    )
    install_strategic_rebalance_optimizer_bridge(worker)

    plan = worker.adaptive_portfolio_optimizer(
        [_candidate()],
        {
            "cash": 2000.0,
            "equity": 2000.0,
            "buying_power": 2000.0,
            "broker_equity": 1_000_000.0,
            "broker_buying_power": 1_000_000.0,
        },
        [],
        engine="crypto",
    )

    assert len(plan["allocations"]) == 1
    allocation = plan["allocations"][0]
    assert allocation["symbol"] == "BTC-USD"
    assert allocation["amount"] >= 10.0
    assert allocation["amount"] <= 2000.0 * adaptive.GLOBAL_PIT_PREFERRED_POSITION_PCT
    assert allocation["amount"] <= 2000.0 - 2000.0 * adaptive.GLOBAL_PIT_RESERVE_PCT
    assert allocation["amount"] < 1_000_000.0
    assert allocation["authorization_basis"] == "explicit_core_rebalance_target_gap"
    assert plan["meaningful_entry_floor"] == 10.0


def test_crypto_optimizer_converts_tiny_rebalance_to_watch_candidate(monkeypatch):
    worker = _worker()
    monkeypatch.setattr(
        adaptive,
        "hard_risk_gate",
        lambda item: {
            "allowed": False,
            "reasons": [
                "at least three core signals must support the trade",
                "confidence below trade threshold",
            ],
        },
    )
    install_strategic_rebalance_optimizer_bridge(worker)

    plan = worker.adaptive_portfolio_optimizer(
        [_candidate(core_target_amount=0.10)],
        {"cash": 2000.0, "equity": 2000.0, "buying_power": 2000.0},
        [],
        engine="crypto",
    )

    assert plan["allocations"] == []
    assert plan["rejections"]
    rejection = plan["rejections"][0]
    assert rejection["reason"] == "watch_momentum_candidate"
    assert rejection["watch_only"] is True
    assert rejection["proposed_amount"] == 0.10
    assert rejection["meaningful_entry_floor"] == 10.0
    assert rejection["minimum_notional"] >= 2.0
    assert rejection["tiny_gap_cooldown"] is True
    assert rejection["cooldown_active"] is False
    assert rejection["cooldown_scope"] == "symbol"


def test_tiny_gap_cooldown_is_per_symbol_and_repeated_watch_logs_debug(monkeypatch):
    worker = _worker()
    monkeypatch.setattr(adaptive, "hard_risk_gate", lambda item: {"allowed": True, "reasons": []})
    monkeypatch.setenv("CRYPTO_TINY_GAP_COOLDOWN_SECONDS", "300")
    install_strategic_rebalance_optimizer_bridge(worker)
    portfolio = {"cash": 2000.0, "equity": 2000.0, "buying_power": 2000.0}

    first = worker.adaptive_portfolio_optimizer(
        [_candidate(symbol="BNB-USD", core_target_amount=0.10)], portfolio, [], engine="crypto"
    )
    repeated = worker.adaptive_portfolio_optimizer(
        [_candidate(symbol="BNB-USD", core_target_amount=0.10)], portfolio, [], engine="crypto"
    )
    other_symbol = worker.adaptive_portfolio_optimizer(
        [_candidate(symbol="ADA-USD", core_target_amount=0.10)], portfolio, [], engine="crypto"
    )

    assert first["rejections"][0]["cooldown_active"] is False
    assert repeated["rejections"][0]["cooldown_active"] is True
    assert other_symbol["rejections"][0]["cooldown_active"] is False
    assert len(worker.log.info_records) == 2
    assert len(worker.log.debug_records) == 1


def test_tiny_gap_state_change_breaks_quiet_period(monkeypatch):
    worker = _worker()
    monkeypatch.setattr(adaptive, "hard_risk_gate", lambda item: {"allowed": True, "reasons": []})
    monkeypatch.setenv("CRYPTO_TINY_GAP_COOLDOWN_SECONDS", "300")
    install_strategic_rebalance_optimizer_bridge(worker)
    portfolio = {"cash": 2000.0, "equity": 2000.0, "buying_power": 2000.0}

    worker.adaptive_portfolio_optimizer(
        [_candidate(symbol="BNB-USD", core_target_amount=0.10, regime="range")], portfolio, [], engine="crypto"
    )
    changed = worker.adaptive_portfolio_optimizer(
        [_candidate(symbol="BNB-USD", core_target_amount=0.10, regime="trend")], portfolio, [], engine="crypto"
    )

    assert changed["rejections"][0]["cooldown_active"] is False
    assert len(worker.log.info_records) == 2
    assert worker.log.debug_records == []


def test_core_rebalance_trace_changes_only_when_symbol_state_changes():
    worker = _worker()
    tiny = {
        "BNB-USD": {
            "status": "REJECTED",
            "reason": "watch_momentum_candidate",
            "proposed_amount": 0.10,
            "meaningful_entry_floor": 10.0,
        }
    }
    executable = {
        "BNB-USD": {
            "status": "CANDIDATE_ALLOCATED",
            "reason": "candidate_capital_reserved_for_downstream_validation",
            "candidate_amount": 25.0,
            "meaningful_entry_floor": 10.0,
        }
    }

    assert _trace_state_changed(worker, tiny) is True
    assert _trace_state_changed(worker, tiny) is False
    assert _trace_state_changed(worker, executable) is True


def test_crypto_optimizer_uses_locked_execution_minimum_before_approval(monkeypatch):
    worker = _worker()
    monkeypatch.setattr(adaptive, "hard_risk_gate", lambda item: {"allowed": True, "reasons": []})
    monkeypatch.setattr(bridge, "MIN_TRADE_VALUE", 1.0)
    monkeypatch.setattr(bridge, "MIN_TRADE_NOTIONAL", 2.0)
    install_strategic_rebalance_optimizer_bridge(worker)

    plan = worker.adaptive_portfolio_optimizer(
        [_candidate(core_target_amount=1.05, tactical_action="BUY")],
        {"cash": 174.65, "equity": 174.65, "buying_power": 174.65},
        [],
        engine="crypto",
    )

    assert plan["allocations"] == []
    assert plan["rejections"][0]["reason"] == "watch_momentum_candidate"
    assert plan["rejections"][0]["proposed_amount"] == 1.05
    assert plan["rejections"][0]["minimum_notional"] == 2.0
    assert plan["rejections"][0]["meaningful_entry_floor"] >= 2.0


def test_crypto_optimizer_keeps_just_under_adaptive_floor_as_watch(monkeypatch):
    worker = _worker()
    monkeypatch.setattr(adaptive, "hard_risk_gate", lambda item: {"allowed": True, "reasons": []})
    install_strategic_rebalance_optimizer_bridge(worker)

    plan = worker.adaptive_portfolio_optimizer(
        [_candidate(core_target_amount=9.99, tactical_action="BUY")],
        {"cash": 2000.0, "equity": 2000.0, "buying_power": 2000.0},
        [],
        engine="crypto",
    )

    assert plan["allocations"] == []
    assert plan["rejections"][0]["reason"] == "watch_momentum_candidate"
    assert plan["rejections"][0]["meaningful_entry_floor"] == 10.0


def test_crypto_optimizer_keeps_hard_execution_failure_at_zero(monkeypatch):
    worker = _worker()
    monkeypatch.setattr(
        adaptive,
        "hard_risk_gate",
        lambda item: {
            "allowed": False,
            "reasons": [
                "confidence below trade threshold",
                "verified quote is stale or unavailable for execution",
            ],
        },
    )
    install_strategic_rebalance_optimizer_bridge(worker)

    plan = worker.adaptive_portfolio_optimizer(
        [_candidate()],
        {"cash": 2000.0, "equity": 2000.0, "buying_power": 2000.0},
        [],
        engine="crypto",
    )

    assert plan["allocations"] == []
    assert plan["rejections"]
    assert "verified quote is stale or unavailable for execution" in plan["rejections"][0]["risk_reasons"]


def test_stock_optimizer_is_unchanged_delegate():
    calls = []

    def original(opportunities, portfolio, positions, *, engine):
        calls.append(engine)
        return {"allocations": ["delegated"]}

    worker = SimpleNamespace(adaptive_portfolio_optimizer=original, log=_Log())
    install_strategic_rebalance_optimizer_bridge(worker)

    plan = worker.adaptive_portfolio_optimizer([], {}, [], engine="stock")

    assert plan == {"allocations": ["delegated"]}
    assert calls == ["stock"]


def test_crypto_optimizer_does_not_approve_known_negative_economics(monkeypatch):
    worker = _worker()
    hard_gate_calls = []
    monkeypatch.setattr(
        adaptive,
        "hard_risk_gate",
        lambda item: hard_gate_calls.append(item) or {"allowed": True, "reasons": []},
    )
    monkeypatch.setattr(
        bridge,
        "fee_edge_allows_entry",
        lambda item: (
            False,
            "edge_unavailable_known_negative_economics:samples=46:expectancy=-0.276231:pf=0.2818",
            None,
            0.6501,
        ),
    )
    install_strategic_rebalance_optimizer_bridge(worker)

    plan = worker.adaptive_portfolio_optimizer(
        [_candidate(tactical_action="BUY", cohort="core_alt", strategy_name="oracle_council_v3", regime="range")],
        {"cash": 2000.0, "equity": 2000.0, "buying_power": 2000.0},
        [],
        engine="crypto",
    )

    assert plan["allocations"] == []
    assert plan["rejections"][0]["reason"] == "economics_blocked"
    assert plan["rejections"][0]["watch_only"] is True
    assert plan["rejections"][0]["expected_edge_pct"] is None
    assert plan["rejections"][0]["estimated_round_trip_cost_pct"] == 0.6501
    assert plan["rejections"][0]["economics_identity"] == {
        "cohort": "core_alt",
        "strategy": "oracle_council_v3",
        "regime": "range",
    }
    assert hard_gate_calls == []


def test_crypto_optimizer_reclassifies_allowed_missing_edge_as_bounded_exploration(monkeypatch):
    _enable_paper_unbounded(monkeypatch)
    worker = _worker()
    monkeypatch.setattr(adaptive, "hard_risk_gate", lambda item: {"allowed": True, "reasons": []})
    monkeypatch.setattr(bridge, "_adaptive_meaningful_entry_floor", lambda item, *, equity, minimum_notional: 2.0)
    monkeypatch.setattr(
        bridge,
        "fee_edge_allows_entry",
        lambda item: (True, "edge_unavailable_insufficient_evidence_exploration", None, 0.50),
    )
    install_strategic_rebalance_optimizer_bridge(worker)

    plan = worker.adaptive_portfolio_optimizer(
        [_candidate(
            tactical_action="BUY",
            paper_unbounded_learning=True,
            core_target_amount=25.0,
        )],
        {"cash": 2000.0, "equity": 2000.0, "buying_power": 2000.0},
        [],
        engine="crypto",
    )

    assert len(plan["allocations"]) == 1
    allocation = plan["allocations"][0]
    assert allocation["amount"] == 5.0
    assert allocation["paper_learning_exploration"] is True
    assert allocation["economics_observed_only"] is True
    assert allocation["economics_reason"] == "edge_unavailable_insufficient_evidence_exploration"
    assert allocation["expected_edge_pct"] is None
    assert allocation["estimated_round_trip_cost_pct"] == 0.50


def test_trace_allocation_is_candidate_not_execution_approval():
    from core_rebalance_optimizer_trace import _optimizer_decision_index
    decisions = _optimizer_decision_index(
        [{"symbol": "BTC-USD", "action": "BUY"}],
        {"allocations": [{"symbol": "BTC-USD", "amount": 25.0}], "rejections": []},
    )
    assert decisions["BTC-USD"]["status"] == "CANDIDATE_ALLOCATED"
    assert decisions["BTC-USD"]["reason"] == "candidate_capital_reserved_for_downstream_validation"
    assert decisions["BTC-USD"]["candidate_amount"] == 25.0
    assert "approved_amount" not in decisions["BTC-USD"]


def test_expected_edge_consumes_explicit_calibrated_provenance():
    from paper_strategy_economics import expected_edge_pct
    assert expected_edge_pct({"calibrated_expected_edge_pct": 0.42}) == 0.42
    assert expected_edge_pct({"expected_edge_pct": -0.31}) == -0.31


def _enable_paper_unbounded(monkeypatch):
    monkeypatch.setenv("EXECUTION_MODE", "paper")
    monkeypatch.setenv("PAPER_AUTONOMOUS_LEARNING", "true")
    monkeypatch.setenv("PAPER_UNBOUNDED_LEARNING", "true")
    monkeypatch.setenv("ENABLE_BROKER_SUBMISSION", "false")
    monkeypatch.setenv("LIVE_TRADING_ARMED", "false")


def test_paper_unbounded_learning_keeps_known_negative_economics_observation_only(monkeypatch):
    _enable_paper_unbounded(monkeypatch)
    worker = _worker()
    hard_gate_calls = []
    monkeypatch.setattr(
        adaptive,
        "hard_risk_gate",
        lambda item: hard_gate_calls.append(item) or {"allowed": True, "reasons": []},
    )
    monkeypatch.setattr(bridge, "_adaptive_meaningful_entry_floor", lambda item, *, equity, minimum_notional: 2.0)
    monkeypatch.setattr(
        bridge,
        "fee_edge_allows_entry",
        lambda item: (False, "edge_below_round_trip_cost:-0.30<0.55", -0.30, 0.55),
    )
    install_strategic_rebalance_optimizer_bridge(worker)

    plan = worker.adaptive_portfolio_optimizer(
        [_candidate(
            tactical_action="BUY",
            paper_unbounded_learning=True,
            core_target_amount=25.0,
        )],
        {"cash": 2000.0, "equity": 2000.0, "buying_power": 2000.0},
        [],
        engine="crypto",
    )

    assert plan["allocations"] == []
    assert plan["rejections"][0]["reason"] == "economics_blocked"
    assert plan["rejections"][0]["watch_only"] is True
    assert plan["rejections"][0]["expected_edge_pct"] == -0.30
    assert plan["rejections"][0]["estimated_round_trip_cost_pct"] == 0.55
    assert hard_gate_calls == []


def test_paper_unbounded_learning_allows_bounded_exploration_for_insufficient_evidence(monkeypatch):
    _enable_paper_unbounded(monkeypatch)
    worker = _worker()
    monkeypatch.setattr(adaptive, "hard_risk_gate", lambda item: {"allowed": True, "reasons": []})
    monkeypatch.setattr(bridge, "_adaptive_meaningful_entry_floor", lambda item, *, equity, minimum_notional: 2.0)
    monkeypatch.setattr(
        bridge,
        "fee_edge_allows_entry",
        lambda item: (False, "edge_unavailable_insufficient_evidence", None, 0.55),
    )
    install_strategic_rebalance_optimizer_bridge(worker)

    plan = worker.adaptive_portfolio_optimizer(
        [_candidate(
            tactical_action="BUY",
            paper_unbounded_learning=True,
            core_target_amount=25.0,
        )],
        {"cash": 2000.0, "equity": 2000.0, "buying_power": 2000.0},
        [],
        engine="crypto",
    )

    assert len(plan["allocations"]) == 1
    allocation = plan["allocations"][0]
    assert allocation["amount"] == 5.0
    assert allocation["paper_learning_exploration"] is True
    assert allocation["economics_observed_only"] is True
    assert allocation["expected_edge_pct"] is None
    assert allocation["estimated_round_trip_cost_pct"] == 0.55
    assert plan["rejections"] == []

def test_paper_unbounded_learning_promotes_tiny_strategic_gap_to_minimum_learning_fill(monkeypatch):
    _enable_paper_unbounded(monkeypatch)
    worker = _worker()
    monkeypatch.setattr(adaptive, "hard_risk_gate", lambda item: {"allowed": True, "reasons": []})
    monkeypatch.setattr(bridge, "_adaptive_meaningful_entry_floor", lambda item, *, equity, minimum_notional: 2.0)
    monkeypatch.setattr(
        bridge,
        "fee_edge_allows_entry",
        lambda item: (True, "edge_ok", 0.25, 0.10),
    )
    install_strategic_rebalance_optimizer_bridge(worker)

    plan = worker.adaptive_portfolio_optimizer(
        [_candidate(
            tactical_action="BUY",
            paper_unbounded_learning=True,
            core_target_amount=0.02,
        )],
        {"cash": 2000.0, "equity": 2000.0, "buying_power": 2000.0},
        [],
        engine="crypto",
    )

    assert len(plan["allocations"]) == 1
    allocation = plan["allocations"][0]
    assert allocation["amount"] == 2.0
    assert allocation["paper_learning_exploration"] is True
    assert allocation["economics_observed_only"] is False
    assert allocation["economics_reason"] == "edge_ok"
    assert allocation["expected_edge_pct"] == 0.25
    assert allocation["estimated_round_trip_cost_pct"] == 0.10
    assert plan["rejections"] == []


def test_paper_exploration_requires_explicit_safe_qualification(monkeypatch):
    _enable_paper_unbounded(monkeypatch)
    worker = _worker()
    monkeypatch.setattr(adaptive, "hard_risk_gate", lambda item: {"allowed": True, "reasons": []})
    monkeypatch.setattr(
        bridge,
        "fee_edge_allows_entry",
        lambda item: (False, "known_negative", -0.40, 0.20),
    )
    install_strategic_rebalance_optimizer_bridge(worker)

    plan = worker.adaptive_portfolio_optimizer(
        [_candidate(tactical_action="BUY", paper_unbounded_learning=False)],
        {"cash": 2000.0, "equity": 2000.0, "buying_power": 2000.0},
        [],
        engine="crypto",
    )

    assert plan["allocations"] == []
    assert plan["rejections"][0]["reason"] == "economics_blocked"


def test_paper_exploration_cannot_activate_when_broker_submission_is_enabled(monkeypatch):
    _enable_paper_unbounded(monkeypatch)
    monkeypatch.setenv("ENABLE_BROKER_SUBMISSION", "true")
    worker = _worker()
    monkeypatch.setattr(adaptive, "hard_risk_gate", lambda item: {"allowed": True, "reasons": []})
    monkeypatch.setattr(
        bridge,
        "fee_edge_allows_entry",
        lambda item: (False, "known_negative", -0.40, 0.20),
    )
    install_strategic_rebalance_optimizer_bridge(worker)

    plan = worker.adaptive_portfolio_optimizer(
        [_candidate(tactical_action="BUY", paper_unbounded_learning=True)],
        {"cash": 2000.0, "equity": 2000.0, "buying_power": 2000.0},
        [],
        engine="crypto",
    )

    assert plan["allocations"] == []
    assert plan["rejections"][0]["reason"] == "economics_blocked"
