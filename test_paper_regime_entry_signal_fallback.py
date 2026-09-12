from __future__ import annotations

from contextlib import contextmanager
from types import SimpleNamespace
import sys

import paper_regime_entry_signal_fallback as fallback


def _paper(monkeypatch):
    monkeypatch.setenv("EXECUTION_MODE", "paper")
    monkeypatch.setenv("PAPER_AUTONOMOUS_LEARNING", "true")
    monkeypatch.setenv("ENABLE_BROKER_SUBMISSION", "false")
    monkeypatch.setenv("LIVE_TRADING_ARMED", "false")


def test_merge_entry_features_preserves_immutable_ledger_values():
    merged = fallback._merge_entry_features(
        {"trend_strength": 0.12, "momentum_20d": 0.07},
        {"trend_strength": -0.80, "momentum_20d": -0.40, "volatility_20d": 0.42},
    )
    assert merged["trend_strength"] == 0.12
    assert merged["momentum_20d"] == 0.07
    assert merged["volatility_20d"] == 0.42


def test_entry_signal_features_reads_exact_signal_payload_only():
    class Conn:
        def execute(self, sql, params):
            assert "FROM signals" in sql
            assert params == ("42",)
            return SimpleNamespace(fetchone=lambda: {
                "payload": {
                    "trend_strength": 0.10,
                    "momentum_20d": 0.08,
                    "volatility_20d": 0.31,
                }
            })

    assert fallback._entry_signal_features(Conn(), 42) == {
        "trend_strength": 0.10,
        "momentum_20d": 0.08,
        "volatility_20d": 0.31,
    }


def test_repair_unknown_regime_uses_entry_signal_without_touching_economics(monkeypatch):
    _paper(monkeypatch)
    updates = []

    class Result:
        def __init__(self, rows=None, one=None):
            self._rows = rows or []
            self._one = one

        def fetchall(self):
            return self._rows

        def fetchone(self):
            return self._one

    class Conn:
        def execute(self, sql, params=None):
            if "FROM paper_regime_trade_metrics" in sql:
                return Result(rows=[{
                    "trade_id": "trade-1",
                    "regime": "range__vol_unknown",
                    "entry_signal_id": "77",
                    "feature_snapshot": {"trend_strength": 0.09},
                }])
            if "FROM signals" in sql:
                assert params == ("77",)
                return Result(one={"payload": {
                    "trend_strength": 0.09,
                    "momentum_20d": 0.06,
                    "volatility_20d": 0.25,
                }})
            if "UPDATE paper_regime_trade_metrics" in sql:
                updates.append(params)
                return Result()
            raise AssertionError(sql)

    @contextmanager
    def connect():
        yield Conn()

    monkeypatch.setitem(sys.modules, "database", SimpleNamespace(connect=connect))

    repaired = fallback.repair_unknown_regimes()
    assert repaired == 1
    assert updates == [(fallback._SCHEMA_VERSION and "trend_up__low_vol", fallback._SCHEMA_VERSION, "trade-1")]


def test_repair_does_not_invent_regime_when_entry_signal_lacks_volatility(monkeypatch):
    _paper(monkeypatch)
    updates = []

    class Result:
        def __init__(self, rows=None, one=None):
            self._rows = rows or []
            self._one = one
        def fetchall(self):
            return self._rows
        def fetchone(self):
            return self._one

    class Conn:
        def execute(self, sql, params=None):
            if "FROM paper_regime_trade_metrics" in sql:
                return Result(rows=[{
                    "trade_id": "trade-2",
                    "regime": "range__vol_unknown",
                    "entry_signal_id": "88",
                    "feature_snapshot": {},
                }])
            if "FROM signals" in sql:
                return Result(one={"payload": {"trend_strength": 0.10, "momentum_20d": 0.09}})
            if "UPDATE paper_regime_trade_metrics" in sql:
                updates.append(params)
                return Result()
            raise AssertionError(sql)

    @contextmanager
    def connect():
        yield Conn()

    monkeypatch.setitem(sys.modules, "database", SimpleNamespace(connect=connect))
    assert fallback.repair_unknown_regimes() == 0
    assert updates == []


def test_active_fails_closed_for_live(monkeypatch):
    _paper(monkeypatch)
    assert fallback.active() is True
    monkeypatch.setenv("LIVE_TRADING_ARMED", "true")
    assert fallback.active() is False
