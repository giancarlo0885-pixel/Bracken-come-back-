"""Final SELL learning requires committed execution, fill, and lot evidence."""
from __future__ import annotations

from contextlib import contextmanager
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

import oracle_bot
import paper_execution_accounting as accounting
import paper_execution_reality as reality
import profit_attribution
from profit_attribution import PositionLot


# Exercise the actual final mutation, with installers applied at test runtime.
_execute_close_position = oracle_bot._execute_close_position
_record_sell_attribution = oracle_bot._record_sell_attribution


class _Result:
    def __init__(self, row=None, rows=()):
        self.row, self.rows = row, rows

    def fetchone(self):
        return self.row

    def fetchall(self):
        return list(self.rows)


class _ExecutionConnection:
    def __init__(self, *, commit_fails=False):
        self.active = False
        self.pending = []
        self.committed = []
        self.commit_fails = commit_fails

    def execute(self, statement, params=()):
        assert self.active, "Execution evidence must use the active transaction"
        normalized = " ".join(statement.split())
        self.pending.append((normalized, params))
        if "SELECT * FROM portfolios" in normalized:
            return _Result({"cash": 1000, "margin_debt": 0})
        if "SELECT * FROM position_lots" in normalized:
            return _Result(rows=[{
                "lot_id": "entry-lot-uuid", "symbol": "TEST-USD", "market": "crypto",
                "opened_at": datetime(2026, 1, 1, tzinfo=timezone.utc),
                "quantity_opened": 1, "quantity_remaining": 1,
                "entry_price": 100, "entry_fees": 1, "entry_signal_id": "entry-signal",
                "entry_decision_id": "entry-decision", "broker_mode": "PAPER",
                "account_environment": "PAPER",
            }])
        if "INSERT INTO trades" in normalized:
            assert "RETURNING id" in normalized
            return _Result({"id": 41})
        return _Result()

    @contextmanager
    def transaction(self):
        self.active = True
        try:
            yield self
            if self.commit_fails:
                raise RuntimeError("simulated commit failure")
            self.committed = deepcopy(self.pending)
        finally:
            self.pending.clear()
            self.active = False


@pytest.mark.parametrize("failure", [None, "fill_write", "commit", "memory"])
def test_final_sell_publishes_learning_only_after_atomic_execution_commit(monkeypatch, failure):
    conn = _ExecutionConnection(commit_fails=failure == "commit")
    memory = []
    persisted = []

    monkeypatch.setenv("EXECUTION_MODE", "paper")
    monkeypatch.setattr(oracle_bot, "connect", conn.transaction)
    monkeypatch.setattr(oracle_bot, "build_account", lambda *args: SimpleNamespace())
    monkeypatch.setattr(oracle_bot, "_try_execution_claim", lambda *args, **kwargs: (True, "exact-claim", ""))
    monkeypatch.setattr(oracle_bot, "_complete_execution_claim", lambda active, key: None)
    monkeypatch.setattr(oracle_bot, "_shared_risk_gate", lambda **kwargs: (True, "", {}))
    monkeypatch.setattr(oracle_bot, "allocate_sale", lambda **kwargs: (1100, 0, 0))
    monkeypatch.setattr(oracle_bot, "allocate_purchase", lambda **kwargs: None)
    monkeypatch.setattr(oracle_bot, "_record_buy_attribution", lambda *args, **kwargs: None)
    monkeypatch.setattr(oracle_bot, "_record_sell_attribution", _record_sell_attribution)
    monkeypatch.setattr(oracle_bot, "_paper_execution_accounting_installed", False, raising=False)
    monkeypatch.setattr(oracle_bot, "_execute_close_position", _execute_close_position)
    for name in ("_buy", "fifo_close_lots", "simulate_fill"):
        monkeypatch.setattr(oracle_bot, name, getattr(oracle_bot, name))
    monkeypatch.setattr(profit_attribution, "fifo_close_lots", profit_attribution.fifo_close_lots)
    monkeypatch.setattr(reality, "simulate_fill", reality.simulate_fill)

    def persist(active, **kwargs):
        assert active is conn and conn.active
        assert kwargs["execution_trade_id"] == 41
        assert kwargs["lot_links"][0][0] == "entry-lot-uuid"
        ledger_insert = next(params for sql, params in conn.pending if "INSERT INTO trade_ledger" in sql)
        assert kwargs["lot_links"][0][1] == ledger_insert[0]
        assert kwargs["lot_links"][0][2] == 1
        persisted.append(kwargs)
        conn.pending.append(("paper_fills_and_links", kwargs["execution_trade_id"]))
        if failure == "fill_write":
            raise RuntimeError("simulated fill persistence failure")

    def learn(**kwargs):
        assert not conn.active
        assert conn.committed, "An independent outcome requires a committed canonical close"
        memory.append(kwargs)
        if failure == "memory":
            raise RuntimeError("simulated post-commit memory failure")
        return True

    monkeypatch.setattr(oracle_bot, "record_closed_trade_memory", learn)
    monkeypatch.setattr(accounting, "_record_order_and_fill", persist)
    accounting.install_paper_execution_accounting()
    result = oracle_bot._execute_close_position(
        "crypto", {"symbol": "TEST-USD", "quantity": 1, "average_price": 100},
        101, "test-exit", quote_metadata={"paper_fee_pct": 0.001},
    )
    assert result is (failure not in {"fill_write", "commit"})
    assert len(persisted) == 1
    assert persisted[0]["fee_amount"] == pytest.approx(0.101)
    if failure in {"fill_write", "commit"}:
        assert conn.committed == []
        assert memory == []
    else:
        assert len(memory) == 1
        assert memory[0]["pnl"] == pytest.approx(-0.101)
        assert memory[0]["canonical_outcome"]["entry_cost_basis"] == 101
        assert memory[0]["entry_provenance"]["entry_signal_id"] == "entry-signal"
        assert any(sql == "paper_fills_and_links" for sql, _ in conn.committed)
    assert accounting._fee_context.get().get("closed_outcomes") is None


