from __future__ import annotations

import traceback
import unittest
from unittest.mock import patch, Mock

import pandas as pd
import pytest
import requests

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
        self.assertEqual(get.call_args.kwargs["timeout"], 15)
        self.assertTrue(get.call_args.args[0].startswith("https://data.alpaca.markets/v2/stocks/"))
        self.assertEqual(frame.iloc[0].to_dict(), {"Open": 100, "High": 102, "Low": 99, "Close": 101, "Volume": 200})

    @patch("alpaca_iex_provider.requests.get")
    def test_http_error_does_not_leak_secrets(self, get):
        for status_code in (401, 402, 403, 429, 500, 503):
            with self.subTest(status_code=status_code):
                get.return_value = Mock(status_code=status_code, text="fake-test-key fake-test-secret")
                with self.assertRaisesRegex(AlpacaDataError, str(status_code)) as error:
                    iex_stock_history("AAPL", "5d", "5m")
                self.assertEqual(error.exception.status_code, status_code)
                self.assertFalse(error.exception.timed_out)
                self.assertNotIn("fake-test-key", str(error.exception))
                self.assertNotIn("fake-test-secret", str(error.exception))
                self.assertIsNone(error.exception.__context__)
                self.assertFalse(hasattr(error.exception, "response"))
                get.return_value.json.assert_not_called()

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


@pytest.mark.parametrize("failure_type", [requests.Timeout, requests.ConnectTimeout, requests.ReadTimeout, requests.ConnectionError, requests.HTTPError])
@pytest.mark.parametrize("status_code", [None, 401, 429, 503])
def test_request_failure_keeps_typed_status_and_timeout_without_raw_transport(monkeypatch, failure_type, status_code):
    monkeypatch.setenv("APCA_API_KEY_ID", "unit-key")
    monkeypatch.setenv("APCA_API_SECRET_KEY", "unit-secret")
    monkeypatch.setenv("ALPACA_DATA_FEED", "iex")
    monkeypatch.setenv("ALPACA_DATA_URL", "https://data.alpaca.markets")
    request = requests.Request(
        "GET", "https://data.alpaca.markets/v2/stocks/AAPL/bars?token=unit-secret",
        headers={"APCA-API-KEY-ID": "unit-key", "APCA-API-SECRET-KEY": "unit-secret"},
    ).prepare()
    response = None
    if status_code is not None:
        response = requests.Response()
        response.status_code = status_code
        response._content = b"unit-key unit-secret raw-provider-body"
        response.request = request
    failure = failure_type("unit-key unit-secret raw-provider-message", request=request, response=response)
    monkeypatch.setattr("alpaca_iex_provider.requests.get", Mock(side_effect=failure))

    with pytest.raises(AlpacaDataError) as captured:
        iex_stock_history("AAPL", "5d", "5m")

    error = captured.value
    assert error.status_code == status_code
    assert error.timed_out is issubclass(failure_type, requests.Timeout)
    assert vars(error) == {"status_code": status_code, "timed_out": issubclass(failure_type, requests.Timeout)}
    assert error.__context__ is None
    assert error.__cause__ is None
    rendered = "".join(traceback.format_exception(error))
    for private_value in ("unit-key", "unit-secret", "raw-provider-body", "raw-provider-message"):
        assert private_value not in str(error)
        assert private_value not in repr(error)
        assert private_value not in rendered


@pytest.mark.parametrize("failure_type", [ValueError, TypeError, requests.exceptions.JSONDecodeError])
def test_malformed_json_does_not_retain_response_error(monkeypatch, failure_type):
    monkeypatch.setenv("APCA_API_KEY_ID", "unit-key")
    monkeypatch.setenv("APCA_API_SECRET_KEY", "unit-secret")
    monkeypatch.setenv("ALPACA_DATA_FEED", "iex")
    monkeypatch.setenv("ALPACA_DATA_URL", "https://data.alpaca.markets")
    response = Mock(status_code=200)
    if failure_type is requests.exceptions.JSONDecodeError:
        failure = failure_type("unit-secret", "unit-key raw-provider-body", 0)
        failure.response = response
        failure.request = Mock(url="https://data.alpaca.markets?token=unit-secret")
    else:
        failure = failure_type("unit-key unit-secret raw-provider-body")
    response.json.side_effect = failure
    monkeypatch.setattr("alpaca_iex_provider.requests.get", Mock(return_value=response))

    with pytest.raises(AlpacaDataError, match="^Alpaca returned malformed history response$") as captured:
        iex_stock_history("AAPL", "5d", "5m")

    assert captured.value.status_code is None
    assert captured.value.timed_out is False
    assert captured.value.__context__ is None
    assert captured.value.__cause__ is None
    assert not hasattr(captured.value, "response")
    assert not hasattr(captured.value, "request")
    rendered = "".join(traceback.format_exception(captured.value))
    for private_value in ("unit-key", "unit-secret", "raw-provider-body"):
        assert private_value not in rendered


