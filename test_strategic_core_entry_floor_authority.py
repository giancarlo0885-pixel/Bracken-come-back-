from types import SimpleNamespace

import strategic_core_rebalance_runtime as runtime


class Signal:
    def __init__(self, **values):
        self.__dict__.update(values)


def _worker(decision=None):
    return SimpleNamespace(
        _core_rebalance_optimizer_decisions={"LINK-USD": decision or {}},
    )


def test_producer_floor_is_not_used_when_optimizer_has_not_evaluated_candidate():
    signal = Signal(symbol="LINK-USD", core_meaningful_entry_floor=None, v39_optimizer_allocation={})
    assert runtime._effective_meaningful_floor(_worker(), signal) == 0.0


def test_optimizer_decision_is_authoritative_meaningful_entry_floor():
    signal = Signal(
        symbol="LINK-USD",
        core_meaningful_entry_floor=18.39,
        v39_optimizer_allocation={},
    )
    worker = _worker({
        "meaningful_entry_floor": 2.00,
        "entry_floor_mode": "adaptive_equity_spread_liquidity_confidence",
    })
    assert runtime._effective_meaningful_floor(worker, signal) == 2.00
