from types import SimpleNamespace

import runtime_integrity_patch as patch


class _Log:
    def debug(self, *args, **kwargs):
        pass

    def info(self, *args, **kwargs):
        pass


def _finite_positive(value):
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if number > 0 else None


def _worker(*, cash=2000.0, equity=2000.0, allocate=True):
    worker = SimpleNamespace()
    worker.log = _Log()
    worker.HIGH_SCORE_THRESHOLD = 70.0
    worker.HIGH_CONFIDENCE_THRESHOLD = 0.78
    worker._finite_positive = _finite_positive
    worker._v39_position_rows = lambda market: (
        {"cash": cash, "equity": equity, "buying_power": cash},
        [],
    )

    def opportunity(market, signal, prices, ranked_by_symbol, scan_type):
        quote = prices.get(signal.symbol, {})
        action = str(signal.action).upper()
        return {
            "symbol": signal.symbol,
            "asset_class": "crypto",
            "market": market,
            "qualified_for_capital": bool(
                action in {"BUY", "STRONG_BUY", "ACCUMULATE", "LONG"}
                and quote.get("quote_verified") is True
                and quote.get("identity_verified") is True
                and quote.get("fresh") is True
                and quote.get("tradeable") is True
                and float(quote.get("liquidity", 0) or 0) > 0
                and quote.get("spread_known") is True
                and quote.get("risk_known") is True
                and getattr(signal, "signal_id", None)
                and getattr(signal, "forecast_id", None)
            ),
            "stages": ["surveillance", "buy_signal"] if action == "ACCUMULATE" else ["surveillance"],
        }

    worker._v39_signal_opportunity = opportunity

    def prioritize(market, signals, prices, ranked, scan_type):
        ranked_by_symbol = {row["symbol"]: row for row in ranked}
        for signal in signals:
            candidate = worker._v39_signal_opportunity(market, signal, prices, ranked_by_symbol, scan_type)
            if allocate and candidate.get("qualified_for_capital") is True:
                signal.v39_optimizer_approved_amount = 200.0
                signal.v39_optimizer_allocation = {"symbol": signal.symbol, "amount": 200.0}
        return signals

    worker._v39_prioritize_signals = prioritize
    return worker


def _hold_signal():
    return SimpleNamespace(
        symbol="BTC-USD",
        action="HOLD",
        score=92.0,
        confidence=0.91,
        signal_id="sig-1",
        forecast_id="fc-1",
    )


def _verified_prices():
    return {
        "BTC-USD": {
            "quote_verified": True,
            "identity_verified": True,
            "fresh": True,
            "tradeable": True,
            "liquidity": 10_000_000.0,
            "spread_known": True,
            "risk_known": True,
        }
    }


def test_optimizer_allocation_emits_core_rebalance_buy_and_normalizes_hold():
    worker = _worker()
    patch._install_core_rebalance_producer(worker)
    signal = _hold_signal()

    ordered = worker._v39_prioritize_signals(
        "crypto",
        [signal],
        _verified_prices(),
        [{"symbol": "BTC-USD"}],
        "deep",
    )

    assert ordered == [signal]
    assert signal.rebalance_intent == patch.CORE_REBALANCE_BUY_INTENT
    assert signal.portfolio_intent == patch.CORE_REBALANCE_BUY_INTENT
    assert signal.v39_optimizer_approved_amount == 200.0
    assert signal.action == "ACCUMULATE"
    assert signal.v39_original_action == "HOLD"
    assert signal.v39_normalization_reason == patch.CORE_REBALANCE_BUY_INTENT


def test_failed_quote_or_risk_prerequisite_cannot_emit_core_rebalance_buy():
    worker = _worker()
    patch._install_core_rebalance_producer(worker)
    signal = _hold_signal()
    prices = _verified_prices()
    prices["BTC-USD"]["quote_verified"] = False

    worker._v39_prioritize_signals(
        "crypto",
        [signal],
        prices,
        [{"symbol": "BTC-USD"}],
        "deep",
    )

    assert signal.action == "HOLD"
    assert signal.portfolio_intent == patch.CORE_REBALANCE_CANDIDATE_INTENT
    assert getattr(signal, "rebalance_intent", None) is None
    assert getattr(signal, "v39_optimizer_approved_amount", None) is None


def test_optimizer_rejection_cannot_emit_core_rebalance_buy():
    worker = _worker(allocate=False)
    patch._install_core_rebalance_producer(worker)
    signal = _hold_signal()

    worker._v39_prioritize_signals(
        "crypto",
        [signal],
        _verified_prices(),
        [{"symbol": "BTC-USD"}],
        "deep",
    )

    assert signal.action == "HOLD"
    assert signal.portfolio_intent == patch.CORE_REBALANCE_CANDIDATE_INTENT
    assert getattr(signal, "rebalance_intent", None) is None


def test_fully_invested_portfolio_does_not_turn_hold_into_rebalance_candidate():
    worker = _worker(cash=0.0, equity=2000.0)
    patch._install_core_rebalance_producer(worker)
    signal = _hold_signal()

    worker._v39_prioritize_signals(
        "crypto",
        [signal],
        _verified_prices(),
        [{"symbol": "BTC-USD"}],
        "deep",
    )

    assert signal.action == "HOLD"
    assert getattr(signal, "portfolio_intent", None) is None
    assert getattr(signal, "rebalance_intent", None) is None


def test_low_quality_hold_is_not_promoted_just_because_portfolio_is_empty():
    worker = _worker()
    patch._install_core_rebalance_producer(worker)
    signal = _hold_signal()
    signal.score = 55.0

    worker._v39_prioritize_signals(
        "crypto",
        [signal],
        _verified_prices(),
        [{"symbol": "BTC-USD"}],
        "deep",
    )

    assert signal.action == "HOLD"
    assert getattr(signal, "portfolio_intent", None) is None
    assert getattr(signal, "rebalance_intent", None) is None
