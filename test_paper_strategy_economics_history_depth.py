from __future__ import annotations

from datetime import datetime, timedelta, timezone
import sys
import types

import paper_strategy_economics as econ


def _oracle_record(index: int, closed_at: datetime) -> dict[str, object]:
    return {
        "strategy": f"20d momentum +{index / 10:.1f}%; RSI 63.0; Oracle Council V3 consensus BUY",
        "symbol": f"TEST{index}-USD",
        "net_pnl": -0.01,
        "gross_pnl": -0.005,
        "fees": 0.005,
        "return_pct": -0.1,
        "entry_time": closed_at - timedelta(minutes=10),
        "exit_time": closed_at,
        "model": None,
        "model_version": None,
    }


def test_ledger_records_keep_more_than_250_normalized_epoch_closes(monkeypatch):
    start = datetime.now(timezone.utc) - timedelta(hours=6)
    closed_at = datetime.now(timezone.utc)
    records = [_oracle_record(index, closed_at) for index in range(300)]
    fake_database = types.SimpleNamespace(
        row=lambda sql, params: {"started_at": start},
        rows=lambda sql, params=None: records,
    )
    monkeypatch.setitem(sys.modules, "database", fake_database)

    matched = econ._ledger_records("oracle_council_v3")

    assert len(matched) == 300
    assert matched[0]["symbol"] == "TEST0-USD"
    assert matched[-1]["symbol"] == "TEST299-USD"


def test_ledger_records_filters_strategy_before_global_history_limit(monkeypatch):
    """Unrelated closes must not evict Council samples from the 1,000-row window."""
    start = datetime.now(timezone.utc) - timedelta(hours=6)
    closed_at = datetime.now(timezone.utc)
    oracle_records = [_oracle_record(index, closed_at) for index in range(300)]
    observed: list[tuple[str, tuple[object, ...]]] = []

    def rows(sql: str, params=None):
        bound = tuple(params or ())
        observed.append((sql, bound))
        # Simulate PostgreSQL honoring the strategy predicate. Before the fix the
        # query had no strategy predicate, so the newest 1,000 unrelated closes
        # would be returned and the Council sample would appear to shrink.
        if "%oracle council v3%" in bound and "oracle_council_v3" in bound:
            return oracle_records
        return [
            {**_oracle_record(index, closed_at), "strategy": "unrelated_strategy"}
            for index in range(1000)
        ]

    fake_database = types.SimpleNamespace(
        row=lambda sql, params: {"started_at": start},
        rows=rows,
    )
    monkeypatch.setitem(sys.modules, "database", fake_database)

    matched = econ._ledger_records("oracle_council_v3")

    assert len(matched) == 300
    assert any("LOWER(COALESCE(strategy,''))" in sql for sql, _ in observed)
    assert any("%oracle council v3%" in params for _, params in observed)
