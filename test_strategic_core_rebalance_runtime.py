from types import SimpleNamespace

import strategic_core_rebalance_runtime as runtime
import runtime_integrity_patch as patch


class _Log:
    def info(self, *args, **kwargs):
        pass


def test_configured_core_gap_tags_only_matching_hold(monkeypatch):
    btc = SimpleNamespace(symbol="BTC-USD", action="HOLD", score=0.40, confidence=0.40)
    eth = SimpleNamespace(symbol="ETH-USD", action="SELL", score=0.90, confidence=0.90)
    worker = SimpleNamespace()
    worker.log = _Log()
    worker._v39_position_rows = lambda market: ({"cash": 2000.0, "equity": 2000.0}, [])
    worker._v39_prioritize_signals = lambda market, signals, prices, ranked, scan_type: signals
    monkeypatch.setattr(
        runtime,
        "crypto_core_rebalance_plan",
        lambda prices, portfolio, positions: [
            {
                "Asset": "BTC-USD",
                "Bucket": "Core",
                "Target Weight": "40%",
                "Current Core Value": 0.0,
                "Amount": 240.0,
                "Reason": "Underweight verified crypto core holding above protected reserve.",
            },
            {
                "Asset": "ETH-USD",
                "Bucket": "Core",
                "Target Weight": "25%",
                "Current Core Value": 0.0,
                "Amount": 150.0,
                "Reason": "Underweight verified crypto core holding above protected reserve.",
            },
        ],
    )

    runtime.install_strategic_core_rebalance_producer(worker)
    result = worker._v39_prioritize_signals(
        "crypto",
        [btc, eth],
        {"BTC-USD": {"price": 100.0}, "ETH-USD": {"price": 10.0}},
        [],
        "fast",
    )

    assert result == [btc, eth]
    assert btc.portfolio_intent == patch.CORE_REBALANCE_CANDIDATE_INTENT
    assert btc.core_rebalance_source == "configured_core_allocation_gap"
    assert btc.core_target_amount == 240.0
    assert btc.action == "HOLD"
    assert not hasattr(eth, "portfolio_intent")
    assert eth.action == "SELL"


def test_no_core_plan_does_not_promote_tactical_hold(monkeypatch):
    signal = SimpleNamespace(symbol="BTC-USD", action="HOLD", score=0.99, confidence=0.99)
    worker = SimpleNamespace()
    worker.log = _Log()
    worker._v39_position_rows = lambda market: ({"cash": 2000.0, "equity": 2000.0}, [])
    worker._v39_prioritize_signals = lambda market, signals, prices, ranked, scan_type: signals
    monkeypatch.setattr(runtime, "crypto_core_rebalance_plan", lambda prices, portfolio, positions: [])

    runtime.install_strategic_core_rebalance_producer(worker)
    worker._v39_prioritize_signals("crypto", [signal], {"BTC-USD": {"price": 100.0}}, [], "fast")

    assert not hasattr(signal, "portfolio_intent")
    assert signal.action == "HOLD"
