from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
import base64

import pandas as pd

import engine
import market_worker
import provider_router
import robinhood_agentic_mcp as agentic
import robinhood_crypto_api as rh


def _history(symbol: str = "ETH-USD", price: float = 2500.0) -> pd.DataFrame:
    frame = pd.DataFrame(
        {
            "Open": [price - 1] * 70,
            "High": [price + 2] * 70,
            "Low": [price - 3] * 70,
            "Close": [price] * 70,
            "Volume": [10_000] * 70,
        },
        index=pd.date_range(datetime.now(timezone.utc) - timedelta(minutes=69), periods=70, freq="min"),
    )
    frame.attrs["provider_route"] = {
        "provider": "Polygon",
        "requested_symbol": symbol,
        "provider_symbol": symbol,
        "provider_native_symbol": "X:ETHUSD",
        "price": price,
        "current_price": price,
        "quote_timestamp": frame.index[-1].isoformat(),
        "interval": "1m",
        "quote_verified": True,
        "source_identity": f"Polygon:{symbol}:1d:1m",
        "cache_identity": f"history_polygon_{symbol}_1d_1m_adjusted_true_extended_true",
    }
    return frame


def test_positive_crypto_price_propagates_to_execution_payload():
    history = _history("ETH-USD", 2500.25)
    signal = engine.analyze_market("ETH-USD", history, 0.0)
    assert signal is not None

    route = market_worker._attach_execution_metadata(signal, history, "fast")
    payload = market_worker._execution_quote_payload_from_history("ETH-USD", history, signal.price, scan_type="fast")

    assert route["price"] == 2500.25
    assert signal.price == 2500.25
    assert payload is not None
    assert payload["price"] == 2500.25
    assert payload["scan_type"] == "fast"
    assert payload["quote_verified"] is True


def test_zero_price_rejected_before_candidate_execution():
    history = _history("ADA-USD", 0.0)
    assert engine.analyze_market("ADA-USD", history, 0.0) is None
    payload = market_worker._execution_quote_payload_from_history("ADA-USD", history, 0.0, scan_type="fast")
    assert payload is None


def test_yahoo_research_fallback_keeps_quote_verified_false(monkeypatch):
    def yahoo(*args):
        frame = _history("BTC-USD", 65000.0)
        frame.attrs.clear()
        return frame

    monkeypatch.setattr(provider_router, "get_api_settings", lambda: {})
    routed = provider_router.route_history("BTC-USD", "5d", "1d", yahoo)

    assert routed.provider == "Yahoo Finance"
    assert routed.frame.attrs["quote_verified"] is False
    assert routed.attempts[-1].status == "strict_research_fallback"


def test_robinhood_agentic_disconnected_and_tool_discovery():
    disconnected = agentic.discover_tools(None)
    assert disconnected.status == "ROBINHOOD_AGENTIC_NOT_CONNECTED"

    class Client:
        def list_tools(self):
            return [{"name": "get_accounts"}, {"name": "preview_crypto_order"}]

    limited = agentic.discover_tools(Client())
    assert limited.connected is True
    assert limited.status == "LIMITED"
    assert "place_crypto_order" in limited.missing_tools


def test_agentic_preview_failure_and_success():
    class FailingClient:
        def list_tools(self):
            return [{"name": name} for name in agentic.REQUIRED_CRYPTO_TOOLS]

        def call_tool(self, tool, payload):
            raise RuntimeError("preview failed")

    assert agentic.preview_crypto_order(FailingClient(), {"symbol": "BTC-USD"})["ok"] is False

    class SuccessClient(FailingClient):
        def call_tool(self, tool, payload):
            return {"estimated_price": "100.00", "warnings": []}

    assert agentic.preview_crypto_order(SuccessClient(), {"symbol": "BTC-USD"})["ok"] is True


def test_direct_api_missing_key_and_malformed_private_key():
    client = rh.RobinhoodCryptoClient(api_key="", private_key_base64="")
    assert client.configured()["ok"] is False
    try:
        rh.sign_message("not-base64", b"message")
    except ValueError as exc:
        assert "malformed" in str(exc)
    else:
        raise AssertionError("malformed private key should fail")


def test_signature_message_and_timestamp_freshness():
    key = base64.b64encode(b"1" * 32).decode("ascii")
    signature = rh.sign_message(key, rh.signing_message("api", "100", "/path", "POST", {"a": 1}), signer=lambda private, msg: b"signed")
    assert signature == base64.b64encode(b"signed").decode("ascii")
    assert b"api100/pathPOST" in rh.signing_message("api", "100", "/path", "POST", {"a": 1})
    assert rh.timestamp_is_fresh("100", now=120, max_age_seconds=30) is True
    assert rh.timestamp_is_fresh("100", now=200, max_age_seconds=30) is False


