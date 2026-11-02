"""Regression: boolean telemetry is not a verified monetary return or notional."""
from datetime import datetime, timedelta, timezone

import pytest

from paper_winner_edge_research import discover, prepare


ENTRY = datetime(2026, 10, 12, 12, tzinfo=timezone.utc)
AS_OF = ENTRY + timedelta(days=2)


def verified_lot(**overrides):
    row = {
        "trade_id": "verified-entry-1",
        "market": "crypto",
        "strategy": "council",
        "regime": "trend_up",
        "entry_pattern": "dip_rebound",
        "entry_time": ENTRY.isoformat(),
        "exit_time": (ENTRY + timedelta(minutes=20)).isoformat(),
        "entry_evidence_time": ENTRY.isoformat(),
        "entry_notional": 100.0,
        "net_pnl": 1.0,
        "fifo_complete": True,
        "fill_fee_evidence_verified": True,
        "exact_fill_lot_linkage_verified": True,
        "entry_cohort_verified": True,
    }
    row.update(overrides)
    return row


@pytest.mark.parametrize("field,invalid", [
    ("net_pnl", True),
    ("net_pnl", False),
    ("entry_notional", True),
])
def test_boolean_economics_never_count_as_qualified_forward_outcome(field, invalid):
    row = verified_lot(**{field: invalid})
    valid, excluded = prepare([row], AS_OF)
    assert valid == []
    assert excluded["missing_features_or_economics"] == 1
    forward = discover([row], cutoff=ENTRY - timedelta(days=1), as_of=AS_OF)
    assert forward["forward"]["baseline"]["trades"] == 0
    assert forward["promotion_action"] == "NONE"
    assert forward["execution_impact"] == "NONE"


def test_finite_numeric_and_numeric_text_controls_remain_qualified():
    for value in (1.0, 0.0, -1.0, "1.25"):
        valid, excluded = prepare([verified_lot(net_pnl=value)], AS_OF)
        assert len(valid) == 1
        assert valid[0]["net_return"] == pytest.approx(float(value) / 100.0)
        assert not excluded
