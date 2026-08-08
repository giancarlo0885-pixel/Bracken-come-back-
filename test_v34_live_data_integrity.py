from __future__ import annotations

from datetime import datetime, timedelta, timezone

from dashboard_helpers import format_asset_price, symbol_currency
from prediction_engine import build_decisions


def _op(symbol: str, score: float = 90.0, payload: dict | None = None, market: str = "cash") -> dict:
    return {
        "market": market,
        "symbol": symbol,
        "opportunity_score": score,
        "payload": payload or {"action": "BUY", "confidence": 90},
        "created_at": datetime.now(timezone.utc).isoformat(),
    }


def test_missing_price_is_never_a_buy() -> None:
    decisions = build_decisions([_op("ACVA")], [], [], 10)
    assert decisions[0]["action"] == "WAIT"
    assert decisions[0]["trade_eligible"] is False
    assert "live market price" in decisions[0]["data_status"]


def test_missing_target_is_never_a_buy() -> None:
    signals = [{"market": "cash", "symbol": "ABT", "price": 100, "action": "BUY", "confidence": .9, "created_at": datetime.now(timezone.utc).isoformat()}]
    decisions = build_decisions([_op("ABT")], signals, [], 10)
    assert decisions[0]["action"] == "WAIT"
    assert "forecast target" in decisions[0]["data_status"]


def test_small_expected_move_is_downgraded() -> None:
    now = datetime.now(timezone.utc).isoformat()
    signals = [{"market": "cash", "symbol": "7203.T", "price": 3224, "action": "BUY", "confidence": .9, "created_at": now, "source_interval": "1d", "scan_type": "deep", "source_quote_timestamp": now}]
    forecasts = [{"market": "cash", "symbol": "7203.T", "requested_symbol": "7203.T", "provider_symbol": "7203.T", "source_interval": "1d", "scan_type": "deep", "source_quote_timestamp": now, "target_price": 3238.24, "created_at": now}]
    decisions = build_decisions([_op("7203.T")], signals, forecasts, 10)
    assert decisions[0]["action"] == "WAIT"
    assert "trade threshold" in decisions[0]["data_status"]


def test_fresh_price_and_target_can_remain_buy() -> None:
    now = datetime.now(timezone.utc).isoformat()
    signals = [{"market": "cash", "symbol": "AAPL", "price": 200, "action": "BUY", "confidence": .9, "created_at": now, "source_interval": "1d", "scan_type": "deep", "source_quote_timestamp": now}]
    forecasts = [{"market": "cash", "symbol": "AAPL", "requested_symbol": "AAPL", "provider_symbol": "AAPL", "source_interval": "1d", "scan_type": "deep", "source_quote_timestamp": now, "target_price": 205, "created_at": now}]
    decisions = build_decisions([_op("AAPL")], signals, forecasts, 10)
    assert decisions[0]["action"] == "BUY"
    assert decisions[0]["trade_eligible"] is True


def test_missing_signal_timestamp_is_never_trade_ready() -> None:
    now = datetime.now(timezone.utc).isoformat()
    signals = [{"market": "cash", "symbol": "AAPL", "price": 200, "action": "BUY", "confidence": .9}]
    forecasts = [{"market": "cash", "symbol": "AAPL", "target_price": 205, "created_at": now}]
    decisions = build_decisions([_op("AAPL")], signals, forecasts, 10)
    assert decisions[0]["action"] == "WAIT"
    assert decisions[0]["trade_eligible"] is False
    assert "signal timestamp" in decisions[0]["data_status"]