def test_v2_trading_pair_parsing_and_order_limits():
    pair = rh.parse_trading_pair(
        {
            "symbol": "BTC-USD",
            "status": "tradable",
            "is_api_tradable": True,
            "asset_increment": "0.00000001",
            "quote_increment": "0.01",
            "min_order_amount": "1.00",
            "max_order_size": "2",
        }
    )
    assert pair["tradable"] is True
    assert rh.decimal_down("1.234567899", pair["asset_increment"]) == Decimal("1.23456789")
    assert rh.decimal_down("10.239", pair["quote_increment"]) == Decimal("10.23")
    assert rh.validate_order_amount(pair, "0.50")["reason"] == "MIN_ORDER_NOT_MET"
    assert rh.validate_order_amount(pair, "10.00", quantity="3")["reason"] == "MAX_ORDER_EXCEEDED"
    assert rh.validate_order_amount(pair, "10.00", quantity="1")["ok"] is True


def test_best_bid_ask_estimated_price_spread_and_slippage_inputs():
    quote = rh.best_bid_ask({"bid_price": "99.00", "ask_price": "101.00"})
    assert quote is not None
    assert quote["mid"] == Decimal("100.00")
    assert quote["spread_pct"] == Decimal("2.00")
    assert rh.best_bid_ask({"bid_price": "0", "ask_price": "101.00"}) is None


def test_order_journal_uuid_duplicate_timeout_and_restart_reconcile():
    journal = rh.OrderJournal()
    record = journal.create("ETH-USD", "BUY", {"amount": "25"})
    client_order_id = record["client_order_id"]
    assert client_order_id
    assert journal.has_duplicate("ETH-USD", "BUY") is True
    timed_out = rh.mark_submit_timeout(journal, client_order_id)
    assert timed_out["state"] == "UNKNOWN_RECONCILE_REQUIRED"
    reconciled = rh.reconcile_unfinished_orders(journal, lambda _: {"state": "FILLED", "id": "remote"})
    assert reconciled[0]["state"] == "FILLED"


def test_live_arming_gates_default_disarmed_and_no_secret_logging():
    status = rh.live_arming_status(preflight_passed=True)
    assert status["armed"] is False
    assert "ENABLE_AUTOTRADE=false" in status["reasons"]
    assert "secret" not in rh._redact("error apikey=secret").lower()


def test_crypto_priority_weight_controls_analysis_not_capital():
    assert market_worker.analysis_priority_weight("crypto") == 0.70
    assert market_worker.analysis_priority_weight("cash") == 0.30


def test_broker_market_reference_requires_symbol_spread_and_price_agreement():
    good = rh.validate_broker_market_reference(
        "BTC-USD",
        100.05,
        {"symbol": "BTC-USD", "bid": "99.90", "ask": "100.10"},
        max_price_difference_pct="0.50",
        max_spread_pct="0.50",
    )
    assert good["ok"] is True
    assert good["reason"] == "BROKER_PRICE_CONFIRMED"

    mismatch = rh.validate_broker_market_reference(
        "BTC-USD",
        100.0,
        {"symbol": "ETH-USD", "bid": "99.90", "ask": "100.10"},
    )
    assert mismatch["reason"] == "BROKER_SYMBOL_MISMATCH"

    divergence = rh.validate_broker_market_reference(
        "BTC-USD",
        105.0,
        {"symbol": "BTC-USD", "bid": "99.90", "ask": "100.10"},
        max_price_difference_pct="0.50",
    )
    assert divergence["reason"] == "BROKER_PRICE_DIVERGENCE"


def test_robinhood_preflight_requires_account_buying_power_and_live_quote(monkeypatch):
    class Client:
        def configured(self):
            return {"ok": True}

        def trading_pairs(self):
            return [
                rh.parse_trading_pair(
                    {
                        "symbol": "BTC-USD",
                        "status": "tradable",
                        "is_api_tradable": True,
                        "min_order_amount": "1",
                        "max_order_size": "10",
                    }
                )
            ]

        def account_details(self):
            return {"status": "active", "buying_power": "250.00", "buying_power_currency": "USD"}

        def best_bid_ask_quotes(self, *symbols):
            return [{"symbol": "BTC-USD", "bid": "99.90", "ask": "100.10"}]

    monkeypatch.setattr(rh, "ENABLE_AUTOTRADE", True)
    monkeypatch.setattr(rh, "ENABLE_CRYPTO_AUTOTRADE", True)
    monkeypatch.setattr(rh, "ENABLE_BROKER_SUBMISSION", True)
    monkeypatch.setattr(rh, "LIVE_TRADING_ARMED", True)
    monkeypatch.setattr(rh, "GLOBAL_KILL_SWITCH", False)
    monkeypatch.setattr(rh, "LIVE_ORDER_APPROVAL_MODE", "preauthorized")

    result = rh.preflight(Client(), rh.OrderJournal())
    assert result["ROBINHOOD AUTH"] == "PASS"
    assert result["ACCOUNT STATUS"] == "PASS"
    assert result["BUYING POWER CHECK"] == "PASS"
    assert result["QUOTE CHECK"] == "PASS"
    assert result["LIVE TRADING ARMED/DISARMED"] == "ARMED"

    class NoBuyingPower(Client):
        def account_details(self):
            return {"status": "active", "buying_power": "0", "buying_power_currency": "USD"}

    blocked = rh.preflight(NoBuyingPower(), rh.OrderJournal())
    assert blocked["BUYING POWER CHECK"] == "FAIL"
    assert blocked["LIVE TRADING ARMED/DISARMED"] == "DISARMED"
