from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import paper_broker
from paper_execution_accounting import (
    _fee_aware_fifo_close_lots,
    _fill_capacity,
    _simulate_fill_explicit_fee,
)
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
    rows = _fee_aware_fifo_close_lots(
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
