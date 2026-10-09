from __future__ import annotations

import logging
from types import SimpleNamespace

import crypto_execution_guard as guard
import oracle_bot
import robinhood_quote_resilience as resilience
from market_data import MarketSnapshot


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


def test_paper_grace_quote_blocks_new_crypto_entry(monkeypatch):
    monkeypatch.setenv("EXECUTION_MODE", "paper")
    monkeypatch.setattr(oracle_bot, "_verified_quote_for", _verified)

    worker, captured = _worker()
    guard.install_crypto_execution_quote_guard(worker)
    result = worker.process_signals(
        "crypto",
        [{"symbol": "BTC-USD", "action": "BUY"}],
        {
            "BTC-USD": {
                "symbol": "BTC-USD",
                "price": 100.0,
                "provider": "Robinhood Crypto",
                "quote_verified": True,
                "provider_quote_verified": True,
                "verification_basis": "paper_grace:provider:robinhood_crypto_best_bid_ask_read_time",
            }
        },
    )

    assert captured == []
    assert result == []


def test_paper_grace_quote_still_allows_protective_sell_path(monkeypatch):
    monkeypatch.setenv("EXECUTION_MODE", "paper")
    monkeypatch.setattr(oracle_bot, "_verified_quote_for", _verified)

    worker, captured = _worker()
    guard.install_crypto_execution_quote_guard(worker)
    signal = {"symbol": "BTC-USD", "action": "SELL"}
    result = worker.process_signals(
        "crypto",
        [signal],
        {
            "BTC-USD": {
                "symbol": "BTC-USD",
                "price": 100.0,
                "provider": "Robinhood Crypto",
                "quote_verified": True,
                "provider_quote_verified": True,
                "verification_basis": "paper_grace:provider:robinhood_crypto_best_bid_ask_read_time",
            }
        },
    )

    assert captured == [signal]
    assert result == [{"symbol": "BTC-USD"}]


def test_paper_grace_marker_preserves_verified_snapshot_and_marks_provenance():
    original = MarketSnapshot(
        symbol="BTC-USD",
        price=100.0,
        change_pct=0.0,
        volume=0.0,
        timestamp="2026-10-02T13:00:00+00:00",
        bid=99.9,
        ask=100.1,
        provider="Robinhood Crypto",
        interval="1m",
        fetched_at="2026-10-02T13:00:00+00:00",
        requested_symbol="BTC-USD",
        provider_symbol="BTC-USD",
        provider_native_symbol="BTC-USD",
        quote_verified=True,
        stale=False,
        provider_quote_verified=True,
        verification_basis="provider:robinhood_crypto_best_bid_ask_read_time",
    )

    marked = resilience._paper_grace_snapshot(original)

    assert marked is not original
    assert marked.quote_verified is True
    assert marked.provider_quote_verified is True
    assert marked.verification_basis == (
        "paper_grace:provider:robinhood_crypto_best_bid_ask_read_time"
    )
    assert original.verification_basis == "provider:robinhood_crypto_best_bid_ask_read_time"


