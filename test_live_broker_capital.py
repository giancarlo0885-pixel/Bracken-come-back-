from __future__ import annotations

from types import SimpleNamespace

import pytest

import live_broker_capital as capital


class BrokerClient:
    def __init__(self, *, missing_eth: bool = False):
        self.missing_eth = missing_eth

    def account_details(self):
        return {
            "account_number": "acct-test",
            "status": "active",
            "buying_power": "1000.00",
            "buying_power_currency": "USD",
        }

    def holdings(self, account_number: str):
        assert account_number == "acct-test"
        return [
            {
                "account_number": account_number,
                "asset_code": "BTC",
                "total_quantity": "0.01",
                "quantity_available_for_trading": "0.01",
            },
            {
                "account_number": account_number,
                "asset_code": "ETH",
                "total_quantity": "0.50",
                "quantity_available_for_trading": "0.40",
            },
        ]

    def best_bid_ask_quotes(self, *symbols: str):
        assert set(symbols) == {"BTC-USD", "ETH-USD"}
        quotes = [
            {"symbol": "BTC-USD", "bid": 60000.0, "ask": 60010.0},
        ]
        if not self.missing_eth:
            quotes.append({"symbol": "ETH-USD", "bid": 3000.0, "ask": 3001.0})
        return quotes


def test_snapshot_reconstructs_live_equity_from_buying_power_and_bid_marks():
    snapshot = capital.build_robinhood_capital_snapshot(BrokerClient())

    assert snapshot.valid is True
    assert snapshot.complete is True
    assert snapshot.buying_power == pytest.approx(1000.0)
    assert snapshot.position_values["BTC-USD"] == pytest.approx(600.0)
    assert snapshot.position_values["ETH-USD"] == pytest.approx(1500.0)
    assert snapshot.holdings_value == pytest.approx(2100.0)
    assert snapshot.gross_exposure == pytest.approx(2100.0)
    assert snapshot.equity == pytest.approx(3100.0)
    assert snapshot.tradable_quantities["ETH-USD"] == pytest.approx(0.40)
    assert snapshot.sizing_allowed is True


def test_missing_holding_quote_blocks_new_capital_deployment():
    snapshot = capital.build_robinhood_capital_snapshot(BrokerClient(missing_eth=True))
    metrics = snapshot.portfolio_metrics()

    assert snapshot.valid is True
    assert snapshot.complete is False
    assert snapshot.missing_quotes == ("ETH-USD",)
    assert metrics["cash"] == 0.0
    assert metrics["buying_power"] == 0.0
    assert metrics["buying_power_validated"] is False
    assert metrics["broker_capital_reason"] == "BROKER_HOLDING_QUOTE_INCOMPLETE"


def test_live_crypto_allocator_uses_fresh_broker_capital(monkeypatch):
    monkeypatch.setenv("EXECUTION_MODE", "live")
    monkeypatch.setenv("ROBINHOOD_CRYPTO_ENABLED", "true")
    monkeypatch.setenv("BROKER_CAPITAL_SIZING_ENABLED", "true")

    calls = []

    def original_portfolio_equity(market, *args, **kwargs):
        return {"equity": 2000.0, "cash": 2000.0, "gross_exposure": 0.0, "buying_power": 2000.0}

    def original_allocator(*args, **kwargs):
        calls.append(dict(kwargs))
        return dict(kwargs)

    oracle = SimpleNamespace(
        portfolio_equity=original_portfolio_equity,
        adaptive_capital_allocation=original_allocator,
    )
    provider = capital.LiveBrokerCapitalProvider(lambda: BrokerClient(), ttl_seconds=60)
    capital.install_live_broker_capital_sizing(oracle, provider=provider)

    portfolio = oracle.portfolio_equity("crypto")
    assert portfolio["equity"] == pytest.approx(3100.0)
    assert portfolio["cash"] == pytest.approx(1000.0)
    assert portfolio["gross_exposure"] == pytest.approx(2100.0)
    assert portfolio["broker_capital_complete"] is True

    result = oracle.adaptive_capital_allocation(
        symbol="BTC-USD",
        market="crypto",
        equity=2000.0,
        cash=2000.0,
        current_exposure=100.0,
        existing_position_value=50.0,
        buying_power=2000.0,
        buying_power_validated=False,
    )
    assert calls
    assert result["equity"] == pytest.approx(3100.0)
    assert result["cash"] == pytest.approx(1000.0)
    assert result["buying_power"] == pytest.approx(1000.0)
    assert result["buying_power_validated"] is True
    assert result["current_exposure"] == pytest.approx(2100.0)
    assert result["existing_position_value"] == pytest.approx(600.0)


def test_paper_mode_and_stocks_keep_existing_sizing(monkeypatch):
    monkeypatch.setenv("EXECUTION_MODE", "paper")
    monkeypatch.setenv("ROBINHOOD_CRYPTO_ENABLED", "true")

    oracle = SimpleNamespace(
        portfolio_equity=lambda market, *args, **kwargs: {"equity": 2000.0, "cash": 1500.0},
        adaptive_capital_allocation=lambda *args, **kwargs: dict(kwargs),
    )
    provider = capital.LiveBrokerCapitalProvider(lambda: BrokerClient(), ttl_seconds=60)
    capital.install_live_broker_capital_sizing(oracle, provider=provider)

    portfolio = oracle.portfolio_equity("crypto")
    assert portfolio == {"equity": 2000.0, "cash": 1500.0}

    result = oracle.adaptive_capital_allocation(
        symbol="BTC-USD",
        market="crypto",
        equity=2000.0,
        cash=1500.0,
        current_exposure=0.0,
    )
    assert result["equity"] == 2000.0
    assert result["cash"] == 1500.0
