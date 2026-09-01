from datetime import datetime, timezone
from types import SimpleNamespace

import runtime_integrity_patch as patch


def _signal(symbol, score=92.0):
    return SimpleNamespace(
        symbol=symbol,
        action="HOLD",
        score=score,
        confidence=0.91,
        risk_score=0.2,
        signal_id=f"sig-{symbol}",
        forecast_id=f"fc-{symbol}",
        created_at=datetime.now(timezone.utc).isoformat(),
    )


def _quote(symbol, price):
    now = datetime.now(timezone.utc).isoformat()
    return {
        "symbol": symbol,
        "requested_symbol": symbol,
        "provider_symbol": symbol,
        "provider": "unit-paper-reference",
        "price": price,
        "quote_timestamp": now,
        "interval": "5m",
        "source_interval": "5m",
        "quote_verified": True,
        "tradeable": True,
        "avg_dollar_volume": 50_000_000.0,
        "data_quality_score": 1.0,
    }


def test_empty_crypto_portfolio_rebalance_reaches_existing_paper_path_and_reloads(monkeypatch):
    import market_worker

    original_opportunity = market_worker._v39_signal_opportunity
    original_prioritize = market_worker._v39_prioritize_signals
    position_reads = []
    optimizer_calls = []
    paper_calls = []

    def position_rows(market):
        position_reads.append(market)
        if len(paper_calls) == 0:
            return {"cash": 2000.0, "equity": 2000.0, "buying_power": 2000.0}, []
        return (
            {"cash": 1800.0, "equity": 2000.0, "buying_power": 1800.0},
            [{"symbol": "BTC-USD", "quantity": 0.002, "current_price": 100000.0, "market_value": 200.0, "sector": "Crypto"}],
        )

    def optimizer(opportunities, portfolio, positions, *, engine):
        optimizer_calls.append((opportunities, portfolio, positions, engine))
        assert engine == "crypto"
        assert any(row.get("qualified_for_capital") is True for row in opportunities)
        if not positions:
            return {
                "allocations": [{"symbol": "BTC-USD", "amount": 200.0, "sector": "Crypto", "liquidity": {"allowed": True}}],
                "rejections": [],
            }
        return {"allocations": [], "rejections": []}

    def paper_process(market, signals, prices):
        signal = signals[0]
        paper_calls.append((market, signal, prices))
        assert market == "crypto"
        assert signal.symbol == "BTC-USD"
        assert signal.action == "ACCUMULATE"
        assert signal.rebalance_intent == patch.CORE_REBALANCE_BUY_INTENT
        assert signal.v39_optimizer_approved_amount == 200.0
        return [{"action": "BUY", "symbol": signal.symbol, "price": prices[signal.symbol]["price"], "quantity": 0.002}]

    monkeypatch.setattr(market_worker, "_v39_position_rows", position_rows)
    monkeypatch.setattr(market_worker, "adaptive_portfolio_optimizer", optimizer)
    monkeypatch.setattr(market_worker, "process_signals", paper_process)
    monkeypatch.setattr(market_worker, "_v39_record_event", lambda *args, **kwargs: None)
    monkeypatch.setattr(market_worker, "_execution_quote_eligible", lambda payload: True)
    monkeypatch.setattr(market_worker, "GLOBAL_PIT_MODE", True)
    monkeypatch.setattr(market_worker, "HIGH_SCORE_THRESHOLD", 70.0)
    monkeypatch.setattr(market_worker, "HIGH_CONFIDENCE_THRESHOLD", 0.78)

    try:
        patch._install_core_rebalance_producer(market_worker)
        signals = [_signal("BTC-USD"), _signal("ETH-USD", score=85.0)]
        prices = {
            "BTC-USD": _quote("BTC-USD", 100000.0),
            "ETH-USD": _quote("ETH-USD", 5000.0),
        }
        ranked = [
            {"symbol": "BTC-USD", "opportunity_score": 95.0, "spread_pct": 0.001, "risk_score": 0.2, "sector": "Crypto"},
            {"symbol": "ETH-USD", "opportunity_score": 88.0, "spread_pct": 0.001, "risk_score": 0.2, "sector": "Crypto"},
        ]

        actions = market_worker._v39_execute_iterative("crypto", signals, prices, ranked, "deep")

        assert len(actions) == 1  # executed_actions > 0
        assert actions[0]["action"] == "BUY"
        assert len(paper_calls) == 1
        assert len(optimizer_calls) >= 2
        assert len(position_reads) >= 4  # candidate scan + optimizer snapshot, then reload after execution
        assert optimizer_calls[-1][2]  # post-execution optimizer saw the newly held BTC position
    finally:
        market_worker._v39_signal_opportunity = original_opportunity
        market_worker._v39_prioritize_signals = original_prioritize


def test_core_rebalance_path_does_not_bypass_quote_gate(monkeypatch):
    import market_worker

    original_opportunity = market_worker._v39_signal_opportunity
    original_prioritize = market_worker._v39_prioritize_signals
    buy_called = False

    monkeypatch.setattr(
        market_worker,
        "_v39_position_rows",
        lambda market: ({"cash": 2000.0, "equity": 2000.0, "buying_power": 2000.0}, []),
    )
    monkeypatch.setattr(market_worker, "_execution_quote_eligible", lambda payload: False)
    monkeypatch.setattr(market_worker, "_v39_record_event", lambda *args, **kwargs: None)
    monkeypatch.setattr(market_worker, "HIGH_SCORE_THRESHOLD", 70.0)
    monkeypatch.setattr(market_worker, "HIGH_CONFIDENCE_THRESHOLD", 0.78)

    def optimizer(opportunities, portfolio, positions, *, engine):
        assert all(row.get("qualified_for_capital") is False for row in opportunities)
        return {"allocations": [], "rejections": []}

    def should_not_buy(*args, **kwargs):
        nonlocal buy_called
        buy_called = True
        raise AssertionError("paper execution must not run when quote freshness gate fails")

    monkeypatch.setattr(market_worker, "adaptive_portfolio_optimizer", optimizer)
    monkeypatch.setattr(market_worker, "process_signals", should_not_buy)

    try:
        patch._install_core_rebalance_producer(market_worker)
        signal = _signal("BTC-USD")
        actions = market_worker._v39_execute_iterative(
            "crypto",
            [signal],
            {"BTC-USD": _quote("BTC-USD", 100000.0)},
            [{"symbol": "BTC-USD", "opportunity_score": 95.0, "spread_pct": 0.001, "risk_score": 0.2, "sector": "Crypto"}],
            "deep",
        )
        assert actions == []
        assert buy_called is False
        assert signal.action == "HOLD"
        assert getattr(signal, "rebalance_intent", None) is None
    finally:
        market_worker._v39_signal_opportunity = original_opportunity
        market_worker._v39_prioritize_signals = original_prioritize