def _paper_estimate_fixture(monkeypatch, *, quote_overrides=None, records_override=None):
    from datetime import datetime, timezone
    from types import SimpleNamespace
    import threading

    monkeypatch.setenv("EXECUTION_MODE", "paper")
    monkeypatch.setenv("ENABLE_BROKER_SUBMISSION", "false")
    monkeypatch.setenv("LIVE_TRADING_ARMED", "false")
    estimate_calls = []
    retry_calls = []
    quote = {
        "symbol": "BTC-USD",
        "side": "both",
        "quantity": "0.001",
        "bid": "100.20",
        "ask": "100.40",
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    quote.update(quote_overrides or {})

    def estimate(symbol, side, quantity):
        estimate_calls.append((symbol, side, quantity))
        return records_override if records_override is not None else [quote]

    def best_bid_ask(*symbols):
        retry_calls.append(symbols)
        return [{"symbol": "BTC-USD", "bid": "101.00", "ask": "100.00"}]

    provider = SimpleNamespace(
        client=SimpleNamespace(estimated_price=estimate, best_bid_ask_quotes=best_bid_ask),
        snapshots=lambda symbols: {},
        tradable_symbols=lambda: {"BTC-USD", "ETH-USD"},
        _cache={},
        _lock=threading.Lock(),
    )
    worker = SimpleNamespace(
        _robinhood_current_marketdata_provider=provider,
        _held_symbols=lambda market: set(),
        log=logging.getLogger("test-robinhood-paper-estimate"),
    )
    return worker, provider, estimate_calls, retry_calls


def test_crossed_broker_book_recovers_as_paper_only_sized_estimate(monkeypatch):
    worker, provider, estimates, retries = _paper_estimate_fixture(monkeypatch)
    assert resilience.install_robinhood_quote_resilience(worker)
    result = provider.snapshots(["BTC-USD"])
    snapshot = result["BTC-USD"]

    assert len(retries) == 3  # All actual BBO retries still reject the crossed book.
    assert estimates == [("BTC-USD", "both", "0.001")]
    assert snapshot.price == 100.3
    assert snapshot.bid == 100.2 and snapshot.ask == 100.4
    assert snapshot.verification_basis.startswith("paper_estimate:")
    assert snapshot.source_capability == "v2_estimated_price_paper_reference"
    assert snapshot.paper_reference_verified is True
    assert snapshot.provider_quote_verified is False
    assert guard._paper_grace_quote({"verification_basis": snapshot.verification_basis})
    from robinhood_current_marketdata_runtime import overlay_execution_payload

    enriched = overlay_execution_payload(
        {"symbol": "BTC-USD", "avg_dollar_volume": 1_000_000}, snapshot
    )
    assert enriched["tradeable"] is False
    assert enriched["provider_quote_verified"] is False
    assert enriched["paper_reference_verified"] is True
    assert enriched["current_data_verified"] is False
    assert enriched["verified"] is False
    assert enriched["source_mode"] == "broker_paper_estimate_reference"


def test_crossed_book_fallback_is_completely_disabled_outside_disarmed_paper(monkeypatch):
    worker, provider, estimates, retries = _paper_estimate_fixture(monkeypatch)
    monkeypatch.setenv("LIVE_TRADING_ARMED", "true")
    assert resilience.install_robinhood_quote_resilience(worker)
    assert provider.snapshots(["BTC-USD"]) == {}
    assert len(retries) == 3
    assert estimates == []


def test_estimated_quote_must_be_atomic_fresh_size_matched_and_uncrossed(monkeypatch):
    from datetime import datetime, timedelta, timezone

    now = datetime.now(timezone.utc)
    cases = [
        {"symbol": "ETH-USD"},
        {"side": "wrong"},
        {"quantity": "0.01"},
        {"timestamp": (now - timedelta(seconds=45)).isoformat()},
        {"timestamp": (now + timedelta(seconds=30)).isoformat()},
        {"timestamp": "not-a-timestamp"},
        {"bid": "101", "ask": "100"},
        {"bid": "99", "ask": "103"},  # spread + price divergence
        {"bid": "100.2", "ask": ""},
    ]
    crossed = {"bid": "101", "ask": "100"}
    for changes in cases:
        worker, provider, estimates, _ = _paper_estimate_fixture(
            monkeypatch, quote_overrides=changes
        )
        assert resilience._paper_estimated_snapshot(provider, "BTC-USD", crossed, worker) is None

    worker, provider, _, _ = _paper_estimate_fixture(monkeypatch, records_override=[])
    assert resilience._paper_estimated_snapshot(provider, "BTC-USD", crossed, worker) is None
    worker, provider, _, _ = _paper_estimate_fixture(monkeypatch, records_override=[
        {"symbol": "BTC-USD"}, {"symbol": "BTC-USD"}
    ])
    assert resilience._paper_estimated_snapshot(provider, "BTC-USD", crossed, worker) is None


def test_paper_estimated_price_cannot_trigger_new_buy(monkeypatch):
    monkeypatch.setenv("EXECUTION_MODE", "paper")
    monkeypatch.setattr(oracle_bot, "_verified_quote_for", _verified)
    worker, captured = _worker()
    guard.install_crypto_execution_quote_guard(worker)
    result = worker.process_signals(
        "crypto",
        [{"symbol": "BTC-USD", "action": "BUY"}],
        {"BTC-USD": {
            "symbol": "BTC-USD",
            "price": 100.3,
            "provider": "Robinhood Crypto",
            "quote_verified": True,
            "verification_basis": "paper_estimate:robinhood_crypto_v2_size_specific",
        }},
    )
    assert result == []
    assert captured == []


def test_paper_estimate_is_counted_as_quality_degradation(monkeypatch):
    from crypto_provider_health_runtime import install_crypto_provider_health_runtime

    worker, provider, _, _ = _paper_estimate_fixture(monkeypatch)
    assert resilience.install_robinhood_quote_resilience(worker)
    assert install_crypto_provider_health_runtime(worker)
    result = provider.snapshots(["BTC-USD"])
    assert result["BTC-USD"].verification_basis.startswith("paper_estimate:")
    health = worker._crypto_provider_health
    assert health["last_resolved"] == 1
    assert health["last_paper_estimate_only"] == ["BTC-USD"]
    assert health["data_quality_score"] == 0.0


def test_eth_paper_estimated_price_provides_independent_bid_ask(monkeypatch):
    from datetime import datetime, timezone

    worker, provider, _, _ = _paper_estimate_fixture(monkeypatch)
    quote = {
        "symbol": "ETH-USD",
        "side": "both",
        "quantity": "0.01",
        "bid": "1999.8",
        "ask": "2000.2",
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    provider.client.estimated_price = lambda symbol, side, quantity: [quote]
    snapshot = resilience._paper_estimated_snapshot(
        provider, "ETH-USD", {"bid": "2001", "ask": "1999"}, worker,
    )
    assert snapshot is not None
    assert snapshot.price == 2000.0
    assert snapshot.verification_basis.startswith("paper_estimate:")


def test_cached_paper_estimate_cannot_be_reused_in_live_mode(monkeypatch):
    worker, provider, _, _ = _paper_estimate_fixture(monkeypatch)
    snapshot = resilience._paper_estimated_snapshot(
        provider, "BTC-USD", {"bid": "101", "ask": "100"}, worker,
    )
    assert snapshot is not None
    provider.snapshots = lambda symbols: {"BTC-USD": snapshot}
    monkeypatch.setenv("EXECUTION_MODE", "live")
    assert resilience.install_robinhood_quote_resilience(worker)
    assert provider.snapshots(["BTC-USD"]) == {}

def test_crossed_estimated_price_logs_rejection_shape_without_raw_quotes(monkeypatch, caplog):
    worker, provider, _, _ = _paper_estimate_fixture(
        monkeypatch, records_override=[{
            "symbol": "ETH-USD", "side": "both", "quantity": "0.001",
            "bid": "SENSITIVE_BID", "ask": "SENSITIVE_ASK",
        }],
    )
    with caplog.at_level(logging.INFO, logger="test-robinhood-paper-estimate"):
        result = resilience._paper_estimated_snapshot(
            provider, "BTC-USD", {"bid": "101", "ask": "100"}, worker,
        )
    assert result is None
    assert "ROBINHOOD PAPER ESTIMATE SHAPE" in caplog.text
    assert "match=False" in caplog.text
    assert "has_ts=False" in caplog.text
    assert "SENSITIVE_BID" not in caplog.text
    assert "SENSITIVE_ASK" not in caplog.text
