from __future__ import annotations

from urllib.parse import parse_qs, urlparse

import robinhood_crypto_api as rh
from robinhood_quote_compat import estimated_price_side, install_robinhood_quote_compat, normalized_quote_fields


def test_estimated_price_side_maps_order_intent_to_documented_book_side():
    assert estimated_price_side("buy") == "ask"
    assert estimated_price_side("sell") == "bid"
    assert estimated_price_side("ask") == "ask"
    assert estimated_price_side("bid") == "bid"
    assert estimated_price_side("both") == "both"


def test_normalized_quote_fields_accepts_documented_v1_spread_inclusive_shape_without_synthesis():
    normalized = normalized_quote_fields(
        {
            "symbol": "BTC-USD",
            "bid_inclusive_of_sell_spread": "99.90",
            "ask_inclusive_of_buy_spread": "100.10",
        }
    )
    assert normalized["bid"] == "99.90"
    assert normalized["ask"] == "100.10"
    assert "price" not in normalized


def test_install_maps_estimated_price_buy_sell_to_ask_bid(monkeypatch):
    install_robinhood_quote_compat()
    client = rh.RobinhoodCryptoClient(api_key="unit", private_key_base64="unit")
    paths = []

    def request(method, path, body=None):
        paths.append(path)
        return {"results": []}

    monkeypatch.setattr(client, "request", request)
    client.estimated_price("BTC-USD", "buy", "0.01")
    client.estimated_price("BTC-USD", "sell", "0.01")

    first = parse_qs(urlparse(paths[0]).query)
    second = parse_qs(urlparse(paths[1]).query)
    assert first["side"] == ["ask"]
    assert second["side"] == ["bid"]


def test_preflight_accepts_spread_inclusive_broker_quote(monkeypatch):
    install_robinhood_quote_compat()

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
            return {"account_number": "acct", "status": "active", "buying_power": "10", "buying_power_currency": "USD"}

        def holdings(self, account_number):
            return []

        def orders(self, account_number):
            return []

        def best_bid_ask_quotes(self, *symbols):
            return [
                {
                    "symbol": "BTC-USD",
                    "bid_inclusive_of_sell_spread": "99.90",
                    "ask_inclusive_of_buy_spread": "100.10",
                }
            ]

    result = rh.preflight(Client(), rh.OrderJournal())
    assert result["QUOTE CHECK"] == "PASS"


def test_preflight_retries_another_api_tradable_usd_pair_when_primary_quote_is_empty():
    install_robinhood_quote_compat()

    class Client:
        def configured(self):
            return {"ok": True}

        def trading_pairs(self):
            return [
                rh.parse_trading_pair(
                    {"symbol": "BTC-USD", "status": "tradable", "is_api_tradable": True}
                ),
                rh.parse_trading_pair(
                    {"symbol": "ETH-USD", "status": "tradable", "is_api_tradable": True}
                ),
            ]

        def account_details(self):
            return {"account_number": "acct", "status": "active", "buying_power": "10", "buying_power_currency": "USD"}

        def holdings(self, account_number):
            return []

        def orders(self, account_number):
            return []

        def best_bid_ask_quotes(self, *symbols):
            symbol = symbols[0]
            if symbol == "BTC-USD":
                return []
            return [{"symbol": "ETH-USD", "bid": "1999", "ask": "2001"}]

    result = rh.preflight(Client(), rh.OrderJournal())
    assert result["QUOTE CHECK"] == "PASS"
    assert result["QUOTE PROBE SYMBOL"] == "ETH-USD"
    assert result["QUOTE CHECK REASON"] == "VALID_TRADABLE_USD_PAIR"
