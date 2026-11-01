"""Null optional cost fields must not suppress available fallback friction.

All signals are synthetic. No order, quote, database, or historical mutation.
"""
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

import paper_strategy_economics as economics


def _signal(shape, **fields):
    return fields if shape == "dict" else SimpleNamespace(**fields)


@pytest.mark.parametrize("shape", ["dict", "object"])
def test_null_expected_slippage_uses_available_slippage(shape):
    signal = _signal(shape, symbol="BTC-USD", expected_slippage_pct=None,
                     slippage_pct=1.50, spread_pct=0.0, fee_pct=0.10)
    # 2 * 1.50% slippage + 2 * 0.10% fee.
    assert economics.estimated_round_trip_cost_pct(signal) == pytest.approx(3.20)


@pytest.mark.parametrize("shape", ["dict", "object"])
def test_null_fee_uses_configured_fee_instead_of_hardcoded_default(monkeypatch, shape):
    monkeypatch.setenv("PAPER_ESTIMATED_FEE_PCT", "0.75")
    signal = _signal(shape, symbol="BTC-USD", fee_pct=None)
    assert economics.estimated_round_trip_cost_pct(signal) == pytest.approx(1.83)


@pytest.mark.parametrize("shape", ["dict", "object"])
@pytest.mark.parametrize("invalid", [float("nan"), float("inf"), -0.25, "invalid-cost"])
def test_null_expected_slippage_does_not_hide_invalid_fallback(monkeypatch, shape, invalid):
    signal = _signal(shape, symbol="BTC-USD", expected_slippage_pct=None,
                     slippage_pct=invalid)
    lookup = Mock(side_effect=AssertionError("invalid friction must fail before cohort lookup"))
    monkeypatch.setattr(economics, "strategy_economics", lookup)
    allowed, reason, edge, cost = economics.fee_edge_allows_entry(signal)
    assert not allowed
    assert reason == "paper_explicit_cost_invalid"
    assert edge is None
    assert cost == float("inf")
    lookup.assert_not_called()


@pytest.mark.parametrize("shape", ["dict", "object"])
def test_null_fee_does_not_hide_invalid_configured_fee(monkeypatch, shape):
    monkeypatch.setenv("PAPER_ESTIMATED_FEE_PCT", "invalid-cost")
    signal = _signal(shape, symbol="BTC-USD", fee_pct=None)
    assert economics.estimated_round_trip_cost_pct(signal) == float("inf")


def test_explicit_valid_expected_slippage_still_takes_precedence():
    signal = {"symbol": "BTC-USD", "expected_slippage_pct": 0.25,
              "slippage_pct": float("nan"), "fee_pct": 0.10}
    assert economics.estimated_round_trip_cost_pct(signal) == pytest.approx(0.70)


def test_absent_fee_uses_configured_fee(monkeypatch):
    monkeypatch.setenv("PAPER_ESTIMATED_FEE_PCT", "0.75")
    assert economics.estimated_round_trip_cost_pct({"symbol": "BTC-USD"}) == pytest.approx(1.83)
