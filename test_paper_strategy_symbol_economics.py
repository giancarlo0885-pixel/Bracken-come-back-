from types import SimpleNamespace

import paper_strategy_economics as econ


def _row(symbol, pnl):
    return {
        "strategy": "oracle_council_v3",
        "symbol": symbol,
        "net_pnl": pnl,
        "gross_pnl": pnl,
        "fees": 0.0,
        "entry_time": None,
        "exit_time": None,
    }


def test_strategy_economics_is_scoped_to_candidate_symbol(monkeypatch):
    econ._CACHE.clear()
    calls = []

    def records(strategy, symbol=""):
        calls.append((strategy, symbol))
        if symbol == "SOL-USD":
            return [_row(symbol, -1.0)] * 30
        if symbol == "AVAX-USD":
            return [_row(symbol, 1.0)] * 5
        return []

    monkeypatch.setattr(econ, "_ledger_records", records)
    monkeypatch.setattr(econ, "model_validation_ok", lambda signal: False)
    monkeypatch.setattr(econ, "paper_model_tier", lambda signal: "RESEARCH")

    sol = econ.strategy_economics({"symbol": "SOL-USD", "strategy": "Oracle Council V3"})
    avax = econ.strategy_economics({"symbol": "AVAX-USD", "strategy": "Oracle Council V3"})

    assert sol.sample_count == 30
    assert sol.expectancy < 0
    assert avax.sample_count == 5
    assert avax.expectancy > 0
    assert ("oracle_council_v3", "SOL-USD") in calls
    assert ("oracle_council_v3", "AVAX-USD") in calls


def test_negative_symbol_history_does_not_poison_other_symbol_fee_gate(monkeypatch):
    econ._CACHE.clear()

    def records(strategy, symbol=""):
        if symbol == "SOL-USD":
            return [_row(symbol, -1.0)] * 30
        return []

    monkeypatch.setattr(econ, "_ledger_records", records)
    monkeypatch.setattr(econ, "model_validation_ok", lambda signal: False)
    monkeypatch.setattr(econ, "paper_model_tier", lambda signal: "RESEARCH")

    base = {
        "strategy": "Oracle Council V3",
        "expected_edge_pct": 0.80,
        "estimated_cost_pct": 0.60,
    }
    sol_allowed, sol_reason, _, _ = econ.fee_edge_allows_entry({**base, "symbol": "SOL-USD"})
    link_allowed, link_reason, _, _ = econ.fee_edge_allows_entry({**base, "symbol": "LINK-USD"})

    assert sol_allowed is False
    assert "recovery_margin=1.50" in sol_reason
    assert link_allowed is True
    assert "recovery_margin" not in link_reason


def test_research_downsize_preserves_executable_paper_learning_floor(monkeypatch):
    econ._CACHE.clear()
    monkeypatch.setattr(econ, "active", lambda: True)
    scorecard = econ.StrategyEconomics(
        strategy="oracle_council_v3",
        sample_count=30,
        net_pnl=-10.0,
        gross_pnl=-8.0,
        fees=2.0,
        win_rate=0.2,
        average_win=1.0,
        average_loss=-1.0,
        profit_factor=0.25,
        expectancy=-0.33,
        average_holding_minutes=10.0,
        size_multiplier=0.35,
        model_validated=False,
        model_tier="RESEARCH",
    )
    monkeypatch.setattr(econ, "strategy_economics", lambda signal: scorecard)

    sized, _, reason = econ.adjusted_optimizer_target({"symbol": "LINK-USD"}, 5.28)

    assert sized == 2.0
    assert "paper_learning_executable_floor" in reason


def test_executable_floor_does_not_promote_original_subminimum_proposal(monkeypatch):
    econ._CACHE.clear()
    monkeypatch.setattr(econ, "active", lambda: True)
    scorecard = econ.StrategyEconomics(
        strategy="oracle_council_v3",
        sample_count=30,
        net_pnl=-10.0,
        gross_pnl=-8.0,
        fees=2.0,
        win_rate=0.2,
        average_win=1.0,
        average_loss=-1.0,
        profit_factor=0.25,
        expectancy=-0.33,
        average_holding_minutes=10.0,
        size_multiplier=0.35,
        model_validated=False,
        model_tier="RESEARCH",
    )
    monkeypatch.setattr(econ, "strategy_economics", lambda signal: scorecard)

    sized, _, reason = econ.adjusted_optimizer_target({"symbol": "LINK-USD"}, 1.50)

    assert sized == 0.53
    assert "paper_learning_executable_floor" not in reason
