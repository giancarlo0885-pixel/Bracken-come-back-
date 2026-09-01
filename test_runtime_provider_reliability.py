from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import threading
import time
from types import SimpleNamespace

import pandas as pd
import pytest

import crypto_execution_guard as crypto_guard
import runtime_provider_reliability as reliability


class _FailingTicker:
    def __init__(self, counter: dict[str, int], lock: threading.Lock):
        self.counter = counter
        self.lock = lock

    def history(self, **kwargs):
        assert kwargs.get("raise_errors") is True
        with self.lock:
            self.counter["calls"] += 1
        time.sleep(0.03)
        raise RuntimeError("missing Yahoo history")


class _WorkingTicker:
    def history(self, **kwargs):
        assert kwargs.get("raise_errors") is True
        return pd.DataFrame(
            {
                "Open": [100.0, 101.0],
                "High": [101.0, 102.0],
                "Low": [99.0, 100.0],
                "Close": [100.5, 101.5],
                "Volume": [1000.0, 1100.0],
            },
            index=pd.date_range("2026-08-31T18:00:00Z", periods=2, freq="5min"),
        )


def _market_data_stub(ticker_factory):
    def normalize(frame, symbol):
        return frame.copy()

    def stamp(frame, requested_symbol, provider_symbol, provider, interval):
        out = frame.copy()
        out.attrs.update(
            {
                "requested_symbol": requested_symbol,
                "provider_symbol": provider_symbol,
                "provider_native_symbol": provider_symbol,
                "provider": provider,
                "interval": interval,
            }
        )
        return out

    return SimpleNamespace(
        yf=SimpleNamespace(Ticker=ticker_factory),
        _normalize=normalize,
        _stamp_history=stamp,
    )


def setup_function():
    reliability._UNAVAILABLE_UNTIL.clear()
    reliability._KEY_LOCKS.clear()


def test_concurrent_missing_yahoo_symbol_is_requested_once_then_cooled_down():
    counter = {"calls": 0}
    lock = threading.Lock()
    module = _market_data_stub(lambda symbol: _FailingTicker(counter, lock))

    def run():
        return reliability.yahoo_reference_history(module, "TEST-USD", "5d", "5m")

    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(lambda _: run(), range(4)))

    assert counter["calls"] == 1
    assert all(result.empty for result in results)
    assert reliability._cooldown_active(reliability._key("TEST-USD", "5d", "5m")) is True


def test_successful_yahoo_reference_preserves_exact_identity_and_clears_cooldown():
    module = _market_data_stub(lambda symbol: _WorkingTicker())
    key = reliability._key("BTC-USD", "5d", "5m")
    reliability._UNAVAILABLE_UNTIL[key] = time.monotonic() - 1

    result = reliability.yahoo_reference_history(module, "BTC-USD", "5d", "5m")

    assert not result.empty
    assert result.attrs["provider"] == "Yahoo Finance"
    assert result.attrs["requested_symbol"] == "BTC-USD"
    assert result.attrs["provider_symbol"] == "BTC-USD"
    assert result.attrs["provider_native_symbol"] == "BTC-USD"
    assert reliability._cooldown_active(key) is False


@pytest.mark.parametrize(
    ("canonical", "native"),
    [
        ("APT-USD", "APT21794-USD"),
        ("ARB-USD", "ARB11841-USD"),
        ("PEPE-USD", "PEPE24478-USD"),
        ("POL-USD", "POL28321-USD"),
        ("SUI-USD", "SUI20947-USD"),
        ("UNI-USD", "UNI7083-USD"),
    ],
)
def test_current_crypto_aliases_use_yahoo_native_symbol_but_keep_canonical_identity(canonical, native):
    requested_tickers: list[str] = []

    def ticker_factory(symbol: str):
        requested_tickers.append(symbol)
        return _WorkingTicker()

    module = _market_data_stub(ticker_factory)
    result = reliability.yahoo_reference_history(module, canonical, "5d", "5m")

    assert requested_tickers == [native]
    assert reliability.yahoo_native_symbol(canonical) == native
    assert result.attrs["requested_symbol"] == canonical
    assert result.attrs["provider_symbol"] == canonical
    assert result.attrs["provider_native_symbol"] == native


