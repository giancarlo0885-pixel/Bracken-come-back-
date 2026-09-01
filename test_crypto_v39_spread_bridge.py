from types import SimpleNamespace

import crypto_v39_spread_bridge as bridge


class _Log:
    def __init__(self):
        self.events = []

    def info(self, *args):
        self.events.append(args)


def _worker(payload):
    worker = SimpleNamespace()
    worker.log = _Log()
    worker._execution_quote_payload_from_history = lambda *args, **kwargs: dict(payload)
    return worker


def test_coinbase_consensus_supplies_missing_spread_without_inventing_book(monkeypatch):
    worker = _worker(
        {
            "market": "crypto",
            "provider": "Yahoo Finance",
            "paper_reference_verified": True,
            "quote_verified": True,
            "stale": False,
            "price": 100.0,
            "spread_pct": None,
            "bid": None,
            "ask": None,
        }
    )
    monkeypatch.setattr(
        bridge,
        "_coinbase_reference_validation",
        lambda symbol, price: {
            "ok": True,
            "reference_provider": "Coinbase Exchange",
            "reference_price": 100.0,
            "reference_timestamp": "2026-09-01T22:20:00+00:00",
            "spread_pct": 0.12,
            "difference_pct": 0.0,
        },
    )

    bridge.install_crypto_v39_spread_bridge(worker)
    payload = worker._execution_quote_payload_from_history("BTC-USD", object(), scan_type="fast")

    assert payload["spread_pct"] == 0.12
    assert payload["spread_known"] is True
    assert payload["spread_provider"] == "Coinbase Exchange"
    assert payload["price_consensus_verified"] is True
    assert payload["bid"] is None
    assert payload["ask"] is None


def test_failed_coinbase_consensus_keeps_spread_unknown(monkeypatch):
    worker = _worker(
        {
            "market": "crypto",
            "provider": "Yahoo Finance",
            "paper_reference_verified": True,
            "quote_verified": True,
            "stale": False,
            "price": 100.0,
            "spread_pct": None,
        }
    )
    monkeypatch.setattr(
        bridge,
        "_coinbase_reference_validation",
        lambda symbol, price: {"ok": False, "reason": "COINBASE_SPREAD_TOO_WIDE", "spread_pct": 2.5},
    )

    bridge.install_crypto_v39_spread_bridge(worker)
    payload = worker._execution_quote_payload_from_history("BTC-USD", object(), scan_type="fast")

    assert payload["spread_pct"] is None
    assert payload.get("spread_known") is not True
    assert payload.get("price_consensus_verified") is not True


def test_provider_verified_or_non_crypto_quotes_are_unchanged(monkeypatch):
    worker = _worker(
        {
            "market": "crypto",
            "provider": "Coinbase",
            "paper_reference_verified": False,
            "price": 100.0,
            "spread_pct": 0.08,
        }
    )
    called = {"value": False}

    def validation(symbol, price):
        called["value"] = True
        return {"ok": True, "spread_pct": 0.1}

    monkeypatch.setattr(bridge, "_coinbase_reference_validation", validation)
    bridge.install_crypto_v39_spread_bridge(worker)
    payload = worker._execution_quote_payload_from_history("BTC-USD", object())

    assert payload["spread_pct"] == 0.08
    assert called["value"] is False
