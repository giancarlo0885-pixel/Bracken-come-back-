from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import threading
import time
from types import SimpleNamespace

import pandas as pd

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
        return reliability.yahoo_reference_history(module, "APT-USD", "5d", "5m")

    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(lambda _: run(), range(4)))

    assert counter["calls"] == 1
    assert all(result.empty for result in results)
    assert reliability._cooldown_active(reliability._key("APT-USD", "5d", "5m")) is True


def test_successful_yahoo_reference_preserves_exact_identity_and_clears_cooldown():
    module = _market_data_stub(lambda symbol: _WorkingTicker())
    key = reliability._key("BTC-USD", "5d", "5m")
    reliability._UNAVAILABLE_UNTIL[key] = time.monotonic() - 1

    result = reliability.yahoo_reference_history(module, "BTC-USD", "5d", "5m")

    assert not result.empty
    assert result.attrs["provider"] == "Yahoo Finance"
    assert result.attrs["requested_symbol"] == "BTC-USD"
    assert result.attrs["provider_symbol"] == "BTC-USD"
    assert reliability._cooldown_active(key) is False


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
