from __future__ import annotations

from types import SimpleNamespace

import crypto_quote_readiness_sampler as sampler


def _yahoo_quote(symbol: str, price: float = 100.0) -> dict:
    return {
        "symbol": symbol,
        "requested_symbol": symbol,
        "provider_symbol": symbol,
        "provider": "Yahoo Finance",
        "price": price,
        "quote_verified": True,
        "execution_quote_eligible": True,
        "quote_timestamp": "2026-09-01T14:30:00+00:00",
        "stale": False,
    }


class FakeWorker:
    def __init__(self):
        self.original_calls = []
        self.logs = []
        self.log = SimpleNamespace(
            info=lambda *args, **kwargs: self.logs.append(("info", args)),
            warning=lambda *args, **kwargs: self.logs.append(("warning", args)),
        )

        def original(market, signals, prices, ranked, scan_type, *args, **kwargs):
            self.original_calls.append((market, list(signals), prices, ranked, scan_type))
            return []

        self._v39_execute_iterative = original


def test_v39_hold_market_persists_consensus_without_promoting_signal(monkeypatch):
    quote = _yahoo_quote("BTC-USD")
    monkeypatch.setattr(
        sampler.oracle_bot,
        "_verified_quote_for",
        lambda symbol, prices, market="crypto": quote if symbol == "BTC-USD" else None,
    )
    monkeypatch.setattr(
        sampler,
        "_coinbase_reference_validation",
        lambda symbol, price: {
            "ok": True,
            "reason": "COINBASE_REFERENCE_CONFIRMED",
            "reference_provider": "Coinbase Exchange",
            "reference_price": 100.05,
            "reference_timestamp": "2026-09-01T14:30:01+00:00",
            "difference_pct": 0.05,
            "spread_pct": 0.02,
        },
    )
    captured = []

    def persist(records):
        captured.extend(records)
        return len(records)

    monkeypatch.setattr(sampler, "_persist_quote_verifications", persist)
    worker = FakeWorker()
    sampler.install_v39_quote_verification_sampler(worker)
    signal = SimpleNamespace(symbol="BTC-USD", action="HOLD")

    result = worker._v39_execute_iterative(
        "crypto",
        [signal],
        {"BTC-USD": quote},
        [],
        "fast",
    )

    assert result == []
    assert worker.original_calls[0][1] == [signal]
    assert signal.action == "HOLD"
    assert len(captured) == 1
    assert captured[0]["symbol"] == "BTC-USD"
    assert captured[0]["consensus_status"] == "verified"
    assert captured[0]["secondary_provider"] == "Coinbase Exchange"
    assert any("V39 QUOTE VERIFICATION EVIDENCE" in str(args[0]) for level, args in worker.logs if level == "info")


def test_v39_sampler_persists_rejection_evidence_without_executing(monkeypatch):
    quote = _yahoo_quote("ETH-USD")
    monkeypatch.setattr(sampler.oracle_bot, "_verified_quote_for", lambda *args, **kwargs: quote)
    monkeypatch.setattr(
        sampler,
        "_coinbase_reference_validation",
        lambda symbol, price: {
            "ok": False,
            "reason": "COINBASE_PRICE_DIVERGENCE",
            "reference_provider": "Coinbase Exchange",
            "reference_price": 103.0,
            "reference_timestamp": "2026-09-01T14:30:01+00:00",
            "difference_pct": 2.91,
            "spread_pct": 0.03,
        },
    )
    captured = []
    monkeypatch.setattr(
        sampler,
        "_persist_quote_verifications",
        lambda records: captured.extend(records) or len(records),
    )
    worker = FakeWorker()
    sampler.install_v39_quote_verification_sampler(worker)
    signal = SimpleNamespace(symbol="ETH-USD", action="HOLD")

    worker._v39_execute_iterative("crypto", [signal], {"ETH-USD": quote}, [], "fast")

    assert signal.action == "HOLD"
    assert captured[0]["consensus_status"] == "rejected"
    assert captured[0]["difference_pct"] == 2.91
    assert any("V39 CONSENSUS EVIDENCE REJECTED" in str(args[0]) for level, args in worker.logs if level == "info")


def test_v39_sampler_is_bounded_and_crypto_only(monkeypatch):
    quotes = {
        f"C{i}-USD": _yahoo_quote(f"C{i}-USD", 100.0 + i)
        for i in range(5)
    }
    monkeypatch.setattr(
        sampler.oracle_bot,
        "_verified_quote_for",
        lambda symbol, prices, market="crypto": prices.get(symbol),
    )
    monkeypatch.setattr(
        sampler,
        "_coinbase_reference_validation",
        lambda symbol, price: {
            "ok": True,
            "reason": "COINBASE_REFERENCE_CONFIRMED",
            "reference_provider": "Coinbase Exchange",
            "reference_price": price,
            "reference_timestamp": "2026-09-01T14:30:01+00:00",
            "difference_pct": 0.0,
            "spread_pct": 0.01,
        },
    )
    captured = []
    monkeypatch.setattr(
        sampler,
        "_persist_quote_verifications",
        lambda records: captured.extend(records) or len(records),
    )

    worker = FakeWorker()
    signals = [SimpleNamespace(symbol=symbol, action="HOLD") for symbol in quotes]
    assert sampler.persist_v39_quote_verification_evidence(worker, signals, quotes, max_samples=2) == 2
    assert len(captured) == 2

    captured.clear()
    sampler.install_v39_quote_verification_sampler(worker)
    worker._v39_execute_iterative("cash", signals, quotes, [], "fast")
    assert captured == []
