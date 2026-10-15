from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

import crypto_quote_readiness_sampler as sampler
from market_data import MarketSnapshot


def _yahoo_quote(symbol: str, price: float = 100.0) -> dict:
    return {
        "symbol": symbol,
        "requested_symbol": symbol,
        "provider_symbol": symbol,
        "provider": "Yahoo Finance",
        "price": price,
        "quote_verified": True,
        "execution_quote_eligible": True,
        "quote_timestamp": datetime.now(timezone.utc).isoformat(),
        "interval": "1m",
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


def test_v39_sampler_prefers_robinhood_current_snapshot(monkeypatch):
    class Snapshot:
        def to_quote_payload(self):
            return {
                "symbol": "BTC-USD",
                "requested_symbol": "BTC-USD",
                "provider_symbol": "BTC-USD",
                "provider": "Robinhood Crypto",
                "price": 100.0,
                "bid": 99.99,
                "ask": 100.01,
                "quote_timestamp": datetime.now(timezone.utc).isoformat(),
                "interval": "1m",
                "quote_verified": True,
                "provider_quote_verified": True,
                "execution_quote_eligible": True,
                "stale": False,
            }

    monkeypatch.setattr(
        sampler.oracle_bot,
        "_verified_quote_for",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("fallback should not be used")),
    )
    monkeypatch.setattr(
        sampler,
        "_coinbase_reference_validation",
        lambda symbol, price: {
            "ok": True,
            "reason": "COINBASE_REFERENCE_CONFIRMED",
            "reference_provider": "Coinbase Exchange",
            "reference_price": 100.03,
            "reference_timestamp": "2026-09-01T14:30:01+00:00",
            "difference_pct": 0.03,
            "spread_pct": 0.02,
        },
    )
    captured = []
    monkeypatch.setattr(sampler, "_persist_quote_verifications", lambda records: captured.extend(records) or len(records))

    worker = FakeWorker()
    worker._robinhood_current_marketdata_provider = SimpleNamespace(
        snapshots=lambda symbols: {"BTC-USD": Snapshot()}
    )
    signal = SimpleNamespace(symbol="BTC-USD", action="HOLD")

    persisted = sampler.persist_v39_quote_verification_evidence(
        worker,
        [signal],
        {"BTC-USD": _yahoo_quote("BTC-USD")},
        max_samples=1,
    )

    assert persisted == 1
    assert signal.action == "HOLD"
    assert captured[0]["primary_provider"] == "Robinhood Crypto"
    assert captured[0]["secondary_provider"] == "Coinbase Exchange"
    assert captured[0]["payload"]["evidence_kind"] == "independent_crypto_quote_consensus"
    assert captured[0]["payload"]["source"] == "v39_quote_readiness_sampler"


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


def test_sampler_preserves_explicit_paper_reference_provenance():
    snapshot = MarketSnapshot(symbol="BTC-USD", requested_symbol="BTC-USD", provider_symbol="BTC-USD",
        provider="Robinhood Crypto", price=100, change_pct=0, volume=0,
        timestamp=datetime.now(timezone.utc).isoformat(), interval="1m", bid=99.9, ask=100.1,
        quote_verified=True, stale=False, provider_quote_verified=False, paper_reference_verified=True,
        verification_basis="paper_estimate:robinhood_crypto_v2_size_specific")
    payload = sampler._snapshot_payload(snapshot, "BTC-USD")
    assert payload is not None
    assert payload["provider_quote_verified"] is False
    assert payload["verified"] is False
    assert payload["paper_reference_verified"] is True
    assert payload["quote_verified"] is True


def test_sampler_does_not_turn_explicitly_unverified_quote_into_eligible_quote():
    quote = {**_yahoo_quote("BTC-USD"), "provider": "Robinhood Crypto",
             "quote_verified": False, "provider_quote_verified": False, "execution_quote_eligible": False}
    payload = sampler._snapshot_payload(quote, "BTC-USD")
    assert payload is None or (payload["quote_verified"] is False
                               and payload["provider_quote_verified"] is False
                               and payload["execution_quote_eligible"] is False)


@pytest.mark.parametrize("changes", [{"stale": True}, {"quote_timestamp": "invalid"},
    {"quote_timestamp": "2099-01-01T00:00:00Z"}, {"quote_timestamp": "2000-01-01T00:00:00Z"},
    {"requested_symbol": None}, {"provider_symbol": None}, {"symbol": "ETH-USD"},
    {"execution_quote_eligible": False}, {"bid": 102, "ask": 101}, {"bid": "NaN", "ask": 101},
    {"price": True}])
def test_sampler_does_not_upgrade_invalid_primary_evidence(changes):
    quote = {**_yahoo_quote("BTC-USD"), "provider": "Robinhood Crypto", "interval": "1m",
             "quote_timestamp": datetime.now(timezone.utc).isoformat(), "provider_quote_verified": True,
             **changes}
    assert sampler._snapshot_payload(quote, "BTC-USD") is None


def test_paper_estimate_consensus_keeps_independent_agreement_separate_from_provider_grade(monkeypatch):
    snapshot = MarketSnapshot(symbol="BTC-USD", requested_symbol="BTC-USD", provider_symbol="BTC-USD",
        provider="Robinhood Crypto", price=100, change_pct=0, volume=0,
        timestamp=datetime.now(timezone.utc).isoformat(), interval="1m", bid=99.9, ask=100.1,
        quote_verified=True, stale=False, provider_quote_verified=False, paper_reference_verified=True,
        verification_basis="paper_estimate:robinhood_crypto_v2_size_specific")
    worker = FakeWorker()
    worker._robinhood_current_marketdata_provider = SimpleNamespace(snapshots=lambda symbols: {"BTC-USD": snapshot})
    monkeypatch.setattr(sampler, "_coinbase_reference_validation", lambda *args: dict(ok=True,
        reference_provider="Coinbase Exchange", reference_price=100,
        reference_timestamp=datetime.now(timezone.utc).isoformat(), spread_pct=.02, difference_pct=0))
    captured = []
    monkeypatch.setattr(sampler, "_persist_quote_verifications", lambda rows: captured.extend(rows) or len(rows))
    assert sampler.persist_v39_quote_verification_evidence(worker, [SimpleNamespace(symbol="BTC-USD", action="HOLD")], {}) == 1
    assert captured[0]["consensus_status"] == "verified"
    metadata = captured[0]["payload"]
    assert metadata["primary_provider_quote_verified"] is False
    assert metadata["primary_paper_reference_verified"] is True
    assert metadata["primary_execution_quote_eligible"] is True
    assert metadata["primary_verification_basis"].startswith("paper_estimate:")


@pytest.mark.parametrize("changes", [{"stale": True}, {"execution_quote_eligible": False},
                                    {"bid": 102, "ask": 101}])
def test_sampler_fallback_does_not_persist_invalid_primary_even_when_legacy_guard_accepts_it(monkeypatch, changes):
    quote = {**_yahoo_quote("BTC-USD"), "quote_timestamp": datetime.now(timezone.utc).isoformat(),
             "interval": "1m", **changes}
    monkeypatch.setattr(sampler.oracle_bot, "_verified_quote_for", lambda *args: quote)
    monkeypatch.setattr(sampler, "_coinbase_reference_validation", lambda *args: pytest.fail("invalid primary must not be sampled"))
    monkeypatch.setattr(sampler, "_persist_quote_verifications", lambda records: len(records))
    assert sampler.persist_v39_quote_verification_evidence(FakeWorker(), [SimpleNamespace(symbol="BTC-USD")], {}) == 0
