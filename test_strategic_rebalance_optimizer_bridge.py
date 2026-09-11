from types import SimpleNamespace

import global_adaptive_engine as adaptive
import runtime_integrity_patch as patch
import strategic_rebalance_optimizer_bridge as bridge
from strategic_rebalance_optimizer_bridge import (
    _strategic_rebalance_gate,
    install_strategic_rebalance_optimizer_bridge,
)


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
    worker = SimpleNamespace(adaptive_portfolio_optimizer=adaptive.adaptive_portfolio_optimizer)
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
    assert allocation["amount"] >= 20.0
    assert allocation["amount"] <= 2000.0 * adaptive.GLOBAL_PIT_PREFERRED_POSITION_PCT
    assert allocation["amount"] <= 2000.0 - 2000.0 * adaptive.GLOBAL_PIT_RESERVE_PCT
    assert allocation["amount"] < 1_000_000.0
    assert allocation["authorization_basis"] == "explicit_core_rebalance_target_gap"
    assert plan["meaningful_entry_floor"] == 20.0


def test_crypto_optimizer_converts_tiny_rebalance_to_watch_candidate(monkeypatch):
    worker = SimpleNamespace(adaptive_portfolio_optimizer=adaptive.adaptive_portfolio_optimizer)
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
    assert rejection["meaningful_entry_floor"] == 20.0
    assert rejection["minimum_notional"] >= 2.0


def test_crypto_optimizer_uses_locked_execution_minimum_before_approval(monkeypatch):
    worker = SimpleNamespace(adaptive_portfolio_optimizer=adaptive.adaptive_portfolio_optimizer)
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


def test_crypto_optimizer_keeps_just_under_one_percent_as_watch(monkeypatch):
    worker = SimpleNamespace(adaptive_portfolio_optimizer=adaptive.adaptive_portfolio_optimizer)
    monkeypatch.setattr(adaptive, "hard_risk_gate", lambda item: {"allowed": True, "reasons": []})
    install_strategic_rebalance_optimizer_bridge(worker)

    plan = worker.adaptive_portfolio_optimizer(
        [_candidate(core_target_amount=19.99, tactical_action="BUY")],
        {"cash": 2000.0, "equity": 2000.0, "buying_power": 2000.0},
        [],
        engine="crypto",
    )

    assert plan["allocations"] == []
    assert plan["rejections"][0]["reason"] == "watch_momentum_candidate"
    assert plan["rejections"][0]["meaningful_entry_floor"] == 20.0


def test_crypto_optimizer_keeps_hard_execution_failure_at_zero(monkeypatch):
    worker = SimpleNamespace(adaptive_portfolio_optimizer=adaptive.adaptive_portfolio_optimizer)
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

    worker = SimpleNamespace(adaptive_portfolio_optimizer=original)
    install_strategic_rebalance_optimizer_bridge(worker)

    plan = worker.adaptive_portfolio_optimizer([], {}, [], engine="stock")

    assert plan == {"allocations": ["delegated"]}
    assert calls == ["stock"]