from datetime import datetime, timedelta, timezone

import pytest

import brokerage_readiness as broker
import profit_attribution as pnl
from dashboard_helpers import entry_quality_action, market_focus_sections, wall_street_market_focus


def verified_quote(symbol="AAPL", price=100.0):
    return {
        "symbol": symbol,
        "requested_symbol": symbol,
        "provider_symbol": symbol,
        "provider": "polygon",
        "price": price,
        "quote_timestamp": (datetime.now(timezone.utc) - timedelta(seconds=10)).isoformat(),
        "interval": "5m",
        "exchange": "NASDAQ",
        "quote_verified": True,
    }


def reconciled_account(**overrides):
    account = {
        "equity": 10_000,
        "deployed_value": 1_000,
        "daily_new_exposure": 0,
        "daily_realized_pnl": 0,
        "symbol_exposure": {},
        "reconciled": True,
    }
    account.update(overrides)
    return account


class RecordingBroker:
    def __init__(self):
        self.submitted = []
        self.previews = []

    def get_account(self):
        return reconciled_account()

    def get_positions(self):
        return []

    def get_orders(self):
        return []

    def get_fills(self):
        return []

    def preview_order(self, proposal):
        self.previews.append(proposal)
        return {"accepted": True, "estimated_price": proposal.reference_price}

    def submit_order(self, proposal):
        self.submitted.append(proposal)
        return {"submitted": True, "order_id": "live-order-1"}


def test_fifo_lots_trace_realized_profit_by_lot():
    opened = datetime(2026, 1, 2, tzinfo=timezone.utc)
    lot_a = pnl.create_lot(symbol="AAPL", market="cash", quantity=10, entry_price=100, opened_at=opened, bucket="Core")
    lot_b = pnl.create_lot(symbol="AAPL", market="cash", quantity=10, entry_price=120, opened_at=opened + timedelta(days=1), bucket="Tactical")

    rows = pnl.fifo_close_lots([lot_b, lot_a], quantity=15, exit_price=130, exit_time=opened + timedelta(days=2), fees=5)

    assert len(rows) == 2
    assert rows[0].entry_price == 100
    assert rows[0].net_pnl == pytest.approx(296.6666666667)
    assert rows[1].entry_price == 120
    assert rows[1].net_pnl == pytest.approx(48.3333333333)
    assert lot_a.quantity_remaining == 0
    assert lot_b.quantity_remaining == 5


def test_daily_profit_story_sums_realized_trade_ledger():
    now = datetime.now(timezone.utc)
    rows = [
        {"symbol": "AAPL", "market": "cash", "net_pnl": 20, "gross_pnl": 22, "fees": 2, "exit_time": now.isoformat()},
        {"symbol": "MSFT", "market": "cash", "net_pnl": 30, "gross_pnl": 30, "fees": 0, "exit_time": now.isoformat()},
        {"symbol": "BTC-USD", "market": "crypto", "net_pnl": 15, "gross_pnl": 15, "fees": 0, "exit_time": (now - timedelta(days=1)).isoformat()},
    ]

    story = pnl.daily_profit_story(rows, now)

    assert story["net_pnl"] == 50
    assert story["contributors"] == [{"symbol": "MSFT", "net_pnl": 30.0}, {"symbol": "AAPL", "net_pnl": 20.0}]
    assert story["reconciled"] is True


def test_unrealized_profit_waits_for_verified_price():
    position = {"symbol": "AAPL", "quantity": 10, "average_price": 100}

    waiting = pnl.unrealized_position_pnl(position, {"symbol": "AAPL", "price": 120, "quote_verified": False})
    verified = pnl.unrealized_position_pnl(position, verified_quote("AAPL", 120))

    assert waiting["status"] == "WAITING FOR VERIFIED PRICE"
    assert verified["status"] == "VERIFIED"
    assert verified["unrealized_pnl"] == 200


