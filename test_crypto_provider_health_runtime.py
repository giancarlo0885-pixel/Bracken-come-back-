from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
import time

import crypto_provider_health_runtime as runtime

from crypto_provider_health_runtime import install_crypto_provider_health_runtime


class _Log:
    def __init__(self):
        self.messages = []

    def info(self, message, *args):
        self.messages.append(message % args if args else message)


class _Provider:
    def __init__(self):
        self._oracle_quality_quarantined_symbols = set()

    def tradable_symbols(self):
        return {"BTC-USD", "ETH-USD"}

    def snapshots(self, symbols):
        # Model the Robinhood resilience layer discovering repeated crossed books
        # during this call and quarantining BTC while still returning paper grace.
        if "BTC-USD" in symbols:
            self._oracle_quality_quarantined_symbols.add("BTC-USD")
        return {
            symbol: {"symbol": symbol, "bid": 100.0, "ask": 100.1}
            for symbol in symbols
            if symbol in {"BTC-USD", "ETH-USD"}
        }


def test_provider_health_reports_quarantine_created_during_snapshot_call():
    provider = _Provider()
    worker = SimpleNamespace(
        _robinhood_current_marketdata_provider=provider,
        log=_Log(),
    )

    assert install_crypto_provider_health_runtime(worker) is True
    result = provider.snapshots(["BTC-USD", "ETH-USD"])

    assert set(result) == {"BTC-USD", "ETH-USD"}
    health = worker._crypto_provider_health
    assert health["last_quality_quarantined"] == ["BTC-USD"]
    assert health["last_requested"] == 1
    assert health["last_resolved"] == 1
    assert health["availability_score"] == 100.0
    assert health["data_quality_score"] == 50.0
    assert health["quote_health_score"] == 50.0
    assert any("quality_quarantined=BTC-USD" in message for message in worker.log.messages)


def test_provider_health_keeps_unsupported_symbols_as_coverage_gaps():
    provider = _Provider()
    worker = SimpleNamespace(
        _robinhood_current_marketdata_provider=provider,
        log=_Log(),
    )

    assert install_crypto_provider_health_runtime(worker) is True
    provider.snapshots(["ETH-USD", "UNSUPPORTED-USD"])

    health = worker._crypto_provider_health
    assert health["last_coverage_gaps"] == ["UNSUPPORTED-USD"]
    assert health["last_quality_quarantined"] == []
    assert health["last_requested"] == 1
    assert health["last_resolved"] == 1


def test_concurrent_health_scoring_uses_immutable_window(monkeypatch):
    provider = _Provider()
    worker = SimpleNamespace(_robinhood_current_marketdata_provider=provider, log=_Log())
    assert install_crypto_provider_health_runtime(worker)

    original_ratio = runtime._ratio_score

    def checked_ratio(events, requested_index, good_index):
        # Fail deterministically if the shared mutable deque reaches scoring.
        assert isinstance(events, tuple)
        time.sleep(0)  # Yield to concurrent snapshot callers.
        return original_ratio(events, requested_index, good_index)

    monkeypatch.setattr(runtime, "_ratio_score", checked_ratio)
    with ThreadPoolExecutor(max_workers=12) as executor:
        results = list(executor.map(lambda _: provider.snapshots(["ETH-USD"]), range(300)))

    assert all("ETH-USD" in result for result in results)
    health = worker._crypto_provider_health
    assert health["window_calls"] == 200
    assert health["requested"] == 200
    assert health["resolved"] == 200
    assert health["availability_score"] == 100.0
    assert health["data_quality_score"] == 100.0
