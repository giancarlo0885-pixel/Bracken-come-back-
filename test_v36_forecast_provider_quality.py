from __future__ import annotations

from datetime import datetime, timedelta, timezone
import io
import logging

import pandas as pd
import pytest
import requests

import market_worker
import oracle_bot
import provider_capabilities
import provider_router
from forecasting import bars_per_year, forecast_price
from prediction_engine import build_decisions
from production_audit import build_audit_report
from structured_logging import StructuredFormatter


def _history(symbol: str = "AAPL", interval: str = "1d", periods: int = 80) -> pd.DataFrame:
    index = pd.date_range("2026-01-01", periods=periods, freq="D", tz="UTC")
    if interval.endswith("m") or interval.endswith("h"):
        freq = "5min" if interval == "5m" else "1h"
        index = pd.date_range("2026-01-01", periods=periods, freq=freq, tz="UTC")
    close = [100 + i * 0.1 for i in range(periods)]
    frame = pd.DataFrame({"Close": close, "Volume": [1000 + i for i in range(periods)]}, index=index)
    frame.attrs["provider_route"] = {
        "requested_symbol": symbol,
        "provider_symbol": symbol,
        "provider": "unit",
        "interval": interval,
        "quote_timestamp": index[-1].to_pydatetime().isoformat(),
        "quote_verified": True,
    }
    return frame


def test_five_minute_returns_are_not_treated_as_daily_returns():
    forecast = forecast_price(_history(interval="5m"), days=1, source_interval="5m", market="cash")
    assert forecast is not None
    assert forecast.source_interval == "5m"
    assert forecast.horizon_bars == 288
    assert forecast.horizon_days == pytest.approx(1.0)
    assert forecast.bars_per_year == pytest.approx(252 * 390 / 5)


def test_hourly_and_daily_forecasts_use_correct_horizon_scaling():
    hourly = forecast_price(_history(interval="1h"), horizon_hours=6, source_interval="1h")
    daily = forecast_price(_history(interval="1d"), horizon_days=6, source_interval="1d")
    assert hourly is not None and daily is not None
    assert hourly.horizon_bars == 6
    assert daily.horizon_bars == 6
    assert hourly.horizon_minutes == 360
    assert daily.horizon_minutes == 8640


def test_crypto_forecasts_handle_twenty_four_seven_calendar():
    forecast = forecast_price(_history("BTC-USD", "5m"), horizon_days=1, source_interval="5m", market="crypto")
    assert forecast is not None
    assert forecast.asset_class == "crypto"
    assert bars_per_year("5m", "crypto") == pytest.approx(365 * 24 * 60 / 5)


def test_fast_forecast_cannot_replace_deep_forecast():
    now = datetime.now(timezone.utc).isoformat()
    signals = [{"market": "cash", "symbol": "AAPL", "price": 100, "action": "BUY", "confidence": .9, "created_at": now, "scan_type": "deep", "source_interval": "1d"}]
    forecasts = [
        {"market": "cash", "symbol": "AAPL", "target_price": 130, "created_at": now, "scan_type": "fast", "source_interval": "5m"},
        {"market": "cash", "symbol": "AAPL", "target_price": 103, "created_at": now, "scan_type": "deep", "source_interval": "1d"},
    ]
    decision = build_decisions([{"market": "cash", "symbol": "AAPL", "opportunity_score": 90, "payload": {"action": "BUY"}}], signals, forecasts, 1)[0]
    assert decision["target"] == 103


def test_forecast_gate_requires_matching_quote_identity_and_timestamp(monkeypatch):
    quote_time = datetime.now(timezone.utc).isoformat()
    monkeypatch.setattr(
        oracle_bot,
        "row",
        lambda *args, **kwargs: {
            "target_price": 105,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "requested_symbol": "AAPL",
            "provider_symbol": "AAPL",
            "source_interval": "5m",
            "source_quote_timestamp": quote_time,
            "scan_type": "fast",
            "model": "log-return diffusion",
            "model_version": "unit",
            "data_quality_score": 90,
        },
    )
    ok, reason = oracle_bot._entry_forecast_gate(
        "cash",
        "AAPL",
        100,
        {"symbol": "AAPL", "scan_type": "deep"},
        {"interval": "1d", "quote_timestamp": quote_time},
    )
    assert ok is False
    assert "interval" in reason or "scan type" in reason


