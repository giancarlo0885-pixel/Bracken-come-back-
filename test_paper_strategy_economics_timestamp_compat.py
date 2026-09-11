from datetime import datetime, timedelta, timezone
import sys
import types

import paper_strategy_economics as econ


def test_canonical_ledger_casts_text_timestamps_before_epoch_filter(monkeypatch):
    start = datetime.now(timezone.utc) - timedelta(hours=1)
    entry = datetime.now(timezone.utc) - timedelta(minutes=9)
    exit_ = datetime.now(timezone.utc)
    captured = {}

    def fake_rows(sql, params=None):
        captured["sql"] = sql
        captured["params"] = params
        # Emulate PostgreSQL returning typed datetimes only when the SQL casts
        # the persisted TEXT timestamps to timestamptz.
        if "NULLIF(exit_time,'')::timestamptz" not in sql:
            raise TypeError("text timestamp compared with timestamptz")
        return [
            {
                "strategy": "20d momentum +12.0%; RSI 61.0; Oracle Council V3 consensus BUY",
                "symbol": "DOT-USD",
                "net_pnl": -0.11,
                "gross_pnl": -0.07,
                "fees": 0.04,
                "return_pct": -0.2,
                "entry_time": entry,
                "exit_time": exit_,
                "model": None,
                "model_version": None,
            }
        ]

    fake_database = types.SimpleNamespace(
        row=lambda sql, params: {"started_at": start},
        rows=fake_rows,
    )
    monkeypatch.setitem(sys.modules, "database", fake_database)

    records = econ._ledger_records("oracle_council_v3")

    assert len(records) == 1
    assert records[0]["symbol"] == "DOT-USD"
    assert "NULLIF(entry_time,'')::timestamptz AS entry_time" in captured["sql"]
    assert "NULLIF(exit_time,'')::timestamptz AS exit_time" in captured["sql"]
    assert captured["params"] == (start,)
    assert round(econ._holding_minutes(records[0]), 6) == 9.0


def test_legacy_fallback_normalizes_dynamic_council_reason(monkeypatch):
    start = datetime.now(timezone.utc) - timedelta(hours=1)
    exit_ = datetime.now(timezone.utc)
    calls = []

    def fake_rows(sql, params=None):
        calls.append(sql)
        if "FROM trade_ledger" in sql:
            raise RuntimeError("canonical ledger unavailable")
        assert "NULLIF(created_at,'')::timestamptz" in sql
        return [
            {
                "strategy": "20d momentum +8.0%; RSI 57.0; Oracle Council V3 consensus BUY",
                "symbol": "NEAR-USD",
                "net_pnl": 0.13,
                "gross_pnl": 0.17,
                "fees": 0.04,
                "return_pct": None,
                "entry_time": None,
                "exit_time": exit_,
                "model": None,
                "model_version": None,
            },
            {
                "strategy": "unrelated_strategy",
                "symbol": "BTC-USD",
                "net_pnl": 1.0,
                "gross_pnl": 1.1,
                "fees": 0.1,
                "return_pct": None,
                "entry_time": None,
                "exit_time": exit_,
                "model": None,
                "model_version": None,
            },
        ]

    fake_database = types.SimpleNamespace(
        row=lambda sql, params: {"started_at": start},
        rows=fake_rows,
    )
    monkeypatch.setitem(sys.modules, "database", fake_database)

    records = econ._ledger_records("oracle_council_v3")

    assert len(records) == 1
    assert records[0]["symbol"] == "NEAR-USD"
    assert len(calls) == 2