def test_paper_and_live_profit_records_never_mix():
    rows = [
        {"market": "cash", "gross_pnl": 10, "fees": 1, "net_pnl": 9, "account_environment": "PAPER"},
        {"market": "cash", "gross_pnl": 20, "fees": 2, "net_pnl": 18, "account_environment": "LIVE"},
        {"market": "crypto", "gross_pnl": 5, "fees": 0, "net_pnl": 5, "account_environment": "PAPER"},
    ]

    summary = pnl.split_pnl_by_environment(rows)

    assert summary["PAPER:cash"]["net_pnl"] == 9
    assert summary["LIVE:cash"]["net_pnl"] == 18
    assert summary["PAPER:crypto"]["net_pnl"] == 5


def test_reconciliation_failure_blocks_live_proposal():
    proposal = broker.create_live_order_proposal(
        symbol="AAPL",
        market="cash",
        side="BUY",
        quantity=1,
        quote=verified_quote("AAPL", 50),
        strategy="core",
        risk_checks=[{"name": "quote", "passed": True}],
        broker_mode="live_preview",
    )

    valid, reason = broker.validate_live_order_proposal(proposal, reconciled_account(reconciled=False))

    assert valid is False
    assert "reconciliation" in reason


def test_live_read_only_and_preview_modes_cannot_submit(monkeypatch):
    monkeypatch.setattr(broker, "LIVE_TRADING_KILL_SWITCH", False)
    monkeypatch.setattr(broker, "ENABLE_BROKER_SUBMISSION", True)
    adapter = RecordingBroker()
    proposal = broker.create_live_order_proposal(
        symbol="AAPL",
        market="cash",
        side="BUY",
        quantity=1,
        quote=verified_quote("AAPL", 50),
        strategy="core",
        risk_checks=[{"name": "quote", "passed": True}],
        broker_mode="live_preview",
    )

    result = broker.submit_live_order(adapter, proposal, account=reconciled_account())

    assert result["submitted"] is False
    assert result["status"] == "preview"
    assert adapter.submitted == []
    assert len(adapter.previews) == 1


def test_live_kill_switch_blocks_even_manual_approval(monkeypatch):
    monkeypatch.setattr(broker, "LIVE_TRADING_KILL_SWITCH", True)
    monkeypatch.setattr(broker, "ENABLE_BROKER_SUBMISSION", True)
    adapter = RecordingBroker()
    proposal = broker.create_live_order_proposal(
        symbol="AAPL",
        market="cash",
        side="BUY",
        quantity=1,
        quote=verified_quote("AAPL", 50),
        strategy="core",
        risk_checks=[{"name": "quote", "passed": True}],
        broker_mode="live_manual_approval",
    )
    approval = broker.approve_live_order_proposal(proposal)

    result = broker.submit_live_order(adapter, proposal, account=reconciled_account(), approval=approval)

    assert result["submitted"] is False
    assert "kill switch" in result["reason"]
    assert adapter.submitted == []


def test_changed_live_proposal_invalidates_manual_approval(monkeypatch):
    monkeypatch.setattr(broker, "LIVE_TRADING_KILL_SWITCH", False)
    monkeypatch.setattr(broker, "ENABLE_BROKER_SUBMISSION", True)
    adapter = RecordingBroker()
    original = broker.create_live_order_proposal(
        symbol="AAPL",
        market="cash",
        side="BUY",
        quantity=1,
        quote=verified_quote("AAPL", 50),
        strategy="core",
        risk_checks=[{"name": "quote", "passed": True}],
        broker_mode="live_manual_approval",
    )
    changed = broker.create_live_order_proposal(
        symbol="AAPL",
        market="cash",
        side="BUY",
        quantity=2,
        quote=verified_quote("AAPL", 50),
        strategy="core",
        risk_checks=[{"name": "quote", "passed": True}],
        broker_mode="live_manual_approval",
    )
    approval = broker.approve_live_order_proposal(original)

    result = broker.submit_live_order(adapter, changed, account=reconciled_account(), approval=approval)

    assert result["submitted"] is False
    assert "manual approval" in result["reason"]


