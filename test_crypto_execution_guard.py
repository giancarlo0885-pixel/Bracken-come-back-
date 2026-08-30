from __future__ import annotations

from types import SimpleNamespace

import crypto_execution_guard as guard
import oracle_bot


class FakeWorker:
    def __init__(self):
        self.log = SimpleNamespace(info=lambda *args, **kwargs: None)
        self.processed = None

        def original_process(market, signals, prices=None, *args, **kwargs):
            self.processed = list(signals or [])
            return [{"symbol": getattr(item, "symbol", "")} for item in self.processed]

        self.process_signals = original_process


def test_unverified_crypto_quote_is_filtered_before_zero_candidate(monkeypatch):
    monkeypatch.setattr(
        oracle_bot,
        "_verified_quote_for",
        lambda symbol, prices, market="crypto": None,
    )
    worker = FakeWorker()
    guard.install_crypto_execution_quote_guard(worker)

    signal = SimpleNamespace(symbol="FET-USD")
    result = worker.process_signals(
        "crypto",
        [signal],
        {
            "FET-USD": {
                "symbol": "FET-USD",
                "requested_symbol": "FET-USD",
                "provider_symbol": "FET-USD",
                "price": 0.0,
                "quote_verified": False,
            }
        },
    )

    assert result == []
    assert worker.processed == []


def test_verified_crypto_quote_still_reaches_oracle(monkeypatch):
    verified = {
        "symbol": "ETH-USD",
        "requested_symbol": "ETH-USD",
        "provider_symbol": "ETH-USD",
        "price": 2484.23,
        "quote_verified": True,
        "stale": False,
    }
    monkeypatch.setattr(
        oracle_bot,
        "_verified_quote_for",
        lambda symbol, prices, market="crypto": verified if symbol == "ETH-USD" else None,
    )
    worker = FakeWorker()
    guard.install_crypto_execution_quote_guard(worker)

    signal = SimpleNamespace(symbol="ETH-USD")
    result = worker.process_signals("crypto", [signal], {"ETH-USD": verified})

    assert result == [{"symbol": "ETH-USD"}]
    assert worker.processed == [signal]


def test_stock_path_is_unchanged(monkeypatch):
    called = {"verified": False}

    def should_not_run(*args, **kwargs):
        called["verified"] = True
        return None

    monkeypatch.setattr(oracle_bot, "_verified_quote_for", should_not_run)
    worker = FakeWorker()
    guard.install_crypto_execution_quote_guard(worker)

    signal = SimpleNamespace(symbol="MSFT")
    result = worker.process_signals("cash", [signal], {})

    assert result == [{"symbol": "MSFT"}]
    assert called["verified"] is False