def test_missing_forecast_timestamp_is_never_trade_ready() -> None:
    now = datetime.now(timezone.utc).isoformat()
    signals = [{"market": "cash", "symbol": "AAPL", "price": 200, "action": "BUY", "confidence": .9, "created_at": now, "source_interval": "1d", "scan_type": "deep", "source_quote_timestamp": now}]
    forecasts = [{"market": "cash", "symbol": "AAPL", "requested_symbol": "AAPL", "provider_symbol": "AAPL", "source_interval": "1d", "scan_type": "deep", "source_quote_timestamp": now, "target_price": 205}]
    decisions = build_decisions([_op("AAPL")], signals, forecasts, 10)
    assert decisions[0]["action"] == "WAIT"
    assert decisions[0]["trade_eligible"] is False
    assert "forecast timestamp" in decisions[0]["data_status"]


def test_fresh_signal_does_not_make_stale_forecast_trade_ready() -> None:
    now = datetime.now(timezone.utc)
    quote_time = now.isoformat()
    signals = [{"market": "cash", "symbol": "AAPL", "price": 200, "action": "BUY", "confidence": .9, "created_at": quote_time, "source_interval": "1d", "scan_type": "deep", "source_quote_timestamp": quote_time}]
    forecasts = [{"market": "cash", "symbol": "AAPL", "requested_symbol": "AAPL", "provider_symbol": "AAPL", "source_interval": "1d", "scan_type": "deep", "source_quote_timestamp": quote_time, "target_price": 205, "created_at": (now - timedelta(hours=8)).isoformat()}]
    decisions = build_decisions([_op("AAPL")], signals, forecasts, 10)
    assert decisions[0]["action"] == "WAIT"
    assert decisions[0]["trade_eligible"] is False
    assert "forecast is stale" in decisions[0]["data_status"].lower()


def test_stale_signal_is_downgraded() -> None:
    old = (datetime.now(timezone.utc) - timedelta(hours=8)).isoformat()
    now = datetime.now(timezone.utc).isoformat()
    signals = [{"market": "cash", "symbol": "AAPL", "price": 200, "action": "BUY", "confidence": .9, "created_at": old}]
    forecasts = [{"market": "cash", "symbol": "AAPL", "target_price": 205, "created_at": now}]
    decisions = build_decisions([_op("AAPL")], signals, forecasts, 10)
    assert decisions[0]["action"] == "WAIT"
    assert "stale" in decisions[0]["data_status"].lower()


def test_global_currency_labels_are_not_usd() -> None:
    assert symbol_currency("7203.T", "cash") == "JPY"
    assert symbol_currency("TCS.NS", "cash") == "INR"
    assert format_asset_price(3224, "7203.T", "cash") == "3,224.00 JPY"


def test_execution_forecast_gate_rejects_missing_forecast(monkeypatch) -> None:
    import oracle_bot

    monkeypatch.setattr(oracle_bot, "row", lambda *args, **kwargs: None)
    approved, reason = oracle_bot._entry_forecast_gate("cash", "AAPL", 200.0)
    assert approved is False
    assert "signal_id" in reason


def test_execution_forecast_gate_accepts_fresh_edge(monkeypatch) -> None:
    import oracle_bot

    quote_time = datetime.now(timezone.utc).isoformat()
    monkeypatch.setattr(
        oracle_bot,
        "row",
        lambda *args, **kwargs: {
            "signal_id": 123,
            "symbol": "AAPL",
            "target_price": 205.0,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "requested_symbol": "AAPL",
            "provider_symbol": "AAPL",
            "source_interval": "1d",
            "source_quote_timestamp": quote_time,
            "scan_type": "deep",
            "model": "log-return diffusion",
            "model_version": "unit",
            "data_quality_score": 90,
            "forecast_id": "fc-123",
        },
    )
    monkeypatch.setattr(oracle_bot, "model_execution_approved", lambda *args, **kwargs: (True, "approved"))
    approved, reason = oracle_bot._entry_forecast_gate(
        "cash",
        "AAPL",
        200.0,
        {"symbol": "AAPL", "signal_id": 123, "scan_type": "deep", "source_interval": "1d"},
        {"quote_timestamp": quote_time, "interval": "1d"},
    )
    assert approved is True
    assert "forecast approved" in reason
