import crypto_dynamic_universe_runtime as runtime


class _Provider:
    def tradable_symbols(self):
        return ["BTC-USD", "ETH-USD", "SOL-USD", "ADA-USD", "AVAX-USD", "LINK-USD"]


class _Log:
    def info(self, *args, **kwargs):
        return None


class _Worker:
    def __init__(self):
        self._robinhood_current_marketdata_provider = _Provider()
        self.log = _Log()


def test_zero_crypto_scan_cap_discovers_every_broker_tradable_pair(monkeypatch):
    import config
    original_watchlist = config.CRYPTO_WATCHLIST
    original_watchlists_crypto = config.WATCHLISTS.get("crypto")
    try:
        monkeypatch.setenv("CRYPTO_DYNAMIC_UNIVERSE_ENABLED", "true")
        monkeypatch.setenv("CRYPTO_MAX_ACTIVE_SCAN_SYMBOLS", "0")
        config.CRYPTO_WATCHLIST = {"BTC-USD": "Bitcoin"}
        config.WATCHLISTS["crypto"] = config.CRYPTO_WATCHLIST
        worker = _Worker()
        assert runtime.install_crypto_dynamic_universe_runtime(worker) is True
        assert set(config.CRYPTO_WATCHLIST) == set(_Provider().tradable_symbols())
        assert worker._crypto_dynamic_universe["broker_tradable_count"] == 6
        assert worker._crypto_dynamic_universe["provider_coverage_count"] == 6
    finally:
        config.CRYPTO_WATCHLIST = original_watchlist
        config.WATCHLISTS["crypto"] = original_watchlists_crypto


def test_positive_crypto_scan_cap_remains_optional(monkeypatch):
    import config
    original_watchlist = config.CRYPTO_WATCHLIST
    original_watchlists_crypto = config.WATCHLISTS.get("crypto")
    try:
        monkeypatch.setenv("CRYPTO_DYNAMIC_UNIVERSE_ENABLED", "true")
        monkeypatch.setenv("CRYPTO_MAX_ACTIVE_SCAN_SYMBOLS", "3")
        config.CRYPTO_WATCHLIST = {"BTC-USD": "Bitcoin"}
        config.WATCHLISTS["crypto"] = config.CRYPTO_WATCHLIST
        worker = _Worker()
        assert runtime.install_crypto_dynamic_universe_runtime(worker) is True
        assert len(config.CRYPTO_WATCHLIST) == 3
    finally:
        config.CRYPTO_WATCHLIST = original_watchlist
        config.WATCHLISTS["crypto"] = original_watchlists_crypto
