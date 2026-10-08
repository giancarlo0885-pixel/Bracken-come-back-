from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

import paper_broker
from paper_execution_accounting import (
    _fill_capacity,
    _simulate_fill_explicit_fee,
)
from paper_fee_policy import fee_aware_fifo_close_lots
from profit_attribution import PositionLot


def test_fee_is_separate_from_fill_price():
    fill = _simulate_fill_explicit_fee(
        side="BUY",
        market="cash",
        reference_price=100.0,
        quote={"spread_pct": 0.0},
        order_value=500.0,
        spread_pct=0.0,
        slippage_pct=0.0,
        market_impact_pct=0.0,
        latency_pct=0.0,
        fee_pct=0.01,
    )
    assert fill.fill_price == 100.0
    assert fill.fee_pct == 0.01


def test_explicit_fee_installer_updates_oracle_fill_alias():
    source = (Path(__file__).parent / "paper_execution_accounting.py").read_text()
    assert "oracle_bot.simulate_fill = _simulate_fill_explicit_fee" in source


def test_fill_capacity_creates_conservative_ioc_partial_cap(monkeypatch):
    monkeypatch.setenv("PAPER_FILL_MAX_PARTICIPATION_PCT", "0.0025")
    capacity, liquidity = _fill_capacity(10.0, {"volume": 10_000.0})
    assert liquidity == 100_000.0
    assert capacity == 250.0


def test_fifo_return_includes_entry_and_exit_fees_without_double_counting_ledger():
    lot = PositionLot(
        lot_id="lot-1",
        symbol="AAPL",
        market="cash",
        bucket="Tactical",
        strategy="test",
        opened_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        quantity_opened=10.0,
        quantity_remaining=10.0,
        entry_price=100.0,
        entry_fees=2.0,
    )
    rows = fee_aware_fifo_close_lots(
        [lot],
        quantity=10.0,
        exit_price=110.0,
        exit_time=datetime(2026, 1, 2, tzinfo=timezone.utc),
        fees=3.0,
    )
    row = rows[0]
    # BUY ledger carries the $2 entry fee. SELL ledger carries only the $3 exit
    # fee, so portfolio-level sums do not count the entry fee twice.
    assert row.gross_pnl == 100.0
    assert row.fees == 3.0
    assert row.net_pnl == 97.0
    # Return is round-trip net: (100 gross - 2 entry - 3 exit) / 1002 cost basis.
    assert round(row.return_pct, 6) == round(95.0 / 1002.0 * 100.0, 6)


def test_small_account_profile_cannot_restore_stale_margin_leverage(monkeypatch):
    monkeypatch.setattr(paper_broker, "PAPER_BROKER_PROFILE", "small-account-paper")
    account = paper_broker.build_account(
        "cash",
        {
            "starting_balance": 2000.0,
            "cash": 2000.0,
            "margin_debt": 0.0,
            "leverage_limit": 4.0,
            "broker_profile": "small-account-paper",
        },
        [],
    )
    assert account.leverage_limit == 1.0
    assert account.buying_power == 2000.0


def test_execution_accounting_migration_adds_explicit_fee_and_order_tables():
    sql = (Path(__file__).parent / "migrations" / "20260830_paper_execution_accounting.sql").read_text()
    assert "ALTER TABLE trades ADD COLUMN IF NOT EXISTS fees" in sql
    assert "CREATE TABLE IF NOT EXISTS paper_orders" in sql
    assert "CREATE TABLE IF NOT EXISTS paper_fills" in sql


def test_live_worker_does_not_install_paper_fill_layer():
    for filename in ("stock_worker.py", "crypto_worker.py", "worker.py"):
        source = (Path(__file__).parent / filename).read_text()
        assert 'os.getenv("EXECUTION_MODE", "paper").strip().lower() == "paper"' in source


@pytest.mark.parametrize("side", ["BUY", "SELL"])
def test_persisted_fill_audit_uses_completed_trade_price(monkeypatch, side):
    import database
    from paper_execution_accounting import _record_order_and_fill
    statements = []
    class Conn:
        def __enter__(self): return self
        def __exit__(self, *args): return False
        def execute(self, sql, params): statements.append((sql, params))
    monkeypatch.setattr(database, "connect", lambda: Conn())
    fill = _simulate_fill_explicit_fee(side=side, market="crypto", reference_price=100,
                                      order_value=500, fee_pct=0.001)
    actual_price = fill.fill_price + 0.05
    trade = {"price": actual_price, "quantity": 2, "value": actual_price * 2}
    _record_order_and_fill(market="crypto", symbol="TEST-USD", side=side,
                          requested_notional=500, trade=trade, fill=fill,
                          fee_amount=trade["value"] * 0.001, quote={}, reason="entry")
    order, persisted = statements
    assert "INSERT INTO paper_orders" in order[0]
    assert order[1][12] == actual_price
    assert "INSERT INTO paper_fills" in persisted[0]
    assert persisted[1][7] == actual_price
    assert persisted[1][8] == actual_price * persisted[1][5]
    assert persisted[1][9] == trade["value"] * 0.001


