from binance_us_execution_compat import (
    BinanceUsWebSocketGuard,
    MY_FILTERS_WEIGHT,
    normalize_execution_report,
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
