from types import SimpleNamespace

import market_worker


def _signal(symbol: str, score: float) -> SimpleNamespace:
    return SimpleNamespace(symbol=symbol, score=score, volume_ratio=1.0)


def test_crypto_context_benchmarks_are_deep_even_below_rank_cut(monkeypatch):
    monkeypatch.setattr(market_worker, "DEEP_ANALYSIS_CANDIDATES", 10)
    preliminary = [(_signal(f"ALT{i}-USD", 1.0 - i / 100.0), f"Alt {i}") for i in range(12)]
    preliminary += [(_signal("BTC-USD", 0.10), "Bitcoin"), (_signal("ETH-USD", 0.09), "Ethereum")]

    deep = market_worker._select_deep_candidates("crypto", preliminary, set())
    symbols = {signal.symbol for signal, _ in deep}

    assert {"BTC-USD", "ETH-USD"}.issubset(symbols)
    assert len(symbols) == 12


def test_cash_context_and_held_symbol_survive_rank_cut(monkeypatch):
    monkeypatch.setattr(market_worker, "DEEP_ANALYSIS_CANDIDATES", 10)
    preliminary = [(_signal(f"STK{i}", 1.0 - i / 100.0), f"Stock {i}") for i in range(12)]
    preliminary += [(_signal("SPY", 0.10), "SPDR S&P 500 ETF"), (_signal("QQQ", 0.09), "Invesco QQQ"), (_signal("HELD", 0.08), "Held")]

    deep = market_worker._select_deep_candidates("cash", preliminary, {"HELD"})
    symbols = {signal.symbol for signal, _ in deep}

    assert {"SPY", "QQQ", "HELD"}.issubset(symbols)


def test_context_selection_does_not_force_trade_actions(monkeypatch):
    monkeypatch.setattr(market_worker, "DEEP_ANALYSIS_CANDIDATES", 10)
    btc = SimpleNamespace(symbol="BTC-USD", action="HOLD", score=0.1)
    preliminary = [(SimpleNamespace(symbol=f"ALT{i}-USD", action="BUY", score=1.0), str(i)) for i in range(10)]
    preliminary.append((btc, "Bitcoin"))

    deep = market_worker._select_deep_candidates("crypto", preliminary, set())

    assert any(signal is btc for signal, _ in deep)
    assert btc.action == "HOLD"
