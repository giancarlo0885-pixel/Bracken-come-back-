from __future__ import annotations

import unittest
from unittest.mock import patch, Mock

import pandas as pd

from alpaca_iex_provider import AlpacaDataError, iex_stock_history


class AlpacaIEXProviderTests(unittest.TestCase):
    def setUp(self):
        self.env = patch.dict("os.environ", {
            "APCA_API_KEY_ID": "fake-test-key",
            "APCA_API_SECRET_KEY": "fake-test-secret",
            "ALPACA_DATA_FEED": "iex",
            "ALPACA_DATA_URL": "https://data.alpaca.markets",
        })
        self.env.start()
        self.addCleanup(self.env.stop)

    @patch("alpaca_iex_provider.requests.get")
    def test_valid_bars(self, get):
        get.return_value = Mock(status_code=200)
        get.return_value.json.return_value = {
            "bars": [{"t": "2026-10-07T15:00:00Z", "o": 100, "h": 102, "l": 99, "c": 101, "v": 200}],
            "next_page_token": None,
        }
        frame = iex_stock_history("AAPL", "5d", "5m")
        self.assertEqual(len(frame), 1)
        self.assertEqual(frame.attrs["feed"], "iex")
        self.assertEqual(frame.attrs["provider_symbol"], "AAPL")
        self.assertEqual(frame.index.tz, pd.Timestamp.now(tz="UTC").tz)
        self.assertEqual(get.call_args.kwargs["params"]["feed"], "iex")
        self.assertTrue(get.call_args.args[0].startswith("https://data.alpaca.markets/v2/stocks/"))

    @patch("alpaca_iex_provider.requests.get")
    def test_http_error_does_not_leak_secrets(self, get):
        get.return_value = Mock(status_code=429)
        with self.assertRaisesRegex(AlpacaDataError, "429") as error:
            iex_stock_history("AAPL", "5d", "5m")
        self.assertNotIn("fake-test-secret", str(error.exception))

    @patch("alpaca_iex_provider.requests.get")
    def test_invalid_ohlc_rejected(self, get):
        get.return_value = Mock(status_code=200)
        get.return_value.json.return_value = {
            "bars": [{"t": "2026-10-07T15:00:00Z", "o": 100, "h": 98, "l": 99, "c": 101, "v": 200}],
            "next_page_token": None,
        }
        with self.assertRaisesRegex(AlpacaDataError, "inconsistent"):
            iex_stock_history("AAPL", "5d", "5m")

    @patch("alpaca_iex_provider.requests.get")
    def test_pagination(self, get):
        first = Mock(status_code=200)
        first.json.return_value = {"bars": [{"t": "2026-10-07T15:00:00Z", "o": 100, "h": 102, "l": 99, "c": 101, "v": 200}], "next_page_token": "next"}
        second = Mock(status_code=200)
        second.json.return_value = {"bars": [{"t": "2026-10-07T15:05:00Z", "o": 101, "h": 103, "l": 100, "c": 102, "v": 300}], "next_page_token": None}
        get.side_effect = [first, second]
        self.assertEqual(len(iex_stock_history("AAPL", "5d", "5m")), 2)
        self.assertEqual(get.call_count, 2)


if __name__ == "__main__":
    unittest.main()


def test_router_reports_read_only_failure_without_credentials(monkeypatch, caplog):
    import logging
    import pytest
    import provider_router
    def failing(*args):
        raise AlpacaDataError("Alpaca market-data HTTP 401")
    monkeypatch.setattr(provider_router, "iex_stock_history", failing)
    caplog.set_level(logging.WARNING, logger=provider_router.log.name)
    with pytest.raises(AlpacaDataError):
        provider_router._alpaca_iex("AMD", "5d", "5m", "secret-key")
    assert "symbol=AMD | period=5d | interval=5m" in caplog.text
    assert "HTTP 401" in caplog.text
    assert "secret-key" not in caplog.text
