from __future__ import annotations

import logging
from types import SimpleNamespace

import crypto_execution_guard as guard
import oracle_bot


def _worker():
    captured = []

    def process_signals(market, signals, prices=None, *args, **kwargs):
        captured.extend(list(signals or []))
        return [{"symbol": item.get("symbol")} for item in list(signals or [])]

    return SimpleNamespace(process_signals=process_signals, log=logging.getLogger("test-worker")), captured


def _verified(symbol, prices, market):
    return dict((prices or {}).get(symbol) or {}) or None


def test_paper_mode_keeps_verified_quote_behavior_without_broker_call(monkeypatch):
    monkeypatch.setenv("EXECUTION_MODE", "paper")
    monkeypatch.setattr(oracle_bot, "_verified_quote_for", _verified)
    monkeypatch.setattr(guard, "_broker_quote_map", lambda symbols: (_ for _ in ()).throw(AssertionError("paper mode must not call Robinhood")))

    worker, captured = _worker()
    guard.install_crypto_execution_quote_guard(worker)
    result = worker.process_signals(
        "crypto",
        [{"symbol": "BTC-USD"}],
        {"BTC-USD": {"symbol": "BTC-USD", "price": 100.0, "quote_verified": True}},
    )

    assert captured == [{"symbol": "BTC-USD"}]
    assert result == [{"symbol": "BTC-USD"}]


def test_live_mode_requires_robinhood_price_agreement(monkeypatch):
    monkeypatch.setenv("EXECUTION_MODE", "live")
    monkeypatch.setenv("ROBINHOOD_BROKER_PRICE_TOLERANCE_PCT", "0.75")
    monkeypatch.setenv("ROBINHOOD_BROKER_MAX_SPREAD_PCT", "1.50")
    monkeypatch.setattr(oracle_bot, "_verified_quote_for", _verified)
    monkeypatch.setattr(
        guard,
        "_broker_quote_map",
        lambda symbols: ({"BTC-USD": {"symbol": "BTC-USD", "bid": "99.90", "ask": "100.10"}}, None),
    )

    worker, captured = _worker()
    guard.install_crypto_execution_quote_guard(worker)
    worker.process_signals(
        "crypto",
        [{"symbol": "BTC-USD"}],
        {"BTC-USD": {"symbol": "BTC-USD", "price": 100.05, "quote_verified": True}},
    )

    assert captured == [{"symbol": "BTC-USD"}]


def test_live_mode_blocks_divergent_broker_quote(monkeypatch):
    monkeypatch.setenv("EXECUTION_MODE", "live")
    monkeypatch.setenv("ROBINHOOD_BROKER_PRICE_TOLERANCE_PCT", "0.50")
    monkeypatch.setattr(oracle_bot, "_verified_quote_for", _verified)
    monkeypatch.setattr(
        guard,
        "_broker_quote_map",
        lambda symbols: ({"BTC-USD": {"symbol": "BTC-USD", "bid": "89.90", "ask": "90.10"}}, None),
    )

    worker, captured = _worker()
    guard.install_crypto_execution_quote_guard(worker)
    result = worker.process_signals(
        "crypto",
        [{"symbol": "BTC-USD"}],
        {"BTC-USD": {"symbol": "BTC-USD", "price": 100.0, "quote_verified": True}},
    )

    assert captured == []
    assert result == []


def test_live_mode_fails_closed_when_robinhood_market_data_unavailable(monkeypatch):
    monkeypatch.setenv("EXECUTION_MODE", "live")
    monkeypatch.setattr(oracle_bot, "_verified_quote_for", _verified)
    monkeypatch.setattr(guard, "_broker_quote_map", lambda symbols: ({}, "ROBINHOOD_CRYPTO_API_KEY missing"))

    worker, captured = _worker()
    guard.install_crypto_execution_quote_guard(worker)
    result = worker.process_signals(
        "crypto",
        [{"symbol": "ETH-USD"}],
        {"ETH-USD": {"symbol": "ETH-USD", "price": 2500.0, "quote_verified": True}},
    )

    assert captured == []
    assert result == []
