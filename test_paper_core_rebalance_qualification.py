import global_adaptive_engine as adaptive
import runtime_integrity_patch as patch
import strategic_rebalance_optimizer_bridge as bridge

import paper_core_rebalance_qualification as qualification


def _enable_unbounded_paper(monkeypatch):
    monkeypatch.setenv("EXECUTION_MODE", "paper")
    monkeypatch.setenv("PAPER_AUTONOMOUS_LEARNING", "true")
    monkeypatch.setenv("PAPER_UNBOUNDED_LEARNING", "true")
    monkeypatch.setenv("ENABLE_BROKER_SUBMISSION", "false")
    monkeypatch.setenv("LIVE_TRADING_ARMED", "false")


def test_unbounded_gate_does_not_promote_hold_core_candidate(monkeypatch):
    _enable_unbounded_paper(monkeypatch)

    def denied_gate(item):
        return {"allowed": False, "reasons": ["at least three core signals must support the trade"]}

    monkeypatch.setattr(adaptive, "hard_risk_gate", denied_gate)
    monkeypatch.setattr(
        bridge,
        "_adaptive_meaningful_entry_floor",
        lambda item, *, equity, minimum_notional: minimum_notional,
    )

    qualification._install_unbounded_optimizer_policy()

    result = adaptive.hard_risk_gate(
        {
            "action": "HOLD",
            "core_rebalance_candidate": True,
            "portfolio_intent": patch.CORE_REBALANCE_CANDIDATE_INTENT,
        }
    )

    assert result["allowed"] is False
    assert result["reasons"] == ["at least three core signals must support the trade"]
    assert "authorization_basis" not in result


def test_unbounded_gate_still_relaxes_genuine_entry_action(monkeypatch):
    _enable_unbounded_paper(monkeypatch)

    def denied_gate(item):
        return {"allowed": False, "reasons": ["paper-observed tactical threshold"]}

    monkeypatch.setattr(adaptive, "hard_risk_gate", denied_gate)
    monkeypatch.setattr(
        bridge,
        "_adaptive_meaningful_entry_floor",
        lambda item, *, equity, minimum_notional: minimum_notional,
    )

    qualification._install_unbounded_optimizer_policy()

    result = adaptive.hard_risk_gate({"action": "BUY"})

    assert result["allowed"] is True
    assert result["reasons"] == []
    assert result["paper_observed_risk_reasons"] == ["paper-observed tactical threshold"]
    assert result["authorization_basis"] == "paper_unbounded_learning"
