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


class ExactTradeConn:
    def __init__(self, rows=None, fail_on=None):
        self.rows = rows or {}
        self.fail_on = fail_on
        self.statements = []

    def execute(self, sql, params):
        self.statements.append((sql, params))
        if self.fail_on and self.fail_on in sql:
            raise RuntimeError("forced atomic transaction failure")
        class Result:
            def __init__(self, value): self.value = value
            def fetchone(self): return self.value
        if "FROM trades WHERE id=%s FOR UPDATE" in sql:
            return Result(self.rows.get(params[0]))
        return Result(None)


def _exact_trade(*, trade_id, side, price=100.05, quantity=2):
    return {"id": trade_id, "market": "crypto", "symbol": "TEST-USD",
            "side": side, "price": price, "quantity": quantity,
            "value": price * quantity}


def _exact_audit(conn, *, side="BUY", trade_id=42, price=100.05,
                 quantity=2, lot_links=None):
    from paper_execution_accounting import _record_order_and_fill
    _record_order_and_fill(
        conn, execution_trade_id=trade_id, market="crypto", symbol="TEST-USD",
        side=side, quantity=quantity, price=price, reference_price=100,
        fee_amount=price * quantity * 0.001, requested_notional=500,
        lot_links=lot_links or [("lot-1", "ledger-1", quantity)],
        quote={"provider": "quote-provider"}, reason="entry",
    )


@pytest.mark.parametrize("side", ["BUY", "SELL"])
def test_exact_fill_audit_uses_same_transaction_and_canonical_trade_id(monkeypatch, side):
    import database
    monkeypatch.setattr(database, "connect", lambda: (_ for _ in ()).throw(
        AssertionError("paper audit must never open a second connection")))
    conn = ExactTradeConn({42: _exact_trade(trade_id=42, side=side)})
    _exact_audit(conn, side=side)
    order = next((sql, params) for sql, params in conn.statements
                 if "INSERT INTO paper_orders" in sql)
    persisted = next((sql, params) for sql, params in conn.statements
                     if "INSERT INTO paper_fills" in sql)
    fee_update = next((sql, params) for sql, params in conn.statements
                      if "UPDATE trades SET fees" in sql)
    link = next((sql, params) for sql, params in conn.statements
                if "INSERT INTO paper_fill_lot_links" in sql)
    assert "FOR UPDATE" in conn.statements[0][0]
    assert conn.statements[0][1] == (42,)
    assert order[1][12] == 100.05
    assert persisted[1][2] == 42
    assert persisted[1][8] == 100.05
    assert persisted[1][9] == 100.05 * persisted[1][6]
    assert persisted[1][10] == pytest.approx(0.2001)
    assert fee_update[1][2] == 42
    assert link[1][0] == persisted[1][0]
    assert link[1][1:3] == ("lot-1", "ledger-1")


def test_concurrent_same_symbol_executions_cannot_select_wrong_trade():
    one = ExactTradeConn({100: _exact_trade(trade_id=100, side="BUY"),
                          101: _exact_trade(trade_id=101, side="BUY")})
    _exact_audit(one, trade_id=100, lot_links=[("lot-a", "ledger-a", 2)])
    _exact_audit(one, trade_id=101, lot_links=[("lot-b", "ledger-b", 2)])
    checked_ids = [params[0] for sql, params in one.statements if "FROM trades WHERE id=%s FOR UPDATE" in sql]
    filled_ids = [params[2] for sql, params in one.statements if "INSERT INTO paper_fills" in sql]
    assert checked_ids == filled_ids == [100, 101]
    assert all("ORDER BY id DESC" not in sql for sql, _ in one.statements)


def test_failing_fill_lot_link_raises_to_roll_back_all_execution_writes():
    conn = ExactTradeConn({42: _exact_trade(trade_id=42, side="SELL")},
                          fail_on="INSERT INTO paper_fill_lot_links")
    with pytest.raises(RuntimeError, match="forced atomic"):
        _exact_audit(conn, side="SELL")


def test_missing_or_conflicting_canonical_trade_cannot_invent_fill():
    from paper_execution_accounting import _record_order_and_fill
    conn = ExactTradeConn({42: _exact_trade(trade_id=42, side="BUY")})
    with pytest.raises(ValueError):
        _exact_audit(conn, trade_id=99)
    with pytest.raises(ValueError):
        _exact_audit(conn, price=0)
    with pytest.raises(ValueError, match="quantities"):
        _exact_audit(conn, lot_links=[("lot-a", "ledger-a", 1)])
    assert not any("INSERT INTO paper_fills" in sql for sql, _ in conn.statements)


def test_forward_only_migration_preserves_unlinked_historical_fills():
    sql = (Path(__file__).parent / "migrations" /
           "20261014_atomic_paper_fill_execution_links.sql").read_text()
    assert "canonical_trade_id BIGINT REFERENCES trades(id)" in sql
    assert "CREATE TABLE IF NOT EXISTS paper_fill_lot_links" in sql
    assert "REFERENCES position_lots(lot_id)" in sql
    assert "REFERENCES trade_ledger(trade_id)" in sql
    assert "UPDATE paper_fills" not in sql
    assert "DELETE FROM" not in sql
    code = (Path(__file__).parent / "paper_execution_accounting.py").read_text()
    assert "_latest_trade(" not in code
    assert "_finalize_trade_row(" not in code
    assert "from database import connect" not in code
    bot = (Path(__file__).parent / "oracle_bot.py").read_text()
    assert bot.count("RETURNING id") >= 3
    assert "execution_trade_id=executed_trade" in bot
    assert "execution_trade_id=rotation_executed_trade" in bot
    assert "execution_trade_id=bought_trade" in bot


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
    def attribution(conn, *, market, position, quantity, price, fees, **kwargs):
        assert position["symbol"] == "TEST-USD"
        return [row.to_dict() for row in fee_aware_fifo_close_lots(
            [lot], quantity=quantity, exit_price=price,
            exit_time=datetime(2026, 1, 2, tzinfo=timezone.utc), fees=fees)]
    fake = SimpleNamespace(allocate_purchase=lambda **kwargs: None, allocate_sale=lambda **kwargs: None,
                           _record_buy_attribution=lambda *args, **kwargs: None,
                           _record_sell_attribution=attribution,
                           record_closed_trade_memory=lambda **kwargs: saved.append(kwargs),
                           _buy=lambda *args, **kwargs: None)
    def close(market, position, price, reason, **kwargs):
        fake._record_sell_attribution(None, market=market, position=position, quantity=1, price=price)
        fake.record_closed_trade_memory(market=market, symbol=position["symbol"], quantity=1,
                                       exit_price=price, pnl=1, position=position, exit_reason=reason)
        return True
    fake._execute_close_position = close
    monkeypatch.setitem(sys.modules, "oracle_bot", fake)
    monkeypatch.setenv("EXECUTION_MODE", "paper")
    monkeypatch.setattr(reality, "simulate_fill", reality.simulate_fill)
    monkeypatch.setattr(profit_attribution, "fifo_close_lots", profit_attribution.fifo_close_lots)
    accounting.install_paper_execution_accounting()
    assert fake._execute_close_position("crypto", {"symbol": "TEST-USD"}, 101, "exit",
                                        quote_metadata={"paper_fee_pct": 0.001}) is True
    assert len(saved) == 1
    assert saved[0]["pnl"] == -0.101
    assert saved[0]["canonical_outcome"]["entry_cost_basis"] == 101
    assert accounting._fee_context.get().get("closed_outcomes") is None