def test_arbitrum_strict_yahoo_path_preserves_native_alias(monkeypatch):
    import provider_router

    requested_tickers: list[str] = []

    def ticker_factory(symbol: str):
        requested_tickers.append(symbol)
        return _WorkingTicker()

    module = _market_data_stub(ticker_factory)
    frame = reliability.yahoo_reference_history(module, "ARB-USD", "5d", "5m")
    original = provider_router._strict_yahoo_history
    monkeypatch.setattr(provider_router, "_strict_yahoo_history", original)
    reliability._install_yahoo_strict_alias_preservation()

    result = provider_router._strict_yahoo_history(frame, "ARB-USD", "5d", "5m")

    assert requested_tickers == ["ARB11841-USD"]
    assert not result.empty
    assert result.attrs["requested_symbol"] == "ARB-USD"
    assert result.attrs["provider_symbol"] == "ARB-USD"
    assert result.attrs["provider_native_symbol"] == "ARB11841-USD"
    assert result.attrs["quote_verified"] is False


def test_runtime_crypto_universe_retires_matic_for_pol(monkeypatch):
    import config

    watchlist = {
        "BTC-USD": "Bitcoin",
        "MATIC-USD": "Polygon",
        "PEPE-USD": "Pepe",
    }
    watchlists = {"cash": {}, "crypto": watchlist}
    monkeypatch.setattr(config, "CRYPTO_WATCHLIST", watchlist)
    monkeypatch.setattr(config, "WATCHLISTS", watchlists)

    assert reliability._install_current_crypto_universe() is True
    assert "MATIC-USD" not in watchlist
    assert watchlist["POL-USD"] == "Polygon Ecosystem Token"
    assert config.WATCHLISTS["crypto"] is watchlist


def test_current_crypto_universe_is_idempotent(monkeypatch):
    import config

    watchlist = {"BTC-USD": "Bitcoin", "POL-USD": "Polygon Ecosystem Token"}
    watchlists = {"cash": {}, "crypto": watchlist}
    monkeypatch.setattr(config, "CRYPTO_WATCHLIST", watchlist)
    monkeypatch.setattr(config, "WATCHLISTS", watchlists)

    assert reliability._install_current_crypto_universe() is False
    assert list(watchlist).count("POL-USD") == 1


def test_yahoo_provider_name_without_execution_eligibility_is_not_consensus_candidate():
    quote = {"provider": "Yahoo Finance", "price": 100.0}
    assert crypto_guard._paper_yahoo_reference(quote) is False


def test_legacy_yahoo_execution_candidate_is_always_sent_to_consensus():
    quote = {
        "provider": "Yahoo Finance",
        "quote_verified": True,
        "price": 100.0,
    }
    assert crypto_guard._paper_yahoo_reference(quote) is True


def test_explicit_yahoo_paper_reference_is_consensus_candidate_even_with_new_metadata():
    quote = {
        "provider": "Yahoo Finance",
        "quote_verified": True,
        "execution_quote_eligible": True,
        "paper_reference_verified": True,
        "provider_quote_verified": False,
        "price": 100.0,
    }
    assert crypto_guard._paper_yahoo_reference(quote) is True
    # Even if a malformed/legacy payload incorrectly claims provider verification,
    # Yahoo still must go through Coinbase rather than bypassing consensus.
    quote["provider_quote_verified"] = True
    assert crypto_guard._paper_yahoo_reference(quote) is True


def test_crypto_railway_config_uses_key_normalizing_entrypoint():
    payload = json.loads(Path("railway.crypto-worker.json").read_text(encoding="utf-8"))
    assert payload["deploy"]["startCommand"] == "python crypto_worker_entrypoint.py"
