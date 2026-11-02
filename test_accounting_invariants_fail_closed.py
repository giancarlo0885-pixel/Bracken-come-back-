"""Fail-closed accounting health when canonical portfolio values are unavailable."""

import pytest

import accounting_invariants as accounting


@pytest.mark.parametrize(
    ("portfolio", "snapshot_equity"),
    [
        (None, 0.0),
        ({"cash": float("nan"), "margin_debt": 0.0, "margin_interest_accrued": 0.0}, 0.0),
        ({"cash": 0.0, "margin_debt": 0.0, "margin_interest_accrued": 0.0}, float("nan")),
    ],
    ids=["missing-portfolio", "nonfinite-cash", "nonfinite-snapshot-equity"],
)
def test_market_invariant_rejects_missing_or_nonfinite_canonical_values(
    monkeypatch, portfolio, snapshot_equity
):
    """A zero-equity snapshot cannot validate an absent or corrupted portfolio."""

    def fake_row(sql, params=()):
        if "FROM portfolios" in sql:
            return portfolio
        if "FROM positions" in sql:
            return {"positions_value": 0.0, "invalid_positions": 0}
        if "FROM equity_snapshots" in sql:
            return {"equity": snapshot_equity}
        raise AssertionError(sql)

    monkeypatch.setattr(accounting, "row", fake_row)
    result = accounting._market_invariant("crypto", tolerance_pct=0.001)
    assert result.ok is False


def test_market_invariant_preserves_valid_zero_equity_control(monkeypatch):
    def fake_row(sql, params=()):
        if "FROM portfolios" in sql:
            return {"cash": 0.0, "margin_debt": 0.0, "margin_interest_accrued": 0.0}
        if "FROM positions" in sql:
            return {"positions_value": 0.0, "invalid_positions": 0}
        if "FROM equity_snapshots" in sql:
            return {"equity": 0.0}
        raise AssertionError(sql)

    monkeypatch.setattr(accounting, "row", fake_row)
    result = accounting._market_invariant("crypto", tolerance_pct=0.001)
    assert result.ok is True


@pytest.mark.parametrize("field", ["margin_debt", "margin_interest_accrued"])
@pytest.mark.parametrize("bad_value", [float("nan"), float("inf")], ids=["nan", "infinity"])
def test_market_invariant_rejects_nonfinite_liability_evidence(
    monkeypatch, field, bad_value
):
    """An invalid debt or interest amount must not silently become zero."""

    portfolio = {"cash": 0.0, "margin_debt": 0.0, "margin_interest_accrued": 0.0}
    portfolio[field] = bad_value

    def fake_row(sql, params=()):
        if "FROM portfolios" in sql:
            return portfolio
        if "FROM positions" in sql:
            return {"positions_value": 0.0, "invalid_positions": 0}
        if "FROM equity_snapshots" in sql:
            return {"equity": 0.0}
        raise AssertionError(sql)

    monkeypatch.setattr(accounting, "row", fake_row)
    assert accounting._market_invariant("crypto", tolerance_pct=0.001).ok is False
