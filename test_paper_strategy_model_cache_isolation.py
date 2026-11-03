"""Fail-first tests: cached sizing must not cross model governance identities.

All rows are synthetic. No production trade or profitability inference.
"""
import paper_strategy_economics as economics


def _profitable_rows():
    return [
        {"net_pnl": 1.0, "gross_pnl": 1.1, "fees": 0.1,
         "entry_time": None, "exit_time": None}
        for _ in range(30)
    ]


def _setup(monkeypatch):
    monkeypatch.setattr(economics, "_CACHE", {})
    monkeypatch.setenv("PAPER_STRATEGY_MAX_SIZE_MULTIPLIER", "1.25")
    monkeypatch.setenv("PAPER_RESEARCH_MAX_SIZE_MULTIPLIER", "0.35")
    monkeypatch.setenv("PAPER_STRATEGY_EXPLORATION_FLOOR", "0.35")
    calls = []
    def ledger(strategy, symbol=""):
        calls.append((strategy, symbol))
        return _profitable_rows()
    monkeypatch.setattr(economics, "_ledger_records", ledger)
    monkeypatch.setattr(
        economics, "model_validation_ok",
        lambda signal: signal.get("model_version") == "approved-v1"
    )
    monkeypatch.setattr(
        economics, "paper_model_tier",
        lambda signal: ("CAPITAL_QUALIFIED" if
                        signal.get("model_version") == "approved-v1"
                        else "RESEARCH")
    )
    return calls


def test_cached_capital_approval_cannot_transfer_to_other_model(monkeypatch):
    calls = _setup(monkeypatch)
    common = {"strategy": "same-strategy", "symbol": "BTC-USD"}
    approved = economics.strategy_economics({
        **common, "model": "approved-model", "model_version": "approved-v1"
    })
    unapproved = economics.strategy_economics({
        **common, "model": "unapproved-model", "model_version": "unapproved-v1"
    })
    assert approved.model_validated is True
    assert approved.size_multiplier == 1.25
    assert unapproved.model_validated is False
    assert unapproved.model_tier == "RESEARCH"
    assert unapproved.size_multiplier == 0.35
    assert len(calls) == 2


def test_cached_approval_cannot_transfer_to_new_version_of_same_model(monkeypatch):
    calls = _setup(monkeypatch)
    common = {"strategy": "same-strategy", "symbol": "ETH-USD", "model": "same-model"}
    first = economics.strategy_economics({**common, "model_version": "approved-v1"})
    second = economics.strategy_economics({**common, "model_version": "unapproved-v2"})
    assert first.size_multiplier == 1.25
    assert second.size_multiplier == 0.35
    assert second.model_validated is False
    assert len(calls) == 2


def test_same_model_version_and_symbol_still_share_bounded_cache(monkeypatch):
    calls = _setup(monkeypatch)
    signal = {"strategy": "same-strategy", "symbol": "BTC-USD",
              "model": "approved-model", "model_version": "approved-v1"}
    first = economics.strategy_economics(signal)
    second = economics.strategy_economics(dict(signal))
    assert first is second
    assert len(calls) == 1