def test_missing_completed_trade_price_cannot_invent_fill_audit(monkeypatch):
    import database
    from paper_execution_accounting import _record_order_and_fill
    def forbidden(): raise AssertionError("missing execution price must not be simulated")
    monkeypatch.setattr(database, "connect", forbidden)
    fill = _simulate_fill_explicit_fee(side="SELL", market="crypto", reference_price=100)
    for price in (None, 0, float("nan")):
        _record_order_and_fill(market="crypto", symbol="TEST-USD", side="SELL",
                              requested_notional=100, trade={"price": price, "quantity": 1, "value": 100},
                              fill=fill, fee_amount=0.1, quote={}, reason="exit")


def test_brain_outcome_uses_round_trip_fifo_not_sell_ledger_net():
    from paper_execution_accounting import _closed_lot_memory_outcome
    lot = PositionLot(lot_id="lot", symbol="TEST-USD", market="crypto", bucket="Tactical", strategy="test",
                      opened_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
                      quantity_opened=2, quantity_remaining=2, entry_price=100, entry_fees=2)
    rows = fee_aware_fifo_close_lots([lot], quantity=1, exit_price=101,
                                    exit_time=datetime(2026, 1, 2, tzinfo=timezone.utc), fees=0.5)
    row = rows[0].to_dict()
    assert row["net_pnl"] == 0.5  # SELL ledger excludes the already recorded entry fee.
    outcome = _closed_lot_memory_outcome([row])
    assert outcome["net_pnl"] == -0.5  # Brain must classify this as a loss.
    assert outcome["entry_cost_basis"] == 101
    assert outcome["quantity"] == 1
    assert _closed_lot_memory_outcome([{**row, "round_trip_net_pnl": None}]) is None
    assert _closed_lot_memory_outcome([{**row, "broker_mode": "LIVE"}]) is None
    assert _closed_lot_memory_outcome([]) is None


def test_runtime_brain_handoff_charges_entry_and_exit_fees_once(monkeypatch):
    import sys
    from types import SimpleNamespace
    import paper_execution_accounting as accounting
    import paper_execution_reality as reality
    import profit_attribution
    saved = []
    lot = PositionLot(lot_id="lot", symbol="TEST-USD", market="crypto", bucket="Tactical", strategy="test",
                      opened_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
                      quantity_opened=2, quantity_remaining=2, entry_price=100, entry_fees=2)
    def attribution(conn, **kwargs):
        return [row.to_dict() for row in fee_aware_fifo_close_lots(
            [lot], quantity=kwargs["quantity"], exit_price=kwargs["price"],
            exit_time=datetime(2026, 1, 2, tzinfo=timezone.utc), fees=kwargs["fees"])]
    fake = SimpleNamespace(allocate_purchase=lambda **kwargs: None, allocate_sale=lambda **kwargs: None,
                           _record_buy_attribution=lambda *args, **kwargs: None,
                           _record_sell_attribution=attribution,
                           record_closed_trade_memory=lambda **kwargs: saved.append(kwargs),
                           _buy=lambda *args, **kwargs: None)
    def close(market, position, price, reason, **kwargs):
        fake._record_sell_attribution(None, market=market, symbol=position["symbol"], quantity=1, price=price)
        fake.record_closed_trade_memory(market=market, symbol=position["symbol"], quantity=1,
                                       exit_price=price, pnl=1, position=position, exit_reason=reason)
        return True
    fake._execute_close_position = close
    monkeypatch.setitem(sys.modules, "oracle_bot", fake)
    monkeypatch.setenv("EXECUTION_MODE", "paper")
    monkeypatch.setattr(reality, "simulate_fill", reality.simulate_fill)
    monkeypatch.setattr(profit_attribution, "fifo_close_lots", profit_attribution.fifo_close_lots)
    monkeypatch.setattr(accounting, "_latest_trade", lambda *args: None)
    accounting.install_paper_execution_accounting()
    assert fake._execute_close_position("crypto", {"symbol": "TEST-USD"}, 101, "exit",
                                        quote_metadata={"paper_fee_pct": 0.001}) is True
    assert len(saved) == 1
    assert saved[0]["pnl"] == -0.101
    assert saved[0]["canonical_outcome"]["entry_cost_basis"] == 101
    assert accounting._fee_context.get().get("closed_outcomes") is None