def test_unsupported_provider_capability_enters_cooldown(monkeypatch):
    provider_capabilities._cooldowns.clear()
    response = requests.Response()
    response.status_code = 403
    error = requests.HTTPError("403 plan not supported")
    error.response = response

    monkeypatch.setattr(provider_router, "get_api_settings", lambda: {"POLYGON_API_KEY": "x"})
    monkeypatch.setattr(provider_router, "_polygon", lambda *args, **kwargs: (_ for _ in ()).throw(error))
    monkeypatch.setattr(provider_router, "_finnhub", lambda *args, **kwargs: pd.DataFrame())
    monkeypatch.setattr(provider_router, "_eodhd", lambda *args, **kwargs: pd.DataFrame())
    monkeypatch.setattr(provider_router, "_alpha", lambda *args, **kwargs: pd.DataFrame())
    monkeypatch.setattr(provider_router, "mark_symbol_unavailable", lambda *args, **kwargs: None)
    provider_router.route_history("ZZZV36", "5d", "1d", lambda *args: pd.DataFrame())
    assert provider_capabilities.capability_available("Polygon", "us_history") is False
    assert provider_capabilities.capability_available("Polygon", "movers") is True


def test_crypto_symbols_bypass_equity_only_provider_routes(monkeypatch):
    called = {"yahoo": 0}
    monkeypatch.setattr(provider_router, "get_api_settings", lambda: {})

    def yahoo(symbol, period, interval):
        called["yahoo"] += 1
        return _history(symbol, interval)

    result = provider_router.route_history("BTC-USD", "5d", "1d", yahoo)
    assert result.provider == "Yahoo Finance"
    assert called["yahoo"] == 1
    assert result.frame.attrs["requested_symbol"] == "BTC-USD"


def test_separate_stock_and_crypto_kill_switches_default_false():
    assert market_worker.ENABLE_STOCK_AUTOTRADE is False
    assert market_worker.ENABLE_CRYPTO_AUTOTRADE is False
    assert market_worker._execution_enabled("cash") is False
    assert market_worker._execution_enabled("crypto") is False


def test_scanning_persistence_continues_when_execution_disabled(monkeypatch):
    saved = {"signals": 0, "forecasts": 0}
    monkeypatch.setattr(market_worker, "ENABLE_AUTOTRADE", False)
    monkeypatch.setattr(market_worker, "_fast_candidate_batch", lambda market: [("AAPL", "Apple")])
    signal = type("Signal", (), {"symbol": "AAPL", "price": 100.0, "score": 90.0, "confidence": 0.9, "action": "BUY", "reason": "", "to_dict": lambda self: {"symbol": "AAPL"}})()
    monkeypatch.setattr(market_worker, "_fast_discover_symbol", lambda market, symbol, name: (signal, _history(symbol, "5m")))
    monkeypatch.setattr(market_worker, "save_json_signal", lambda *args, **kwargs: saved.__setitem__("signals", saved["signals"] + 1))
    monkeypatch.setattr(market_worker, "save_forecast", lambda *args, **kwargs: saved.__setitem__("forecasts", saved["forecasts"] + 1))
    monkeypatch.setattr(market_worker, "update_prices", lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("mutated")))
    monkeypatch.setattr(market_worker, "risk_exits", lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("risk")))
    monkeypatch.setattr(market_worker, "process_signals", lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("execution")))
    monkeypatch.setattr(market_worker, "snapshot", lambda *args, **kwargs: {})
    actions = market_worker.fast_scan_market("cash")
    assert actions == []
    assert saved == {"signals": 1, "forecasts": 1}


def test_historical_audit_is_non_destructive():
    cutoff = datetime(2026, 1, 2, tzinfo=timezone.utc)
    trades = [{"id": 1, "symbol": "GM", "created_at": "2026-01-01T00:00:00+00:00", "realized_pnl": -12.5}]
    positions = [{"id": 2, "symbol": "F", "opened_at": "2026-01-01T00:00:00+00:00", "quantity": 2, "entry_price": 10, "current_price": 8}]
    report = build_audit_report(trades, positions, cutoff=cutoff)
    assert report.destructive_changes == 0
    assert report.affected_symbols == ["F", "GM"]
    assert report.trades == 1
    assert report.positions == 1


def test_normal_info_logs_are_not_emitted_as_error():
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(StructuredFormatter())
    logger = logging.getLogger("v36-log-test")
    logger.handlers = [handler]
    logger.setLevel(logging.INFO)
    logger.propagate = False
    logger.info("hello", extra={"service": "stock-worker", "market": "cash", "event": "scan"})
    payload = stream.getvalue()
    assert '"severity": "INFO"' in payload
    assert '"severity": "ERROR"' not in payload