@pytest.mark.parametrize("bad_value", [float("inf"), float("-inf"), float("nan"), None, "invalid"])
def test_nonfinite_or_missing_candle_cannot_leave_a_verified_partial_history(monkeypatch, bad_value):
    monkeypatch.setenv("APCA_API_KEY_ID", "unit-key")
    monkeypatch.setenv("APCA_API_SECRET_KEY", "unit-secret")
    monkeypatch.setenv("ALPACA_DATA_FEED", "iex")
    monkeypatch.setenv("ALPACA_DATA_URL", "https://data.alpaca.markets")
    bars = [
        {"t": "2026-10-13T15:00:00Z", "o": 100, "h": 102, "l": 99, "c": 101, "v": 200},
        {"t": "2026-10-13T15:05:00Z", "o": 100, "h": 102, "l": 99, "c": bad_value, "v": 200},
    ]
    response = Mock(status_code=200)
    response.json.return_value = {"symbol": "MSFT", "bars": bars}
    monkeypatch.setattr("alpaca_iex_provider.requests.get", lambda *args, **kwargs: response)

    with pytest.raises(AlpacaDataError, match="invalid candle values"):
        iex_stock_history("MSFT", "5d", "5m")


def test_invalid_timestamp_cannot_leave_a_verified_partial_history(monkeypatch):
    monkeypatch.setenv("APCA_API_KEY_ID", "unit-key")
    monkeypatch.setenv("APCA_API_SECRET_KEY", "unit-secret")
    monkeypatch.setenv("ALPACA_DATA_FEED", "iex")
    monkeypatch.setenv("ALPACA_DATA_URL", "https://data.alpaca.markets")
    response = Mock(status_code=200)
    response.json.return_value = {"bars": [
        {"t": "2026-10-13T15:00:00Z", "o": 100, "h": 102, "l": 99, "c": 101, "v": 200},
        {"t": "invalid", "o": 100, "h": 102, "l": 99, "c": 101, "v": 200},
    ]}
    monkeypatch.setattr("alpaca_iex_provider.requests.get", lambda *args, **kwargs: response)
    with pytest.raises(AlpacaDataError, match="invalid candle timestamps"):
        iex_stock_history("MSFT", "5d", "5m")


def test_conflicting_response_symbol_is_not_relabelled_to_request(monkeypatch):
    monkeypatch.setenv("APCA_API_KEY_ID", "unit-key")
    monkeypatch.setenv("APCA_API_SECRET_KEY", "unit-secret")
    monkeypatch.setenv("ALPACA_DATA_FEED", "iex")
    monkeypatch.setenv("ALPACA_DATA_URL", "https://data.alpaca.markets")
    response = Mock(status_code=200)
    response.json.return_value = {"symbol": "GOOG", "bars": [
        {"t": "2026-10-13T15:00:00Z", "o": 100, "h": 102, "l": 99, "c": 101, "v": 200},
    ]}
    monkeypatch.setattr("alpaca_iex_provider.requests.get", lambda *args, **kwargs: response)
    with pytest.raises(AlpacaDataError, match="symbol mismatch"):
        iex_stock_history("MSFT", "5d", "5m")


def test_exhausted_pagination_rejects_partial_history(monkeypatch):
    monkeypatch.setenv("APCA_API_KEY_ID", "unit-key")
    monkeypatch.setenv("APCA_API_SECRET_KEY", "unit-secret")
    monkeypatch.setenv("ALPACA_DATA_FEED", "iex")
    monkeypatch.setenv("ALPACA_DATA_URL", "https://data.alpaca.markets")
    response = Mock(status_code=200)
    response.json.return_value = {"symbol": "MSFT", "bars": [
        {"t": "2026-10-13T15:00:00Z", "o": 100, "h": 102, "l": 99, "c": 101, "v": 200},
    ], "next_page_token": "more"}
    get = Mock(return_value=response)
    monkeypatch.setattr("alpaca_iex_provider.requests.get", get)
    with pytest.raises(AlpacaDataError, match="pagination limit"):
        iex_stock_history("MSFT", "1y", "1m")
    assert get.call_count == 5