def test_accounting_only_installer_preserves_fifo_entry_identity_and_single_fee_charges(monkeypatch):
    """Accounting must be correct before workers install the same fee policy."""
    import sys

    fake = SimpleNamespace(
        allocate_purchase=lambda **kwargs: None, allocate_sale=lambda **kwargs: None,
        _record_buy_attribution=lambda *args, **kwargs: None,
        _record_sell_attribution=lambda *args, **kwargs: [],
        record_closed_trade_memory=lambda **kwargs: None,
        _buy=lambda *args, **kwargs: None,
        _execute_close_position=lambda *args, **kwargs: None,
    )
    monkeypatch.setitem(sys.modules, "oracle_bot", fake)
    monkeypatch.setenv("EXECUTION_MODE", "paper")
    monkeypatch.setattr(reality, "simulate_fill", reality.simulate_fill)
    monkeypatch.setattr(profit_attribution, "fifo_close_lots", profit_attribution.fifo_close_lots)
    accounting.install_paper_execution_accounting()
    opened = datetime(2026, 1, 1, tzinfo=timezone.utc)
    lots = [
        PositionLot(lot_id=f"lot:{index}", symbol="TEST-USD", market="crypto",
                    bucket="Tactical", strategy="test", opened_at=opened + timedelta(minutes=index),
                    quantity_opened=2, quantity_remaining=2, entry_price=100, entry_fees=2,
                    entry_signal_id=f"signal:{index}", entry_quote_id=f"quote:{index}",
                    entry_forecast_id=f"forecast:{index}", feature_snapshot={"entry": index})
        for index in range(2)
    ]
    rows = fake.fifo_close_lots(
        list(reversed(lots)), quantity=3, exit_price=102,
        exit_time=opened + timedelta(hours=1), fees=0.6,
    )
    assert [row.quantity for row in rows] == [2, 1]
    assert [row.fees for row in rows] == pytest.approx([0.4, 0.2])
    assert [row.net_pnl for row in rows] == pytest.approx([3.6, 1.8])
    assert [row.round_trip_net_pnl for row in rows] == pytest.approx([1.6, 0.8])
    assert [row.entry_cost_basis for row in rows] == pytest.approx([202, 101])
    assert [row.entry_signal_id for row in rows] == ["signal:0", "signal:1"]
    assert [row.entry_quote_id for row in rows] == ["quote:0", "quote:1"]
    assert [row.entry_forecast_id for row in rows] == ["forecast:0", "forecast:1"]
    assert [row.feature_snapshot for row in rows] == [{"entry": 0}, {"entry": 1}]
    assert [lot.quantity_remaining for lot in lots] == [0, 1]
    # Entry fees on the BUY allocation plus SELL ledger net equal full-cost net.
    assert sum(row.net_pnl for row in rows) - 3 == pytest.approx(sum(row.round_trip_net_pnl for row in rows))
