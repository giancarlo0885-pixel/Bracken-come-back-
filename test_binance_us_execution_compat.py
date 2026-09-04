import hashlib
import hmac
from urllib.parse import urlencode

from binance_us_execution_compat import (
    BinanceUsWebSocketGuard,
    MY_FILTERS_WEIGHT,
    classify_binance_us_error,
    normalize_execution_report,
    percent_encoded_payload,
    sign_percent_encoded_payload,
    trading_symbol_params,
    validate_2026_order_filters,
)


def test_my_filters_weight_is_40():
    assert MY_FILTERS_WEIGHT == 40


def test_max_asset_base_quantity_is_enforced():
    result = validate_2026_order_filters(
        base_asset="BTC",
        quote_asset="USD",
        quantity="1.1",
        price="50000",
        my_filters={
            "assetFilters": [
                {
                    "filterType": "MAX_ASSET",
                    "asset": "BTC",
                    "limit": "1.0",
                }
            ]
        },
    )
    assert result["ok"] is False
    assert result["reason"] == "BINANCE_US_MAX_ASSET_EXCEEDED"


def test_max_asset_quote_notional_is_enforced():
    result = validate_2026_order_filters(
        base_asset="BTC",
        quote_asset="USD",
        quantity="2",
        price="600000",
        my_filters={
            "assetFilters": [
                {
                    "filterType": "MAX_ASSET",
                    "asset": "USD",
                    "limit": "1000000",
                }
            ]
        },
    )
    assert result["ok"] is False
    assert result["transacted"] == "1200000"


def test_order_list_limit_is_enforced():
    result = validate_2026_order_filters(
        base_asset="BTC",
        quote_asset="USD",
        quantity="0.1",
        price="60000",
        my_filters={
            "symbolFilters": [
                {
                    "filterType": "MAX_NUM_ORDER_LISTS",
                    "maxNumOrderLists": 20,
                }
            ]
        },
        open_order_lists=20,
        is_order_list=True,
    )
    assert result["ok"] is False
    assert result["reason"] == "BINANCE_US_MAX_NUM_ORDER_LISTS_EXCEEDED"


def test_expiry_reason_is_classified_terminal():
    result = normalize_execution_report(
        {
            "e": "executionReport",
            "x": "EXPIRED",
            "X": "EXPIRED",
            "s": "BTCUSD",
            "i": 7,
            "eR": "EXPIRE_TAKER",
        }
    )
    assert result is not None
    assert result["expired"] is True
    assert result["terminal"] is True
    assert result["expiry_reason"] == "EXPIRE_TAKER"


def test_websocket_ping_immediately_pongs_and_shutdown_reconnects():
    now = {"v": 0.0}
    sent = []
    guard = BinanceUsWebSocketGuard(clock=lambda: now["v"])
    guard.on_open()
    now["v"] = 20.0
    guard.on_ping(sent.append, b"abc")
    assert sent == [b"abc"]
    assert guard.heartbeat_ok() is True
    assert guard.on_text_event({"e": "serverShutdown"}) is True
    assert guard.reconnect_required is True


def test_signing_percent_encodes_before_hmac():
    params = [
        ("symbol", "BTCUSD"),
        ("clientTag", "oracle alpha/beta+1"),
        ("timestamp", 1700000000000),
    ]
    encoded = percent_encoded_payload(params)
    assert encoded == urlencode(params)
    assert "oracle+alpha%2Fbeta%2B1" in encoded

    payload, signature = sign_percent_encoded_payload(params, "super-secret")
    expected = hmac.new(
        b"super-secret",
        encoded.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    assert payload == encoded
    assert signature == expected


def test_new_2026_errors_are_fail_closed():
    cases = {
        -1022: "BINANCE_US_INVALID_SIGNATURE",
        -1151: "BINANCE_US_DUPLICATE_SYMBOL",
        -1220: "BINANCE_US_SYMBOL_STATUS_MISMATCH",
        -2039: "BINANCE_US_ORDER_IDENTIFIER_MISMATCH",
    }
    for code, reason in cases.items():
        result = classify_binance_us_error({"code": code, "msg": "test"})
        assert result["reason"] == reason
        assert result["retryable"] is False


def test_trading_symbol_params_fail_closed_on_halt_or_break():
    params = trading_symbol_params("btcusd", limit=100)
    assert params == {"symbol": "BTCUSD", "symbolStatus": "TRADING", "limit": 100}


class _SignedResponse:
    status_code = 200
    headers = {}

    def raise_for_status(self):
        return None

    def json(self):
        return {
            "assetFilters": [],
            "symbolFilters": [],
            "exchangeFilters": [],
        }


class _SignedSession:
    def __init__(self):
        self.params = None
        self.headers = None

    def get(self, url, *, params, headers, timeout):
        self.params = dict(params)
        self.headers = dict(headers)
        return _SignedResponse()


def test_signed_my_filters_uses_weight_40_and_does_not_send_secret():
    import binance_us_execution_compat as compat

    session = _SignedSession()
    weights = []
    result = compat.signed_my_filters(
        api_key="public-key",
        secret_key="super-secret",
        symbol="btcusd",
        session=session,
        timestamp_ms=1700000000000,
        acquire_weight=weights.append,
    )
    assert result["assetFilters"] == []
    assert weights == [40]
    assert session.params["symbol"] == "BTCUSD"
    assert "signature" in session.params
    assert "super-secret" not in str(session.params)
    assert session.headers["X-MBX-APIKEY"] == "public-key"
