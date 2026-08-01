from __future__ import annotations

import json
from datetime import date

from earnings_calendar import (
    format_revenue,
    mobile_card_lines,
    prepare_events,
    table_rows,
    validate_symbol,
)


def _record(symbol: str, day: str, **extra):
    payload = {
        "symbol": symbol,
        "name": f"{symbol} Corp",
        "date": day,
        "quarter": 2,
        "hour": "amc",
        "epsEstimate": 1.23456,
        "epsActual": 1.4,
        "revenueEstimate": 2_400_000,
        "revenueActual": 2_700_000,
        "provider": "Finnhub",
    }
    payload.update(extra)
    return {
        "category": "Earnings Calendar",
        "provider": payload.get("provider", "Finnhub"),
        "symbol": symbol,
        "details": json.dumps(payload),
        "event_time": day,
    }


def test_duplicate_earnings_records_are_removed():
    prepared = prepare_events(
        [_record("AAPL", "2026-08-02"), _record("AAPL", "2026-08-02")],
        today=date(2026, 8, 1),
    )
    assert [event["symbol"] for event in prepared["main"]] == ["AAPL"]


def test_missing_hour_becomes_time_not_supplied():
    prepared = prepare_events([_record("MSFT", "2026-08-02", hour="")], today=date(2026, 8, 1))
    assert prepared["main"][0]["hour"] == "Time not supplied"


def test_null_eps_and_revenue_values_are_hidden_from_table_rows():
    prepared = prepare_events(
        [
            _record(
                "NVDA",
                "2026-08-02",
                epsEstimate=None,
                epsActual=None,
                revenueEstimate=None,
                revenueActual=None,
            )
        ],
        today=date(2026, 8, 1),
    )
    row = table_rows(prepared["main"])[0]
    assert "EPS Est." not in row
    assert "EPS Actual" not in row
    assert "Rev. Est." not in row
    assert "Rev. Actual" not in row


def test_invalid_zero_revenue_estimate_is_not_displayed_as_zero():
    prepared = prepare_events([_record("JPM", "2026-08-02", revenueEstimate=0)], today=date(2026, 8, 1))
    row = table_rows(prepared["main"])[0]
    assert row.get("Rev. Est.") != "$0"
    assert "Rev. Est." not in row


def test_nearest_date_sorting_wins_before_priority():
    prepared = prepare_events(
        [
            _record("MSFT", "2026-08-05"),
            _record("AAPL", "2026-08-02"),
        ],
        held_symbols={"MSFT"},
        today=date(2026, 8, 1),
    )
    assert [event["symbol"] for event in prepared["main"]] == ["AAPL", "MSFT"]


def test_currency_abbreviation():
    assert format_revenue(357_000) == "$357K"
    assert format_revenue(2_400_000) == "$2.4M"
    assert format_revenue(1_700_000_000) == "$1.7B"


def test_symbol_validation_fallback_marks_incomplete():
    symbol, ok, reason = validate_symbol("bad ticker")
    assert symbol == "BAD TICKER"
    assert ok is False
    assert "invalid" in reason
    prepared = prepare_events([_record("bad ticker", "2026-08-02")], today=date(2026, 8, 1))
    assert prepared["main"] == []
    assert prepared["incomplete"][0]["status"] == "Incomplete"


def test_mobile_card_output_is_readable_without_raw_json():
    prepared = prepare_events([_record("AAPL", "2026-08-02")], today=date(2026, 8, 1))
    lines = mobile_card_lines(prepared["main"][0])
    text = "\n".join(lines)
    assert "AAPL" in text
    assert "After market" in text
    assert "{" not in text
    assert "raw_payload" not in text


def test_raw_json_not_in_normal_dashboard_rows():
    prepared = prepare_events([_record("AAPL", "2026-08-02")], today=date(2026, 8, 1))
    rows = table_rows(prepared["main"])
    rendered = json.dumps(rows)
    assert "raw_payload" not in rendered
    assert "epsEstimate" not in rendered
