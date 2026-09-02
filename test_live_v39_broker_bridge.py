from types import SimpleNamespace

import live_v39_broker_bridge as bridge


class _Log:
    def __init__(self):
        self.events = []

    def info(self, *args):
        self.events.append(("info", args))

    def warning(self, *args):
        self.events.append(("warning", args))


class _Snapshot:
    source = "robinhood_crypto_v2"

    def __init__(self, *, allowed=True):
        self.valid = True
        self.complete = allowed
        self.sizing_allowed = allowed
        self.buying_power = 1250.0
        self.equity = 2000.0
        self.gross_exposure = 750.0
        self.position_values = {"BTC-USD": 500.0, "ETH-USD": 250.0}
        self.tradable_quantities = {"BTC-USD": 0.005, "ETH-USD": 0.1}
        self.missing_quotes = () if allowed else ("ETH-USD",)
        self.reason = "BROKER_CAPITAL_VERIFIED" if allowed else "BROKER_HOLDING_QUOTE_INCOMPLETE"

    def portfolio_metrics(self):
        return {
            "cash": self.buying_power if self.sizing_allowed else 0.0,
            "equity": self.equity,
            "total_equity": self.equity,
            "buying_power": self.buying_power if self.sizing_allowed else 0.0,
            "gross_exposure": self.gross_exposure,
            "broker_capital_valid": self.valid,
            "broker_capital_complete": self.complete,
            "broker_capital_reason": self.reason,
            "broker_capital_source": self.source,
        }


class _Provider:
    def __init__(self, snapshot):
        self._snapshot = snapshot
        self.calls = []

    def snapshot(self, *, fresh=False):
        self.calls.append(fresh)
        return self._snapshot


def _worker():
    worker = SimpleNamespace()
    worker.log = _Log()
    worker._v39_position_rows = lambda market: ({"cash": 2000.0, "equity": 2000.0, "source": "paper-db"}, [])
    return worker


def test_paper_mode_keeps_database_portfolio(monkeypatch):
    monkeypatch.setenv("EXECUTION_MODE", "paper")
    monkeypatch.setenv("ROBINHOOD_CRYPTO_ENABLED", "true")
    worker = _worker()
    provider = _Provider(_Snapshot())
    oracle = SimpleNamespace(_live_broker_capital_provider=provider)

    bridge.install_live_v39_broker_capital_bridge(worker, oracle)
    portfolio, positions = worker._v39_position_rows("crypto")

    assert portfolio["source"] == "paper-db"
    assert positions == []
    assert provider.calls == []


def test_live_mode_uses_fresh_verified_robinhood_capital(monkeypatch):
    monkeypatch.setenv("EXECUTION_MODE", "live")
    monkeypatch.setenv("ROBINHOOD_CRYPTO_ENABLED", "true")
    worker = _worker()
    provider = _Provider(_Snapshot(allowed=True))
    oracle = SimpleNamespace(_live_broker_capital_provider=provider)

    bridge.install_live_v39_broker_capital_bridge(worker, oracle)
    portfolio, positions = worker._v39_position_rows("crypto")

    assert provider.calls == [True]
    assert portfolio["equity"] == 2000.0
    assert portfolio["buying_power"] == 1250.0
    assert portfolio["broker_capital_validated"] is True
    assert {row["symbol"] for row in positions} == {"BTC-USD", "ETH-USD"}
    assert sum(row["market_value"] for row in positions) == 750.0


def test_incomplete_live_broker_snapshot_fails_closed_for_new_allocations(monkeypatch):
    monkeypatch.setenv("EXECUTION_MODE", "live")
    monkeypatch.setenv("ROBINHOOD_CRYPTO_ENABLED", "true")
    worker = _worker()
    provider = _Provider(_Snapshot(allowed=False))
    oracle = SimpleNamespace(_live_broker_capital_provider=provider)

    bridge.install_live_v39_broker_capital_bridge(worker, oracle)
    portfolio, positions = worker._v39_position_rows("crypto")

    assert portfolio["buying_power"] == 0.0
    assert portfolio["cash"] == 0.0
    assert portfolio["broker_capital_validated"] is False
    assert len(positions) == 2


def test_live_non_crypto_market_is_untouched(monkeypatch):
    monkeypatch.setenv("EXECUTION_MODE", "live")
    monkeypatch.setenv("ROBINHOOD_CRYPTO_ENABLED", "true")
    worker = _worker()
    provider = _Provider(_Snapshot())
    oracle = SimpleNamespace(_live_broker_capital_provider=provider)

    bridge.install_live_v39_broker_capital_bridge(worker, oracle)
    portfolio, positions = worker._v39_position_rows("cash")

    assert portfolio["source"] == "paper-db"
    assert positions == []
    assert provider.calls == []
