from __future__ import annotations

from contextlib import contextmanager
import json
from types import SimpleNamespace

import crypto_execution_guard as guard
import oracle_bot


class FakeConnection:
    def __init__(self):
        self.calls: list[tuple[str, tuple]] = []

    def execute(self, query, params):
        self.calls.append((query, params))
        return None


class FakeWorker:
    def __init__(self):
        self.log = SimpleNamespace(
            info=lambda *args, **kwargs: None,
            warning=lambda *args, **kwargs: None,
        )
        self.processed = None

        def original_process(market, signals, prices=None, *args, **kwargs):
            self.processed = list(signals or [])
            return [{"symbol": getattr(item, "symbol", "")} for item in self.processed]

        self.process_signals = original_process


def _yahoo_quote(symbol: str, price: float) -> dict:
    return {
        "symbol": symbol,
        "requested_symbol": symbol,
        "provider_symbol": symbol,
        "provider": "Yahoo Finance",
        "price": price,
        "quote_verified": True,
        "execution_quote_eligible": True,
        "quote_timestamp": "2026-09-01T03:10:00+00:00",
        "stale": False,
    }


def test_persistence_writes_verified_and_rejected_consensus(monkeypatch):
    connection = FakeConnection()

    @contextmanager
    def fake_connect():
        yield connection

    monkeypatch.setattr(guard, "connect", fake_connect)
    monkeypatch.setattr(guard, "utc_now", lambda: "2026-09-01T03:20:00+00:00")
    monkeypatch.setattr(guard.time, "monotonic", lambda: 1000.0)
    guard._QUOTE_VERIFICATION_CACHE.clear()

    verified = guard._quote_verification_record(
        "BTC-USD",
        _yahoo_quote("BTC-USD", 100.0),
        {
            "ok": True,
            "reason": "COINBASE_REFERENCE_CONFIRMED",
            "reference_provider": "Coinbase Exchange",
            "reference_price": 100.1,
            "reference_timestamp": "2026-09-01T03:10:01+00:00",
            "difference_pct": 0.0999,
            "spread_pct": 0.02,
        },
    )
    rejected = guard._quote_verification_record(
        "ETH-USD",
        _yahoo_quote("ETH-USD", 100.0),
        {
            "ok": False,
            "reason": "COINBASE_PRICE_DIVERGENCE",
            "reference_provider": "Coinbase Exchange",
            "reference_price": 103.0,
            "reference_timestamp": "2026-09-01T03:10:01+00:00",
            "difference_pct": 2.9126,
            "spread_pct": 0.03,
        },
    )

    assert guard._persist_quote_verifications([verified, rejected]) == 2
    assert len(connection.calls) == 2

    first_params = connection.calls[0][1]
    second_params = connection.calls[1][1]
    assert first_params[0] == "BTC-USD"
    assert first_params[7] == "verified"
    assert second_params[0] == "ETH-USD"
    assert second_params[7] == "rejected"
    assert second_params[6] == 2.9126
    assert json.loads(second_params[8])["reason"] == "COINBASE_PRICE_DIVERGENCE"

    # The worker can see the same provider timestamps across several pulses.
    # The evidence cache must prevent duplicate rows for the same result.
    assert guard._persist_quote_verifications([verified, rejected]) == 0
    assert len(connection.calls) == 2


def test_active_crypto_guard_persists_confirmed_consensus(monkeypatch):
    monkeypatch.setenv("EXECUTION_MODE", "paper")
    quote = _yahoo_quote("BTC-USD", 100.0)
    monkeypatch.setattr(
        oracle_bot,
        "_verified_quote_for",
        lambda symbol, prices, market="crypto": quote if symbol == "BTC-USD" else None,
    )
    monkeypatch.setattr(
        guard,
        "_coinbase_reference_validation",
        lambda symbol, price: {
            "ok": True,
            "reason": "COINBASE_REFERENCE_CONFIRMED",
            "reference_provider": "Coinbase Exchange",
            "reference_price": 100.05,
            "reference_timestamp": "2026-09-01T03:10:01+00:00",
            "difference_pct": 0.05,
            "spread_pct": 0.02,
        },
    )
    captured: list[dict] = []

    def capture(records):
        captured.extend(records)
        return len(records)

    monkeypatch.setattr(guard, "_persist_quote_verifications", capture)
    worker = FakeWorker()
    guard.install_crypto_execution_quote_guard(worker)
    signal = SimpleNamespace(symbol="BTC-USD")

    result = worker.process_signals("crypto", [signal], {"BTC-USD": quote})

    assert result == [{"symbol": "BTC-USD"}]
    assert worker.processed == [signal]
    assert len(captured) == 1
    assert captured[0]["symbol"] == "BTC-USD"
    assert captured[0]["consensus_status"] == "verified"
    assert captured[0]["secondary_provider"] == "Coinbase Exchange"


def test_active_crypto_guard_persists_divergence_before_blocking(monkeypatch):
    monkeypatch.setenv("EXECUTION_MODE", "paper")
    quote = _yahoo_quote("ETH-USD", 100.0)
    monkeypatch.setattr(
        oracle_bot,
        "_verified_quote_for",
        lambda symbol, prices, market="crypto": quote if symbol == "ETH-USD" else None,
    )
    monkeypatch.setattr(
        guard,
        "_coinbase_reference_validation",
        lambda symbol, price: {
            "ok": False,
            "reason": "COINBASE_PRICE_DIVERGENCE",
            "reference_provider": "Coinbase Exchange",
            "reference_price": 103.0,
            "reference_timestamp": "2026-09-01T03:10:01+00:00",
            "difference_pct": 2.9126,
            "spread_pct": 0.03,
        },
    )
    captured: list[dict] = []

    def capture(records):
        captured.extend(records)
        return len(records)

    monkeypatch.setattr(guard, "_persist_quote_verifications", capture)
    worker = FakeWorker()
    guard.install_crypto_execution_quote_guard(worker)
    signal = SimpleNamespace(symbol="ETH-USD")

    result = worker.process_signals("crypto", [signal], {"ETH-USD": quote})

    assert result == []
    assert worker.processed == []
    assert len(captured) == 1
    assert captured[0]["symbol"] == "ETH-USD"
    assert captured[0]["consensus_status"] == "rejected"
    assert captured[0]["difference_pct"] == 2.9126
