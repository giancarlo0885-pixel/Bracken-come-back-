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
