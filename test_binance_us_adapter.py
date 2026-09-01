from __future__ import annotations

import binance_us_adapter as adapter


def test_permission_sets_are_and_across_groups_or_within_groups():
    permission_sets = [["SPOT"], ["TRD_GRP_004", "TRD_GRP_005"]]
    assert adapter.permission_sets_satisfied(
        permission_sets,
        {"SPOT", "TRD_GRP_005"},
    )
    assert not adapter.permission_sets_satisfied(permission_sets, {"SPOT"})


def test_parse_symbol_rules_ignores_legacy_permissions():
    parsed = adapter.parse_symbol_rules(
        {
            "symbol": "BTCUSD",
            "baseAsset": "BTC",
            "quoteAsset": "USD",
            "status": "TRADING",
            "isSpotTradingAllowed": True,
            "permissions": ["SPOT"],
            "permissionSets": [["SPOT"], ["TRD_GRP_004"]],
            "filters": [],
        },
        {"SPOT"},
    )
    assert parsed["legacy_permissions_ignored"] is True
    assert parsed["tradable"] is False
    assert parsed["reason"] == "BINANCE_US_PERMISSION_SETS_UNSATISFIED"


def test_request_weights_match_august_31_2026_schedule():
    assert adapter.request_weight("/api/v3/exchangeInfo") == 20
    assert adapter.request_weight("/api/v3/ticker/bookTicker", {"symbol": "BTCUSD"}) == 1
    assert adapter.request_weight("/api/v3/ticker/bookTicker", {}) == 2
    assert adapter.request_weight("/api/v3/depth", {"limit": 100}) == 5
    assert adapter.request_weight("/api/v3/depth", {"limit": 500}) == 25
    assert adapter.request_weight("/api/v3/depth", {"limit": 1000}) == 50
    assert adapter.request_weight("/api/v3/depth", {"limit": 5000}) == 250
    assert adapter.request_weight("/api/v3/trades") == 25
    assert adapter.request_weight("/api/v3/historicalTrades") == 25
    assert adapter.request_weight("/api/v3/aggTrades") == 4
    assert adapter.request_weight("/api/v3/myTrades", {}) == 20
    assert adapter.request_weight("/api/v3/myTrades", {"orderId": 12}) == 5


def test_token_bucket_waits_until_weight_refills():
    state = {"now": 0.0}

    def clock():
        return state["now"]

    def sleeper(seconds):
        state["now"] += seconds

    bucket = adapter.BinanceUsTokenBucket(
        capacity=10,
        refill_per_second=2,
        clock=clock,
        sleeper=sleeper,
    )
    bucket.acquire(10)
    bucket.acquire(4)
    assert state["now"] == 2.0
    assert bucket.available_tokens == 0.0


def test_trade_prevention_parser_uses_new_fields_not_legacy_fields():
    parsed = adapter.parse_trade_prevention_execution_report(
        {
            "e": "executionReport",
            "x": "TRADE_PREVENTION",
            "s": "BTCUSD",
            "i": 123,
            "X": "EXPIRED",
            "E": 1700000000000,
            "v": 77,
            "U": 88,
            "u": 99,
            "l": "0",
            "L": "0",
            "Y": "0",
            "pl": "0.125",
            "pL": "62000.25",
            "pY": "7750.03125",
            "eR": "NONE",
        }
    )
    assert parsed is not None
    assert parsed["prevented_quantity"] == "0.125"
    assert parsed["prevented_price"] == "62000.25"
    assert parsed["prevented_notional"] == "7750.03125"


def test_server_shutdown_requests_immediate_reconnect():
    parsed = adapter.websocket_control_event({"e": "serverShutdown"})
    assert parsed["reconnect_required"] is True
    assert parsed["reason"] == "BINANCE_US_SERVER_SHUTDOWN"


class _Response:
    status_code = 200
    headers = {"X-MBX-USED-WEIGHT-1M": "20"}

    def raise_for_status(self):
        return None

    def json(self):
        return {
            "symbols": [
                {
                    "symbol": "BTCUSD",
                    "baseAsset": "BTC",
                    "quoteAsset": "USD",
                    "status": "TRADING",
                    "isSpotTradingAllowed": True,
                    "permissions": [],
                    "permissionSets": [["SPOT"]],
                    "filters": [],
                }
            ]
        }


class _Session:
    def __init__(self):
        self.last_params = None

    def get(self, url, *, params, headers, timeout):
        self.last_params = dict(params)
        return _Response()


def test_exchange_info_explicitly_requests_permission_sets():
    session = _Session()
    client = adapter.BinanceUsClient(session=session)
    payload = client.exchange_info("BTCUSD")
    assert payload["symbols"][0]["permissionSets"] == [["SPOT"]]
    assert session.last_params["showPermissionSets"] == "true"


def test_installer_falls_back_only_for_coinbase_availability_failures(monkeypatch):
    import crypto_execution_guard as guard
    import crypto_quote_readiness_sampler as sampler

    original_validation = guard._coinbase_reference_validation
    original_record = guard._quote_verification_record
    original_sampler_validation = sampler._coinbase_reference_validation
    original_sampler_record = sampler._quote_verification_record
    original_marker = getattr(
        guard,
        "_binance_us_reference_fallback_installed",
        False,
    )

    try:
        guard._binance_us_reference_fallback_installed = False

        def unavailable(symbol, price):
            return {"ok": False, "reason": "COINBASE_REFERENCE_UNAVAILABLE"}

        guard._coinbase_reference_validation = unavailable
        sampler._coinbase_reference_validation = unavailable

        calls = []

        def binance_success(symbol, price, **kwargs):
            calls.append((symbol, price))
            return {
                "ok": True,
                "reason": "BINANCE_US_REFERENCE_CONFIRMED",
                "reference_provider": "Binance.US",
                "reference_price": 100.0,
                "reference_timestamp": "2026-09-01T12:00:00+00:00",
                "spread_pct": 0.1,
                "difference_pct": 0.1,
            }

        monkeypatch.setattr(adapter, "validate_binance_us_reference", binance_success)
        adapter.install_binance_us_reference_fallback()

        result = guard._coinbase_reference_validation("BTC-USD", 100.1)
        assert result["ok"] is True
        assert result["reference_provider"] == "Binance.US"
        assert calls == [("BTC-USD", 100.1)]

        # Reinstall around a safety rejection. Binance.US must not override it.
        guard._binance_us_reference_fallback_installed = False

        def divergence(symbol, price):
            return {"ok": False, "reason": "COINBASE_PRICE_DIVERGENCE"}

        guard._coinbase_reference_validation = divergence
        sampler._coinbase_reference_validation = divergence
        calls.clear()
        adapter.install_binance_us_reference_fallback()

        result = guard._coinbase_reference_validation("BTC-USD", 100.1)
        assert result["ok"] is False
        assert result["reason"] == "COINBASE_PRICE_DIVERGENCE"
        assert calls == []
    finally:
        guard._coinbase_reference_validation = original_validation
        guard._quote_verification_record = original_record
        sampler._coinbase_reference_validation = original_sampler_validation
        sampler._quote_verification_record = original_sampler_record
        guard._binance_us_reference_fallback_installed = original_marker
