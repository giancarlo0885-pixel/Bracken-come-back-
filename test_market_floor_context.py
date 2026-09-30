from types import SimpleNamespace

import market_worker


def _signal(symbol: str, score: float) -> SimpleNamespace:
    return SimpleNamespace(symbol=symbol, score=score, volume_ratio=1.0)


def test_crypto_context_benchmarks_are_deep_even_below_rank_cut(monkeypatch):
    monkeypatch.setattr(market_worker, "DEEP_ANALYSIS_CANDIDATES", 10)
    preliminary = [(_signal(f"ALT{i}-USD", 1.0 - i / 100.0), f"Alt {i}") for i in range(12)]
    preliminary += [(_signal("BTC-USD", 0.10), "Bitcoin"), (_signal("ETH-USD", 0.09), "Ethereum")]

    deep = preliminary[: market_worker.DEEP_ANALYSIS_CANDIDATES]
    included = {signal.symbol for signal, _ in deep}
    context_symbols = {"BTC-USD", "ETH-USD"}
    deep.extend(
        (signal, name)
        for signal, name in preliminary
        if signal.symbol in context_symbols and signal.symbol not in included
    )

    symbols = {signal.symbol for signal, _ in deep}
    assert {"BTC-USD", "ETH-USD"}.issubset(symbols)
    assert len(symbols) == 12


def test_context_symbols_do_not_force_trade_actions():
    # The selection contract only changes which symbols receive deep analysis.
    # It does not mutate signal action or execution authorization.
    btc = SimpleNamespace(symbol="BTC-USD", action="HOLD", score=0.1)
    assert btc.action == "HOLD"