def test_broker_fill_price_overrides_reference_price():
    proposal = broker.create_live_order_proposal(
        symbol="AAPL",
        market="cash",
        side="BUY",
        quantity=1,
        quote=verified_quote("AAPL", 50),
        strategy="core",
        risk_checks=[{"name": "quote", "passed": True}],
        broker_mode="live_preview",
    )

    execution = broker.execution_from_broker_fill({"fill_price": 49.75, "quantity": 1}, proposal)

    assert execution["fill_price"] == 49.75
    assert execution["fill_price"] != proposal.reference_price


def test_market_focus_separates_stock_crypto_and_filters_foreign():
    rows = [
        {**verified_quote("AAPL", 190), "asset_class": "stock", "market": "cash", "action": "BUY", "opportunity_score": 90},
        {**verified_quote("BTC-USD", 50_000), "asset_class": "crypto", "market": "crypto", "action": "BUY", "opportunity_score": 85, "exchange": "CRYPTO"},
        {**verified_quote("VOD.L", 70), "asset_class": "stock", "market": "cash", "exchange": "LSE", "region": "United Kingdom", "action": "BUY", "opportunity_score": 99},
    ]

    focus = market_focus_sections(rows, positions=[{"symbol": "AAPL", "market": "cash", "quantity": 2, "current_price": 190}])

    assert [row["symbol"] for row in focus["wall_street"]] == ["AAPL"]
    assert [row["symbol"] for row in focus["crypto"]] == ["BTC-USD"]
    assert all(row["symbol"] != "VOD.L" for row in focus["wall_street"] + focus["crypto"] + focus["waiting"])
    assert focus["ownership"][0]["symbol"] == "AAPL"


def test_market_focus_stale_buy_waits_instead_of_displaying_trade():
    stale = {**verified_quote("MSFT", 300), "market": "cash", "action": "BUY", "quote_timestamp": (datetime.now(timezone.utc) - timedelta(days=3)).isoformat()}

    focus = market_focus_sections([stale])

    assert focus["wall_street"] == []
    assert focus["waiting"][0]["display_action"] == "WAIT FOR VERIFIED PRICE"
    assert entry_quality_action(stale) == "WAIT FOR VERIFIED PRICE"


def test_wall_street_market_focus_is_stock_etf_only():
    rows = [
        {**verified_quote("SPY", 550), "asset_class": "etf", "market": "cash", "action": "BUY", "opportunity_score": 88, "avg_dollar_volume": 1_000_000_000},
        {**verified_quote("BTC-USD", 50_000), "asset_class": "crypto", "market": "crypto", "action": "BUY", "opportunity_score": 99, "avg_dollar_volume": 2_000_000_000},
        {**verified_quote("BHP.AX", 40), "asset_class": "stock", "market": "cash", "exchange": "ASX", "region": "Australia", "action": "BUY", "opportunity_score": 97},
    ]

    focus = wall_street_market_focus(rows)

    assert [row["Symbol"] for row in focus["best_trades"]] == ["SPY"]
    assert all("BTC" not in str(section) for section in focus.values())
    assert all("BHP" not in str(section) for section in focus.values())


def test_wall_street_market_focus_never_ranks_stale_opportunities():
    fresh = {**verified_quote("AAPL", 200), "asset_class": "stock", "market": "cash", "action": "BUY", "opportunity_score": 80, "avg_dollar_volume": 500_000_000}
    stale = {**verified_quote("NVDA", 900), "asset_class": "stock", "market": "cash", "action": "BUY", "opportunity_score": 99, "avg_dollar_volume": 800_000_000, "quote_timestamp": (datetime.now(timezone.utc) - timedelta(days=2)).isoformat()}

    focus = wall_street_market_focus([stale, fresh])

    assert [row["Symbol"] for row in focus["best_trades"]] == ["AAPL"]
    assert focus["rejected"][0]["Symbol"] == "NVDA"


