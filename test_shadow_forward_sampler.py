from __future__ import annotations

from types import SimpleNamespace

import shadow_forward_sampler as sampler


class FakeLog:
    def info(self, *args, **kwargs):
        return None


class FakeWorker:
    log = FakeLog()


class FakeClient:
    def __init__(self):
        self.calls = []

    def best_bid_ask_quotes(self, *symbols):
        self.calls.append(symbols)
        return [
            {
                "symbol": symbol,
                "bid_price": "99.90",
                "ask_price": "100.10",
                "bid": "99.90",
                "ask": "100.10",
                "timestamp": "2026-09-01T15:00:00+00:00",
            }
            for symbol in symbols
        ]


def _quote(symbol: str = "BTC-USD") -> dict:
    return {
        "symbol": symbol,
        "provider": "Yahoo Finance",
        "price": 100.0,
        "quote_timestamp": "2026-09-01T15:00:00+00:00",
        "quote_verified": True,
        "execution_quote_eligible": True,
        "paper_reference_verified": True,
        "stale": False,
    }


def _install_valid_reference_stubs(monkeypatch) -> None:
    sampler._SEEN.clear()
    monkeypatch.setattr(sampler, "_already_recorded", lambda *args: False)
    monkeypatch.setattr(
        sampler.oracle_bot,
        "_verified_quote_for",
        lambda symbol, prices, market: dict(prices[symbol]),
    )
    monkeypatch.setattr(sampler, "_paper_yahoo_reference", lambda quote: True)
    monkeypatch.setattr(
        sampler,
        "_coinbase_reference_validation",
        lambda symbol, price: {
            "ok": True,
            "reference_provider": "Coinbase Exchange",
            "reference_timestamp": "2026-09-01T15:00:01+00:00",
            "reference_price": 100.0,
            "difference_pct": 0.0,
        },
    )


def test_passive_shadow_sampler_records_buy_and_sell_without_broker_submission(monkeypatch) -> None:
    monkeypatch.setenv("EXECUTION_MODE", "paper")
    monkeypatch.setenv("ROBINHOOD_CRYPTO_ENABLED", "true")
    _install_valid_reference_stubs(monkeypatch)
    recorded = []
    monkeypatch.setattr(sampler, "record_shadow_order", lambda **kwargs: recorded.append(kwargs))

    client = FakeClient()
    result = sampler.capture_passive_shadow_samples(
        FakeWorker(),
        [SimpleNamespace(symbol="BTC-USD", action="HOLD")],
        {"BTC-USD": _quote()},
        client=client,
    )
    assert result["captured"] == 2
    assert {item["side"] for item in recorded} == {"BUY", "SELL"}
    assert all(item["paper_fill_id"] is None if "paper_fill_id" in item else True for item in recorded)
    assert all(item["payload"]["evidence_kind"] == "passive_paper_execution_model" for item in recorded)
    assert client.calls == [("BTC-USD",)]


def test_passive_shadow_filters_symbols_not_tradable_on_robinhood(monkeypatch) -> None:
    monkeypatch.setenv("EXECUTION_MODE", "paper")
    monkeypatch.setenv("ROBINHOOD_CRYPTO_ENABLED", "true")
    _install_valid_reference_stubs(monkeypatch)
    recorded = []
    monkeypatch.setattr(sampler, "record_shadow_order", lambda **kwargs: recorded.append(kwargs))

    class TradableClient(FakeClient):
        def trading_pairs(self):
            return [
                {"symbol": "BTC-USD", "tradable": True},
                {"symbol": "ETH-USD", "tradable": False},
            ]

    client = TradableClient()
    result = sampler.capture_passive_shadow_samples(
        FakeWorker(),
        [
            SimpleNamespace(symbol="BTC-USD", action="HOLD"),
            SimpleNamespace(symbol="ETH-USD", action="HOLD"),
        ],
        {"BTC-USD": _quote("BTC-USD"), "ETH-USD": _quote("ETH-USD")},
        client=client,
    )

    assert result["captured"] == 2
    assert result["skipped"] == 2
    assert client.calls == [("BTC-USD",)]
    assert {item["symbol"] for item in recorded} == {"BTC-USD"}


def test_pair_discovery_failure_is_fail_closed_without_broker_quote_request(monkeypatch) -> None:
    monkeypatch.setenv("EXECUTION_MODE", "paper")
    monkeypatch.setenv("ROBINHOOD_CRYPTO_ENABLED", "true")
    _install_valid_reference_stubs(monkeypatch)

    class BrokenPairClient(FakeClient):
        def trading_pairs(self):
            raise RuntimeError("pair discovery unavailable")

    client = BrokenPairClient()
    result = sampler.capture_passive_shadow_samples(
        FakeWorker(),
        [SimpleNamespace(symbol="BTC-USD", action="HOLD")],
        {"BTC-USD": _quote()},
        client=client,
    )

    assert result["status"] == "BROKER_PAIR_DISCOVERY_UNAVAILABLE"
    assert result["captured"] == 0
    assert client.calls == []


def test_divergent_reference_is_not_shadow_sampled(monkeypatch) -> None:
    monkeypatch.setenv("EXECUTION_MODE", "paper")
    monkeypatch.setenv("ROBINHOOD_CRYPTO_ENABLED", "true")
    sampler._SEEN.clear()
    monkeypatch.setattr(sampler, "_already_recorded", lambda *args: False)
    monkeypatch.setattr(
        sampler.oracle_bot,
        "_verified_quote_for",
        lambda symbol, prices, market: dict(prices[symbol]),
    )
    monkeypatch.setattr(sampler, "_paper_yahoo_reference", lambda quote: True)
    monkeypatch.setattr(
        sampler,
        "_coinbase_reference_validation",
        lambda symbol, price: {"ok": False, "reason": "COINBASE_PRICE_DIVERGENCE"},
    )
    monkeypatch.setattr(
        sampler,
        "record_shadow_order",
        lambda **kwargs: (_ for _ in ()).throw(AssertionError("must not record rejected consensus")),
    )
    result = sampler.capture_passive_shadow_samples(
        FakeWorker(),
        [SimpleNamespace(symbol="BTC-USD", action="HOLD")],
        {"BTC-USD": _quote()},
        client=FakeClient(),
    )
    assert result["captured"] == 0
    assert result["status"] == "NO_NEW_REFERENCE_BARS"


def test_live_mode_disables_passive_sampler(monkeypatch) -> None:
    monkeypatch.setenv("EXECUTION_MODE", "live")
    monkeypatch.setenv("ROBINHOOD_CRYPTO_ENABLED", "true")
    result = sampler.capture_passive_shadow_samples(FakeWorker(), [], {}, client=FakeClient())
    assert result == {"status": "DISABLED", "captured": 0}
