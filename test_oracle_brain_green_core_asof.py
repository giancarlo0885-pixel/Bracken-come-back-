"""Regression: a future-dated signal cannot see future winner/loser lots.

All rows are synthetic; none represent production fills or profitability.
"""
from datetime import datetime, timedelta, timezone

import database
import oracle_brain
import oracle_brain_feedback as feedback
from paper_strategy_economics import strategy_identity


def test_future_dated_signal_caps_green_core_history_at_present(monkeypatch):
    now = datetime.now(timezone.utc)
    signal = {
        "symbol": "TEST-USD",
        "strategy": "Oracle Council V3",
        "regime": "range__low_vol",
        "momentum_5d": 0.03,
        "decision_timestamp": (now + timedelta(days=2)).isoformat(),
    }
    strategy = strategy_identity(signal)

    def episode(pnl, momentum, exit_time):
        return {
            "symbol": "TEST-USD",
            "strategy": strategy,
            "regime": "range__low_vol",
            "exit_time": exit_time.isoformat(),
            "net_pnl": pnl,
            "feature_snapshot": {"momentum_5d": momentum},
        }

    past = now - timedelta(days=1)
    future = now + timedelta(days=1)
    # Past outcomes penalize positive momentum. Future outcomes reverse it.
    records = (
        [episode(1.0, -0.1, past) for _ in range(20)]
        + [episode(-1.0, 0.1, past) for _ in range(20)]
        + [episode(1.0, 0.1, future) for _ in range(20)]
        + [episode(-1.0, -0.1, future) for _ in range(20)]
    )

    def fetch_rows(sql, params=()):
        if "paper_exit_research_reports" in sql:
            return [{"report": {"winner_entry_memory": {"records": records}}}]
        return []

    monkeypatch.setattr(database, "rows", fetch_rows)
    monkeypatch.setattr(
        oracle_brain, "runtime_safety_state",
        lambda: {"safe_research_boundary": True},
    )
    monkeypatch.setattr(
        feedback, "_counterfactual_memory_for_signal",
        lambda *args, **kwargs: {"adjustment": 0.0},
    )

    result = feedback.outcome_memory_for_signal(signal, market="crypto")
    green = result["green_core"]
    assert result["status"] == "ok"
    assert green["status"] == "ok"
    assert green["samples"] == 40
    assert green["adjustment"] < 0