def test_wall_street_ranking_prefers_verified_liquid_quality_over_raw_gain():
    weak_mover = {
        **verified_quote("THIN", 5),
        "asset_class": "stock",
        "market": "cash",
        "action": "BUY",
        "change_pct": 25,
        "relative_volume": 1.1,
        "avg_dollar_volume": 500_000,
        "score": 55,
        "confidence": 55,
        "reward_risk_ratio": 1.0,
    }
    institutional_mover = {
        **verified_quote("MSFT", 430),
        "asset_class": "stock",
        "market": "cash",
        "action": "BUY",
        "change_pct": 4,
        "relative_volume": 2.4,
        "avg_dollar_volume": 900_000_000,
        "score": 88,
        "confidence": 86,
        "reward_risk_ratio": 2.4,
        "catalyst": "Institutional volume breakout",
    }

    focus = wall_street_market_focus([weak_mover, institutional_mover])

    assert focus["best_trades"][0]["Symbol"] == "MSFT"


def test_wall_street_focus_profit_sources_use_attribution_ledger():
    focus = wall_street_market_focus(
        [],
        ledger_rows=[
            {
                "symbol": "AAPL",
                "market": "cash",
                "strategy": "core",
                "entry_price": 100,
                "exit_price": 110,
                "quantity": 5,
                "net_pnl": 50,
            },
            {"symbol": "ETH-USD", "market": "crypto", "strategy": "crypto", "net_pnl": 99},
        ],
    )

    assert focus["profit_sources"][0]["Symbol"] == "AAPL"
    assert "ETH" not in str(focus["profit_sources"])


def test_stock_profit_attribution_rows_include_realized_unrealized_and_total():
    rows = pnl.profit_attribution_rows(
        positions=[
            {
                "symbol": "AAPL",
                "market": "cash",
                "quantity": 5,
                "average_price": 100,
                "bucket": "Tactical",
                "strategy": "Momentum",
                "quote_provider": "polygon",
            }
        ],
        ledger_rows=[
            {
                "symbol": "AAPL",
                "market": "cash",
                "bucket": "Tactical",
                "strategy": "Momentum",
                "side": "SELL",
                "quantity": 2,
                "entry_price": 90,
                "exit_price": 110,
                "net_pnl": 40,
                "fees": 0,
                "entry_time": "2026-08-01T14:00:00+00:00",
                "exit_time": "2026-08-02T14:00:00+00:00",
                "quote_provider": "polygon",
                "decision_id": "buy-sig",
                "status": "CLOSED",
            }
        ],
        quotes={"AAPL": verified_quote("AAPL", 112)},
        market="cash",
        equity=10_000,
    )

    assert rows[0]["symbol"] == "AAPL"
    assert rows[0]["realized_pnl"] == 40
    assert rows[0]["unrealized_pnl"] == 60
    assert rows[0]["total_pnl"] == 100
    assert rows[0]["status"] == "VERIFIED"


def test_crypto_profit_attribution_rows_are_separate_from_stocks():
    rows = pnl.profit_attribution_rows(
        positions=[
            {"symbol": "BTC-USD", "market": "crypto", "quantity": 1, "average_price": 50_000},
            {"symbol": "AAPL", "market": "cash", "quantity": 1, "average_price": 100},
        ],
        ledger_rows=[
            {"symbol": "BTC-USD", "market": "crypto", "quantity": 1, "entry_price": 50_000, "exit_price": 55_000, "net_pnl": 5_000},
            {"symbol": "AAPL", "market": "cash", "quantity": 1, "entry_price": 100, "exit_price": 110, "net_pnl": 10},
        ],
        quotes={"BTC-USD": verified_quote("BTC-USD", 60_000)},
        market="crypto",
        equity=100_000,
    )

    assert [row["symbol"] for row in rows] == ["BTC-USD"]
    assert rows[0]["realized_pnl"] == 5_000
    assert rows[0]["unrealized_pnl"] == 10_000


def test_reconciliation_error_blocks_new_entries_when_equity_differs():
    result = pnl.reconcile_portfolio(
        cash=1_000,
        positions=[{"symbol": "AAPL", "quantity": 2, "average_price": 100}],
        quotes={"AAPL": verified_quote("AAPL", 125)},
        broker_reported_equity=2_000,
    )

    assert result["status"] == "PORTFOLIO_RECONCILIATION_ERROR"
    assert result["reconciled"] is False
