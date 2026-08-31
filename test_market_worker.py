from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

import pandas as pd
import pytest


def _history(symbol="AAPL", price=123.45, interval="5m", quote_timestamp=None, verified=True, requested_symbol=None, provider_symbol=None):
    quote_timestamp = quote_timestamp if quote_timestamp is not None else datetime.now(timezone.utc).isoformat()
    requested_symbol = requested_symbol if requested_symbol is not None else symbol
    provider_symbol = provider_symbol if provider_symbol is not None else symbol
    frame = pd.DataFrame(
        {
            "Open": [price - 2, price - 1, price],
            "High": [price, price + 1, price + 2],
            "Low": [price - 3, price - 2, price - 1],
            "Close": [price - 1, price - 0.5, price],
            "Volume": [1_000_000, 1_100_000, 1_200_000],
        },
        index=pd.date_range(datetime.now(timezone.utc) - timedelta(minutes=10), periods=3, freq="5min"),
    )
    frame.attrs["provider_route"] = {
        "requested_symbol": requested_symbol,
        "provider_symbol": provider_symbol,
        "provider": "unit-provider",
        "price": price,
        "bid": price - 0.02,
        "ask": price + 0.02,
        "spread_pct": 0.0003,
        "current_price": price,
        "quote_timestamp": quote_timestamp,
        "interval": interval,
        "quote_verified": verified,
        "source_capability": "unit_verified_quote",
        "correlation_id": f"corr-{symbol}-{interval}",
        "source_identity": f"unit:{symbol}:5d:{interval}",
        "cache_identity": f"cache:{symbol}:5d:{interval}",
        "ohlcv_fingerprint": f"ohlcv:{symbol}",
    }
    return frame


def _handoff_records(records):
    return [r for r in records if "EXECUTION_QUOTE_HANDOFF" in r.getMessage()]


def _handoff_fields(message: str) -> dict[str, str]:
    parts = [part.strip() for part in message.split("|")]
    assert parts[0] == "EXECUTION_QUOTE_HANDOFF"
    fields: dict[str, str] = {}
    for part in parts[1:]:
        key, value = part.split("=", 1)
        fields[key.strip()] = value.strip()
    return fields


def test_execution_eligible_quote_logs_handoff_at_info(caplog):
    import market_worker

    history = _history("AAPL", 123.45, interval="5m", verified=True)

    with caplog.at_level(logging.DEBUG, logger="market-worker"):
        payload = market_worker._execution_quote_payload_from_history("AAPL", history, 123.45, scan_type="fast")

    assert payload is not None
    assert payload["quote_verified"] is True
    assert payload["stale"] is False

    handoff = _handoff_records(caplog.records)
    assert len(handoff) == 1
    assert handoff[0].levelno == logging.INFO


def test_unverified_quote_logs_handoff_at_debug(caplog):
    import market_worker

    history = _history("AAPL", 123.45, interval="5m", verified=False)

    with caplog.at_level(logging.DEBUG, logger="market-worker"):
        payload = market_worker._execution_quote_payload_from_history("AAPL", history, 123.45, scan_type="fast")

    assert payload is not None
    assert payload["quote_verified"] is False

    handoff = _handoff_records(caplog.records)
    assert len(handoff) == 1
    assert handoff[0].levelno == logging.DEBUG


def test_stale_quote_logs_handoff_at_debug(caplog):
    import market_worker

    stale_timestamp = (datetime.now(timezone.utc) - timedelta(days=5)).isoformat()
    history = _history("AAPL", 123.45, interval="5m", verified=True, quote_timestamp=stale_timestamp)

    with caplog.at_level(logging.DEBUG, logger="market-worker"):
        payload = market_worker._execution_quote_payload_from_history("AAPL", history, 123.45, scan_type="fast")

    assert payload is not None
    assert payload["stale"] is True

    handoff = _handoff_records(caplog.records)
    assert len(handoff) == 1
    assert handoff[0].levelno == logging.DEBUG


def test_zero_price_rejects_before_handoff_log(caplog):
    import market_worker

    history = _history("AAPL", 123.45, interval="5m")
    history.attrs["provider_route"].pop("price")
    history.attrs["provider_route"].pop("current_price")
    history.attrs["provider_route"]["bid"] = None
    history.attrs["provider_route"]["ask"] = None
    history["Close"] = [float("nan"), float("nan"), float("nan")]

    with caplog.at_level(logging.DEBUG, logger="market-worker"):
        payload = market_worker._execution_quote_payload_from_history("AAPL", history, None, scan_type="fast")

    assert payload is None
    assert _handoff_records(caplog.records) == []


def test_missing_quote_timestamp_rejects_before_handoff_log(caplog):
    import market_worker

    history = _history("AAPL", 123.45, interval="5m", quote_timestamp="")

    with caplog.at_level(logging.DEBUG, logger="market-worker"):
        payload = market_worker._execution_quote_payload_from_history("AAPL", history, 123.45, scan_type="fast")

    assert payload is None
    assert _handoff_records(caplog.records) == []


def test_handoff_message_schema_matches_between_info_and_debug(caplog):
    import market_worker

    quote_time = datetime.now(timezone.utc)
    eligible_history = _history("AAPL", 123.45, interval="5m", quote_timestamp=quote_time.isoformat(), verified=True)
    with caplog.at_level(logging.DEBUG, logger="market-worker"):
        market_worker._execution_quote_payload_from_history("AAPL", eligible_history, 123.45, scan_type="fast")
    info_record = _handoff_records(caplog.records)[0]
    info_fields = _handoff_fields(info_record.getMessage())
    caplog.clear()

    unverified_history = _history("AAPL", 123.45, interval="5m", quote_timestamp=(quote_time - timedelta(minutes=30)).isoformat(), verified=False)
    with caplog.at_level(logging.DEBUG, logger="market-worker"):
        market_worker._execution_quote_payload_from_history("AAPL", unverified_history, 123.45, scan_type="fast")
    debug_record = _handoff_records(caplog.records)[0]
    debug_fields = _handoff_fields(debug_record.getMessage())

    assert info_record.levelno == logging.INFO
    assert debug_record.levelno == logging.DEBUG
    assert set(info_fields) == set(debug_fields)

    for field in ("symbol", "market", "price", "bid", "ask", "provider", "spread_pct", "capability", "correlation_id"):
        assert info_fields[field] == debug_fields[field]

    assert info_fields["verified"] == "True"
    assert info_fields["stale"] == "False"
    assert debug_fields["verified"] == "False"
    assert debug_fields["stale"] == "True"
    assert info_fields["timestamp"] != debug_fields["timestamp"]
