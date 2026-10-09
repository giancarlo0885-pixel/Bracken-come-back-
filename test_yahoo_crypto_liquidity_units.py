"""Provider-denominated liquidity is never multiplied by price twice."""
import math
import pandas as pd

from market_worker import _average_dollar_volume, _quote_payload_from_history


def _history(provider, symbol, close, volume, *, interval="1d", native_symbol=None):
    frame = pd.DataFrame({"Close": [close] * 21, "Volume": [volume] * 21})
    frame.attrs["provider_route"] = {
        "provider": provider,
        "requested_symbol": symbol,
        "provider_symbol": native_symbol or symbol,
        "interval": interval,
        "quote_timestamp": "2026-10-08T20:00:00+00:00",
        "quote_verified": True,
        "price": close,
    }
    return frame


def test_yahoo_btc_quote_volume_is_already_usd_notional():
    frame = _history("Yahoo Finance", "BTC-USD", 82_000.0, 32_000_000_000.0)
    actual = _average_dollar_volume(frame)
    assert math.isclose(actual, 32_000_000_000.0)
    assert actual != 82_000.0 * 32_000_000_000.0
    payload = _quote_payload_from_history("BTC-USD", frame)
    assert math.isclose(payload["avg_dollar_volume"], 32_000_000_000.0)
    assert payload["liquidity_volume_basis"] == "yahoo_crypto_quote_turnover_usd"


def test_yahoo_crypto_intraday_still_treats_quote_notional_as_usd():
    frame = _history("Yahoo", "ETH-USD", 3_000.0, 5_000_000.0, interval="1m")
    assert math.isclose(_average_dollar_volume(frame), 5_000_000.0)


def test_yahoo_stocks_multiply_share_volume_by_price():
    frame = _history("Yahoo Finance", "AAPL", 200.0, 1_000_000.0)
    assert math.isclose(_average_dollar_volume(frame), 200_000_000.0)
    assert _quote_payload_from_history("AAPL", frame)["liquidity_volume_basis"] == "price_times_reported_volume"


def test_base_asset_volume_is_converted_for_non_yahoo_crypto():
    frame = _history("Coinbase", "BTC-USD", 82_000.0, 300.0)
    assert math.isclose(_average_dollar_volume(frame), 24_600_000.0)
    assert _quote_payload_from_history("BTC-USD", frame)["liquidity_volume_basis"] == "price_times_reported_volume"


def test_yahoo_mismatched_symbol_cannot_claim_quote_unit_or_trade_capacity():
    frame = _history("Yahoo Finance", "BTC-USD", 82_000.0, 10.0, native_symbol="ETH-USD")
    assert math.isclose(_average_dollar_volume(frame), 820_000.0)
    payload = _quote_payload_from_history("BTC-USD", frame)
    assert payload["liquidity_volume_basis"] == "price_times_reported_volume"
    assert payload["tradeable"] is False


def test_invalid_or_nonpositive_notional_cannot_qualify_liquidity():
    frame = _history("Yahoo Finance", "BTC-USD", 82_000.0, 0.0)
    assert _average_dollar_volume(frame) is None
    assert _quote_payload_from_history("BTC-USD", frame)["tradeable"] is False
