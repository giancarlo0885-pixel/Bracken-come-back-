"""Synthetic regression: corrupt open-position values cannot renew paper research capacity.

The final paper-only guard must not interpret missing or invalid exposure as zero.
No production database, broker or trading settings are touched.
"""
from __future__ import annotations

from types import SimpleNamespace
import sys

import pytest

from paper_strategy_execution_guard import _remaining_cumulative_tier_capacity


def _read_position(monkeypatch, position):
    monkeypatch.setitem(
        sys.modules, "oracle_bot", SimpleNamespace(row=lambda *args, **kwargs: position)
    )


@pytest.mark.parametrize("quantity", [None, "nan", "inf", "-inf", -1, 0, True])
def test_invalid_open_quantity_does_not_restore_full_research_cap(monkeypatch, quantity):
    _read_position(monkeypatch, {"quantity": quantity, "average_price": 150.0})
    assert _remaining_cumulative_tier_capacity("AAVE-USD", 49.57) == (None, 0.0)


@pytest.mark.parametrize("basis", [None, "nan", "inf", "-inf", -1, 0, True])
def test_invalid_open_basis_does_not_restore_full_research_cap(monkeypatch, basis):
    _read_position(
        monkeypatch,
        {"quantity": 0.2, "average_price": basis, "entry_price": None, "current_price": None},
    )
    assert _remaining_cumulative_tier_capacity("AAVE-USD", 49.57) == (None, 0.0)


def test_valid_open_position_still_subtracts_cost_basis(monkeypatch):
    _read_position(monkeypatch, {"quantity": 0.2, "average_price": 150.0})
    assert _remaining_cumulative_tier_capacity("AAVE-USD", 49.57) == (19.57, 30.0)


def test_valid_price_fallback_and_first_entry_preserved(monkeypatch):
    _read_position(
        monkeypatch,
        {"quantity": 0.2, "average_price": None, "entry_price": 150.0, "current_price": 151.0},
    )
    assert _remaining_cumulative_tier_capacity("AAVE-USD", 49.57) == (19.57, 30.0)
    _read_position(monkeypatch, None)
    assert _remaining_cumulative_tier_capacity("AAVE-USD", 49.57) == (49.57, 0.0)
