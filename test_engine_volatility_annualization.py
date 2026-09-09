import pandas as pd

from engine import _bars_per_year


def _history(interval: str) -> pd.DataFrame:
    frame = pd.DataFrame({"Close": [100.0, 101.0]})
    frame.attrs["provider_route"] = {"interval": interval}
    return frame


def test_crypto_5m_uses_247_calendar():
    assert _bars_per_year("BTC-USD", _history("5m")) == 365.0 * 24.0 * 12.0


def test_crypto_1h_uses_247_calendar():
    assert _bars_per_year("ETH-USD", _history("1h")) == 365.0 * 24.0


def test_crypto_daily_uses_365_days():
    assert _bars_per_year("SOL-USD", _history("1d")) == 365.0


def test_equity_daily_preserves_252_sessions():
    assert _bars_per_year("AAPL", _history("1d")) == 252.0


def test_equity_5m_uses_regular_session_bars():
    assert _bars_per_year("AAPL", _history("5m")) == 252.0 * 78.0
