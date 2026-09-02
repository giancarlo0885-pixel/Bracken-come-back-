import pytest

import robinhood_crypto_api as rh
import robinhood_pagination_compat as compat


class Client:
    def __init__(self):
        self.base_url = "https://trading.robinhood.com"
        self.paths = []
        self.pages = {}

    @staticmethod
    def _results(data):
        return rh.RobinhoodCryptoClient._results(data)

    def request(self, method, path):
        assert method == "GET"
        self.paths.append(path)
        return self.pages[path]


def test_paginated_results_follow_same_host_next_link():
    client = Client()
    client.pages = {
        "/api/v2/crypto/trading/trading_pairs/": {
            "results": [{"symbol": "AAA-USD"}],
            "next": "https://trading.robinhood.com/api/v2/crypto/trading/trading_pairs/?cursor=abc",
        },
        "/api/v2/crypto/trading/trading_pairs/?cursor=abc": {
            "results": [{"symbol": "BTC-USD"}],
            "next": None,
        },
    }

    result = compat._paginated_results(client, "/api/v2/crypto/trading/trading_pairs/")

    assert [item["symbol"] for item in result] == ["AAA-USD", "BTC-USD"]
    assert client.paths == [
        "/api/v2/crypto/trading/trading_pairs/",
        "/api/v2/crypto/trading/trading_pairs/?cursor=abc",
    ]


def test_pagination_rejects_foreign_host_and_loops():
    client = Client()
    client.pages = {
        "/api/v2/crypto/trading/trading_pairs/": {
            "results": [],
            "next": "https://evil.example/api/v2/crypto/trading/trading_pairs/?cursor=x",
        }
    }
    with pytest.raises(RuntimeError, match="host mismatch"):
        compat._paginated_results(client, "/api/v2/crypto/trading/trading_pairs/")

    client.pages = {
        "/api/v2/crypto/trading/trading_pairs/": {
            "results": [],
            "next": "/api/v2/crypto/trading/trading_pairs/",
        }
    }
    with pytest.raises(RuntimeError, match="loop"):
        compat._paginated_results(client, "/api/v2/crypto/trading/trading_pairs/")


def test_install_paginates_pairs_holdings_and_orders(monkeypatch):
    monkeypatch.setattr(rh, "_oracle_pagination_compat_installed", False, raising=False)
    original_pairs = rh.RobinhoodCryptoClient.trading_pairs
    original_holdings = rh.RobinhoodCryptoClient.holdings
    original_orders = rh.RobinhoodCryptoClient.orders
    try:
        compat.install_robinhood_pagination_compat()
        client = Client()
        client.pages = {
            "/api/v2/crypto/trading/trading_pairs/": {
                "results": [
                    {"symbol": "AAA-USD", "status": "tradable", "is_api_tradable": True},
                ],
                "next": "/api/v2/crypto/trading/trading_pairs/?cursor=2",
            },
            "/api/v2/crypto/trading/trading_pairs/?cursor=2": {
                "results": [
                    {"symbol": "BTC-USD", "status": "tradable", "is_api_tradable": True},
                ],
                "next": None,
            },
            "/api/v2/crypto/trading/holdings/?account_number=A1": {
                "results": [{"asset_code": "BTC"}],
                "next": "/api/v2/crypto/trading/holdings/?account_number=A1&cursor=2",
            },
            "/api/v2/crypto/trading/holdings/?account_number=A1&cursor=2": {
                "results": [{"asset_code": "ETH"}],
                "next": None,
            },
            "/api/v2/crypto/trading/orders/?account_number=A1": {
                "results": [{"id": "o1"}],
                "next": "/api/v2/crypto/trading/orders/?account_number=A1&cursor=2",
            },
            "/api/v2/crypto/trading/orders/?account_number=A1&cursor=2": {
                "results": [{"id": "o2"}],
                "next": None,
            },
        }
        client.__class__ = type("PatchedClient", (Client, rh.RobinhoodCryptoClient), {})

        pairs = rh.RobinhoodCryptoClient.trading_pairs(client)
        holdings = rh.RobinhoodCryptoClient.holdings(client, "A1")
        orders = rh.RobinhoodCryptoClient.orders(client, "A1")

        assert [item["symbol"] for item in pairs] == ["AAA-USD", "BTC-USD"]
        assert all(item["tradable"] for item in pairs)
        assert [item["asset_code"] for item in holdings] == ["BTC", "ETH"]
        assert [item["id"] for item in orders] == ["o1", "o2"]
    finally:
        rh.RobinhoodCryptoClient.trading_pairs = original_pairs
        rh.RobinhoodCryptoClient.holdings = original_holdings
        rh.RobinhoodCryptoClient.orders = original_orders
        setattr(rh, "_oracle_pagination_compat_installed", False)
