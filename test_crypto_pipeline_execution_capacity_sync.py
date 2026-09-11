from __future__ import annotations

from types import SimpleNamespace

import crypto_pipeline_integrity_runtime as runtime
import oracle_bot


class _Log:
    def info(self, *args, **kwargs):
        return None


def _active_paper(monkeypatch):
    monkeypatch.setenv("EXECUTION_MODE", "paper")
    monkeypatch.setenv("PAPER_AUTONOMOUS_LEARNING", "true")
    monkeypatch.setenv("ENABLE_BROKER_SUBMISSION", "false")
    monkeypatch.setenv("LIVE_TRADING_ARMED", "false")
    monkeypatch.setattr(oracle_bot, "market_leverage_limit", lambda market: 1.0)
    monkeypatch.setattr(oracle_bot, "PAPER_MAX_MARGIN_UTILIZATION_PCT", 0.82)


def _plan(*, equity=172.37, gross_exposure=141.2864, buying_power=31.09, amount=13.79, floor=2.0):
    return {
        "equity": equity,
        "gross_exposure": gross_exposure,
        "buying_power": buying_power,
        "allocations": [
            {
                "symbol": "NEAR-USD",
                "amount": amount,
                "meaningful_entry_floor": floor,
                "liquidity": {
                    "executable_order_value": amount,
                    "partial_sizing": False,
                },
            }
        ],
        "rejections": [],
    }


def test_optimizer_capacity_below_floor_becomes_watch(monkeypatch):
    _active_paper(monkeypatch)
    worker = SimpleNamespace(log=_Log())
    # maximum_gross = 172.37 * 0.82 = 141.3434, leaving about $0.057 room.
    plan = runtime._apply_optimizer_execution_capacity_sync(worker, _plan())

    assert plan["allocations"] == []
    assert plan["rejections"][0]["reason"] == "paper_execution_capacity_below_floor"
    assert plan["rejections"][0]["watch_only"] is True
    assert 0.05 <= plan["rejections"][0]["proposed_amount"] <= 0.06
    assert plan["paper_execution_capacity"]["executable_room"] < 2.0


def test_optimizer_allocation_is_clipped_to_execution_room(monkeypatch):
    _active_paper(monkeypatch)
    worker = SimpleNamespace(log=_Log())
    equity = 172.37
    maximum_gross = equity * 0.82
    plan = runtime._apply_optimizer_execution_capacity_sync(
        worker,
        _plan(equity=equity, gross_exposure=maximum_gross - 5.0, buying_power=31.09),
    )

    assert len(plan["allocations"]) == 1
    allocation = plan["allocations"][0]
    assert allocation["amount"] == 5.0
    assert allocation["execution_capacity_clipped"] is True
    assert allocation["optimizer_original_amount"] == 13.79
    assert plan["paper_execution_capacity"]["remaining_after_plan"] == 0.0


def test_capacity_sync_never_changes_nonpaper_or_armed_plan(monkeypatch):
    monkeypatch.setenv("EXECUTION_MODE", "paper")
    monkeypatch.setenv("PAPER_AUTONOMOUS_LEARNING", "true")
    monkeypatch.setenv("ENABLE_BROKER_SUBMISSION", "false")
    monkeypatch.setenv("LIVE_TRADING_ARMED", "true")
    worker = SimpleNamespace(log=_Log())
    original = _plan()

    plan = runtime._apply_optimizer_execution_capacity_sync(worker, original)

    assert plan["allocations"][0]["amount"] == 13.79
    assert "paper_execution_capacity" not in plan
