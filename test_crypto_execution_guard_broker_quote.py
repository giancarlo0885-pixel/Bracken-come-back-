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


def test_paper_mode_keeps_primary_verified_quote_behavior_without_broker_call(monkeypatch):
    monkeypatch.setenv("EXECUTION_MODE", "paper")
    monkeypatch.setattr(oracle_bot, "_verified_quote_for", _verified)
    monkeypatch.setattr(guard, "_broker_quote_map", lambda symbols: (_ for _ in ()).throw(AssertionError("paper mode must not call Robinhood")))
    monkeypatch.setattr(guard, "_coinbase_reference_validation", lambda symbol, price: (_ for _ in ()).throw(AssertionError("provider-verified quote must not require Coinbase")))

    worker, captured = _worker()
    guard.install_crypto_execution_quote_guard(worker)
    result = worker.process_signals(
        "crypto",
        [{"symbol": "BTC-USD"}],
        {
            "BTC-USD": {
                "symbol": "BTC-USD",
                "price": 100.0,
                "provider": "Polygon",
                "quote_verified": True,
                "provider_quote_verified": True,
            }
        },
    )

    assert captured == [{"symbol": "BTC-USD"}]
    assert result == [{"symbol": "BTC-USD"}]


def test_yahoo_paper_quote_requires_independent_coinbase_consensus(monkeypatch):
    monkeypatch.setenv("EXECUTION_MODE", "paper")
    monkeypatch.setattr(oracle_bot, "_verified_quote_for", _verified)
    monkeypatch.setattr(
        guard,
        "_coinbase_reference_validation",
        lambda symbol, price: {
            "ok": True,
            "reason": "COINBASE_REFERENCE_CONFIRMED",
            "reference_provider": "Coinbase Exchange",
            "reference_price": 100.02,
            "reference_timestamp": "2026-08-30T16:55:00+00:00",
            "spread_pct": 0.10,
            "difference_pct": 0.02,
        },
    )

    prices = {
        "BTC-USD": {
            "symbol": "BTC-USD",
            "price": 100.0,
            "provider": "Yahoo Finance",
            "quote_verified": True,
            "paper_reference_verified": True,
            "verification_basis": "paper:fresh_identity_matched_yahoo",
        }
    }
    worker, captured = _worker()
    guard.install_crypto_execution_quote_guard(worker)
    result = worker.process_signals("crypto", [{"symbol": "BTC-USD"}], prices)

    assert captured == [{"symbol": "BTC-USD"}]
    assert result == [{"symbol": "BTC-USD"}]


def test_yahoo_paper_quote_is_blocked_when_coinbase_disagrees(monkeypatch):
    monkeypatch.setenv("EXECUTION_MODE", "paper")
    monkeypatch.setattr(oracle_bot, "_verified_quote_for", _verified)
    monkeypatch.setattr(
        guard,
        "_coinbase_reference_validation",
        lambda symbol, price: {"ok": False, "reason": "COINBASE_PRICE_DIVERGENCE"},
    )

    worker, captured = _worker()
    guard.install_crypto_execution_quote_guard(worker)
    result = worker.process_signals(
        "crypto",
        [{"symbol": "BTC-USD"}],
        {
            "BTC-USD": {
                "symbol": "BTC-USD",
                "price": 100.0,
                "provider": "Yahoo Finance",
                "quote_verified": True,
                "paper_reference_verified": True,
                "verification_basis": "paper:fresh_identity_matched_yahoo",
            }
        },
    )

    assert captured == []
    assert result == []


def test_coinbase_reference_validation_checks_freshness_spread_and_price(monkeypatch):
    monkeypatch.setenv("COINBASE_REFERENCE_MAX_DIFF_PCT", "1.00")
    monkeypatch.setenv("COINBASE_REFERENCE_MAX_SPREAD_PCT", "1.50")
    monkeypatch.setenv("COINBASE_REFERENCE_MAX_AGE_SECONDS", "300")
    monkeypatch.setattr(
        guard,
        "_coinbase_quote",
        lambda symbol: (
            {
                "symbol": symbol,
                "bid": "99.90",
                "ask": "100.10",
                "price": "100.00",
                "timestamp": guard.datetime.now(guard.timezone.utc).isoformat(),
                "provider": "Coinbase Exchange",
            },
            None,
        ),
    )

    good = guard._coinbase_reference_validation("BTC-USD", 100.05)
    assert good["ok"] is True
    assert good["reference_provider"] == "Coinbase Exchange"

    divergent = guard._coinbase_reference_validation("BTC-USD", 103.0)
    assert divergent["ok"] is False
    assert divergent["reason"] == "COINBASE_PRICE_DIVERGENCE"


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
        {
            "BTC-USD": {
                "symbol": "BTC-USD",
                "price": 100.05,
                "provider": "Polygon",
                "quote_verified": True,
                "provider_quote_verified": True,
            }
        },
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
        {
            "BTC-USD": {
                "symbol": "BTC-USD",
                "price": 100.0,
                "provider": "Polygon",
                "quote_verified": True,
                "provider_quote_verified": True,
            }
        },
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
        {
            "ETH-USD": {
                "symbol": "ETH-USD",
                "price": 2500.0,
                "provider": "Polygon",
                "quote_verified": True,
                "provider_quote_verified": True,
            }
        },
    )

    assert captured == []
    assert result == []
