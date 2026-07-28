from datetime import datetime, timedelta, timezone
from oracle_one import finalize_decision


def good_kwargs():
    return dict(
        signal={"created_at": datetime.now(timezone.utc).isoformat()},
        recommendation="BUY",
        opportunity_score=84,
        probability_of_profit=64,
        risk_reward_ratio=2.1,
        quant={"net_expected_value_pct": 0.012},
        scenario={"approved": True, "veto": False},
        capital={"approved": True},
        portfolio_supercomputer={"approved": True, "recommended_trade_value": 125.0},
    )


def test_oracle_one_approves_consistent_trade(monkeypatch):
    monkeypatch.delenv("ORACLE_KILL_SWITCH", raising=False)
    verdict = finalize_decision(**good_kwargs())
    assert verdict.approved
    assert verdict.execution_ceiling == 125.0


def test_oracle_one_blocks_stale_signal(monkeypatch):
    monkeypatch.delenv("ORACLE_KILL_SWITCH", raising=False)
    kwargs = good_kwargs()
    kwargs["signal"] = {"created_at": (datetime.now(timezone.utc)-timedelta(hours=2)).isoformat()}
    verdict = finalize_decision(**kwargs, max_signal_age_minutes=30)
    assert not verdict.approved
    assert verdict.stale_data


def test_oracle_one_blocks_missing_execution_ceiling(monkeypatch):
    monkeypatch.delenv("ORACLE_KILL_SWITCH", raising=False)
    kwargs = good_kwargs()
    kwargs["portfolio_supercomputer"] = {"approved": True, "recommended_trade_value": 0}
    verdict = finalize_decision(**kwargs)
    assert not verdict.approved
    assert any("ceiling" in x.lower() for x in verdict.invariant_failures)
