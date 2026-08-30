from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

import oracle_bot
import stock_execution_repair as repair


class FakeSnapshot:
    def __init__(self, symbol: str = "MSFT", price: float = 500.0):
        now = datetime.now(timezone.utc).isoformat()
        self.symbol = symbol
        self.price = price
        self.provider = "Yahoo Finance"
        self.timestamp = now
        self.interval = "1m"
        self.quote_verified = True
        self.stale = False

    def to_quote_payload(self):
        return {
            "symbol": self.symbol,
            "requested_symbol": self.symbol,
            "provider_symbol": self.symbol,
            "provider_native_symbol": self.symbol,
            "provider": self.provider,
            "price": self.price,
            "quote_timestamp": self.timestamp,
            "timestamp": self.timestamp,
            "interval": self.interval,
            "quote_verified": True,
            "verified": True,
            "stale": False,
            "source_capability": "history_intraday",
            "source_identity": f"Yahoo Finance:{self.symbol}:1d:1m",
            "correlation_id": "repair-test",
        }


class FakeWorker:
    def __init__(self):
        self.log = SimpleNamespace(info=lambda *args, **kwargs: None)
        self.processed = None

        def original_quote(symbol, history, price=None, *, scan_type=""):
            return {
                "symbol": symbol,
                "requested_symbol": symbol,
                "provider_symbol": symbol,
                "provider": "research-only",
                "price": 490.0,
                "quote_timestamp": "2026-08-28T20:00:00+00:00",
                "timestamp": "2026-08-28T20:00:00+00:00",
                "interval": "1d",
                "quote_verified": False,
                "verified": False,
                "stale": True,
            }

        def original_process(market, signals, prices=None, *args, **kwargs):
            self.processed = list(signals or [])
            return [{"symbol": getattr(item, "symbol", "")} for item in self.processed]

        self._execution_quote_payload_from_history = original_quote
        self.process_signals = original_process

    @staticmethod
    def _average_dollar_volume(history):
        return 25_000_000.0


def test_verified_live_quote_replaces_research_execution_payload(monkeypatch):
    repair._snapshot_cache.clear()
    monkeypatch.setattr(repair, "_verified_live_snapshot", lambda symbol: FakeSnapshot(symbol, 501.25))
    monkeypatch.setattr(
        oracle_bot,
        "_verified_quote_for",
        lambda symbol, prices, market="cash": (prices or {}).get(symbol)
        if (prices or {}).get(symbol, {}).get("quote_verified") is True
        else None,
    )

    captured = {}

    def original_gate(market, symbol, price, signal=None, quote=None):
        captured["quote"] = dict(quote or {})
        return True, "ok"

    monkeypatch.setattr(oracle_bot, "_entry_forecast_gate", original_gate)

    worker = FakeWorker()
    repair.install_stock_execution_quote_repair(worker)

    payload = worker._execution_quote_payload_from_history("MSFT", object(), scan_type="deep")
    assert payload is not None
    assert payload["price"] == 501.25
    assert payload["quote_verified"] is True
    assert payload["stale"] is False
    assert payload["interval"] == "1m"
    assert payload["avg_dollar_volume"] == 25_000_000.0

    signal = SimpleNamespace(
        symbol="MSFT",
        source_interval="1d",
        source_quote_timestamp="2026-08-28T20:00:00+00:00",
        market_data_route={
            "interval": "1d",
            "quote_timestamp": "2026-08-28T20:00:00+00:00",
        },
    )
    result = worker.process_signals("cash", [signal], {"MSFT": payload})
    assert result == [{"symbol": "MSFT"}]

    ok, reason = oracle_bot._entry_forecast_gate("cash", "MSFT", 501.25, signal, payload)
    assert ok is True
    assert reason == "ok"
    assert captured["quote"]["interval"] == "1d"
    assert captured["quote"]["quote_timestamp"] == "2026-08-28T20:00:00+00:00"


def test_unverified_stock_quote_is_filtered_before_execution(monkeypatch):
    repair._snapshot_cache.clear()
    monkeypatch.setattr(repair, "_verified_live_snapshot", lambda symbol: None)
    monkeypatch.setattr(
        oracle_bot,
        "_verified_quote_for",
        lambda symbol, prices, market="cash": None,
    )
    monkeypatch.setattr(
        oracle_bot,
        "_entry_forecast_gate",
        lambda market, symbol, price, signal=None, quote=None: (False, "not reached"),
    )

    worker = FakeWorker()
    repair.install_stock_execution_quote_repair(worker)
    signal = SimpleNamespace(symbol="JNJ")

    result = worker.process_signals(
        "cash",
        [signal],
        {
            "JNJ": {
                "symbol": "JNJ",
                "requested_symbol": "JNJ",
                "provider_symbol": "JNJ",
                "price": 150.0,
                "quote_verified": False,
            }
        },
    )

    assert result == []
    assert worker.processed == []
