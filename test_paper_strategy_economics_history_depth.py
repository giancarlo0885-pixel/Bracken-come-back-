from __future__ import annotations

from datetime import datetime, timedelta, timezone
import sys
import types

import paper_strategy_economics as econ


def test_ledger_records_keep_more_than_250_normalized_epoch_closes(monkeypatch):
    start = datetime.now(timezone.utc) - timedelta(hours=6)
    closed_at = datetime.now(timezone.utc)
    records = [
        {
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
        for index in range(300)
    ]
    fake_database = types.SimpleNamespace(
        row=lambda sql, params: {"started_at": start},
        rows=lambda sql, params=None: records,
    )
    monkeypatch.setitem(sys.modules, "database", fake_database)

    matched = econ._ledger_records("oracle_council_v3")

    assert len(matched) == 300
    assert matched[0]["symbol"] == "TEST0-USD"
    assert matched[-1]["symbol"] == "TEST299-USD"
