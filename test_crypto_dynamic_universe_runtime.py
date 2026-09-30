import os
from types import SimpleNamespace

import config
from crypto_dynamic_universe_runtime import _max_symbols, install_crypto_dynamic_universe_runtime


class _Log:
    def info(self, *args, **kwargs):
        pass


class _Provider:
    def __init__(self, symbols):
        self._symbols = symbols

    def tradable_symbols(self):
        return list(self._symbols)


def test_auto_limit_covers_complete_provider_universe(monkeypatch):
    monkeypatch.setenv("CRYPTO_MAX_ACTIVE_SCAN_SYMBOLS", "auto")
    assert _max_symbols(provider_count=137, static_count=20) == 137


def test_explicit_positive_limit_is_honored(monkeypatch):
    monkeypatch.setenv("CRYPTO_MAX_ACTIVE_SCAN_SYMBOLS", "60")
    assert _max_symbols(provider_count=137, static_count=20) == 60


def test_dynamic_universe_keeps_core_and_provider_symbols(monkeypatch):
    monkeypatch.setenv("CRYPTO_DYNAMIC_UNIVERSE_ENABLED", "true")
    monkeypatch.setenv("CRYPTO_MAX_ACTIVE_SCAN_SYMBOLS", "auto")
    original = dict(config.CRYPTO_WATCHLIST)
    try:
        monkeypatch.setattr(config, "CRYPTO_WATCHLIST", {"BTC-USD": "Bitcoin", "ETH-USD": "Ethereum"})
        monkeypatch.setitem(config.WATCHLISTS, "crypto", config.CRYPTO_WATCHLIST)
        provider_symbols = ["BTC-USD", "ETH-USD"] + [f"COIN{i}-USD" for i in range(90)]
        worker = SimpleNamespace(
            _robinhood_current_marketdata_provider=_Provider(provider_symbols),
            log=_Log(),
        )
        assert install_crypto_dynamic_universe_runtime(worker) is True
        assert "BTC-USD" in config.CRYPTO_WATCHLIST
        assert "ETH-USD" in config.CRYPTO_WATCHLIST
        assert all(symbol in config.CRYPTO_WATCHLIST for symbol in provider_symbols)
        assert worker._crypto_dynamic_universe["provider_coverage_count"] == len(provider_symbols)
    finally:
        config.CRYPTO_WATCHLIST = original
        config.WATCHLISTS["crypto"] = config.CRYPTO_WATCHLIST
