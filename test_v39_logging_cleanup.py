from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone

import pandas as pd
import pytest


def _history(symbol="AAPL", price=123.45, interval="5m", quote_timestamp=None, verified=True, identity_mismatch=False):
    quote_timestamp = quote_timestamp or datetime.now(timezone.utc).isoformat()
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
        "requested_symbol": symbol,
        "provider_symbol": symbol if not identity_mismatch else f"{symbol}X",
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


def test_rejected_candidate_logs_at_debug_not_info(caplog):
    import market_worker

    market_worker._REJECTION_LOGGED_AT.clear()
    with caplog.at_level(logging.DEBUG, logger="market-worker"):
        market_worker._v39_log_rejection("AAPL", "ZERO_PRICE", {"scan_type": "fast"})

    rejection_records = [r for r in caplog.records if "execution_candidate_rejected" in r.getMessage()]
    info_records = [r for r in rejection_records if r.levelno == logging.INFO]
    debug_records = [r for r in rejection_records if r.levelno == logging.DEBUG]

    assert debug_records
    assert not info_records


def test_execution_eligible_quote_handoff_logs_at_info(caplog):
    import market_worker

    history = _history("AAPL", 123.45, verified=True)
    with caplog.at_level(logging.DEBUG, logger="market-worker"):
        payload = market_worker._execution_quote_payload_from_history("AAPL", history, scan_type="fast")

    assert payload is not None
    handoff_records = [r for r in caplog.records if "EXECUTION_QUOTE_HANDOFF" in r.getMessage()]
    assert handoff_records
    assert any(r.levelno == logging.INFO for r in handoff_records)


def test_unverified_quote_handoff_logs_at_debug_not_info(caplog):
    import market_worker

    history = _history("AAPL", 123.45, verified=False)
    with caplog.at_level(logging.DEBUG, logger="market-worker"):
        payload = market_worker._execution_quote_payload_from_history("AAPL", history, scan_type="fast")

    assert payload is not None
    handoff_records = [r for r in caplog.records if "EXECUTION_QUOTE_HANDOFF" in r.getMessage()]
    assert handoff_records
    assert all(r.levelno == logging.DEBUG for r in handoff_records)
    assert not any(r.levelno == logging.INFO for r in handoff_records)


def test_identity_mismatch_quote_handoff_logs_at_debug(caplog):
    import market_worker

    history = _history("AAPL", 123.45, verified=True, identity_mismatch=True)
    with caplog.at_level(logging.DEBUG, logger="market-worker"):
        payload = market_worker._execution_quote_payload_from_history("AAPL", history, scan_type="fast")

    assert payload is not None
    handoff_records = [r for r in caplog.records if "EXECUTION_QUOTE_HANDOFF" in r.getMessage()]
    assert handoff_records
    assert all(r.levelno == logging.DEBUG for r in handoff_records)


def test_no_qualified_trade_completion_message_is_debug():
    import market_worker

    message = market_worker._build_completion_message("Stock Market", [])
    assert "No new trade met every rule" in message


def test_completion_message_with_actions_is_distinct_from_empty_message():
    import market_worker

    actions = [{"action": "BUY", "symbol": "AAPL", "quantity": 1, "price": 100}]
    message = market_worker._build_completion_message("Stock Market", actions)
    assert "No new trade met every rule" not in message
    assert "BUY AAPL" in message
