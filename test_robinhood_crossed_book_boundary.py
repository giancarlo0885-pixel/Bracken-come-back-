"""Crossed-book diagnostics must be sanitized, observational and paper-only."""
from robinhood_quote_resilience import _sanitized_book_boundary, _invalid_book_reason


def test_boundary_reports_raw_and_normalized_without_unknown_fields():
    quote = {
        "symbol": "BTC-USD", "bid": "68050.00", "ask": "68000.00",
        "timestamp": "2026-10-10T06:30:00Z",
        "access_token": "DO_NOT_LOG", "account_id": "PRIVATE",
    }
    assert _invalid_book_reason(quote) == "CROSSED_BOOK"
    result = _sanitized_book_boundary(quote, "BTC-USD")
    assert result["raw_bid"] == result["normalized_bid"] == "68050.00"
    assert result["raw_ask"] == result["normalized_ask"] == "68000.00"
    assert result["raw_symbol_match"] == "true"
    assert result["source_timestamp_utc"] == "2026-10-10T06:30:00+00:00"
    assert "DO_NOT_LOG" not in str(result)
    assert "PRIVATE" not in str(result)


def test_boundary_exposes_conflicting_price_field_precedence():
    quote = {
        "symbol": "ETH-USD", "bid": "2000", "ask": "2001",
        "bid_price": "2003", "ask_price": "2001",
    }
    result = _sanitized_book_boundary(quote, "ETH-USD")
    assert result["raw_bid"] == "2000"
    assert result["normalized_bid"] == "2003"
    assert result["timestamp_basis"] == "missing"
    assert _invalid_book_reason(quote) == "CROSSED_BOOK"


def test_boundary_rejects_untrusted_numeric_and_naive_time():
    result = _sanitized_book_boundary(
        {"symbol": "BTC-USD", "bid": "NaN", "ask": "-1",
         "timestamp": "2026-10-10T06:30:00"},
        "BTC-USD",
    )
    assert result["raw_bid"] == "invalid"
    assert result["raw_ask"] == "invalid"
    assert result["source_timestamp_utc"] == "invalid"
