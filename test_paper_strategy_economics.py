from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
import sys
import types

import paper_strategy_economics as econ


def _paper(monkeypatch):
    monkeypatch.setenv("EXECUTION_MODE", "paper")
    monkeypatch.setenv("PAPER_AUTONOMOUS_LEARNING", "true")
    monkeypatch.setenv("ENABLE_BROKER_SUBMISSION", "false")
    monkeypatch.setenv("LIVE_TRADING_ARMED", "false")


def test_active_is_fail_closed_for_live(monkeypatch):
    _paper(monkeypatch)
    assert econ.active() is True
    monkeypatch.setenv("LIVE_TRADING_ARMED", "true")
    assert econ.active() is False


def test_fee_edge_blocks_explicit_edge_below_cost(monkeypatch):
    _paper(monkeypatch)
    monkeypatch.setenv("PAPER_MIN_EDGE_TO_COST_MULTIPLIER", "1.25")
    signal = {
        "expected_return_pct": 0.40,
        "expected_slippage_pct": 0.165,
        "fee_pct": 0.10,
        "spread_pct": 0.02,
    }
    allowed, reason, edge, cost = econ.fee_edge_allows_entry(signal)
    assert allowed is False
    assert edge == 0.40
    assert cost > 0.50
    assert reason.startswith("edge_below_round_trip_cost")


def test_fee_edge_preserves_exploration_when_edge_missing(monkeypatch):
    _paper(monkeypatch)
    allowed, reason, edge, cost = econ.fee_edge_allows_entry({"strategy": "explore"})
    assert allowed is True
    assert reason == "edge_unavailable_exploration"
    assert edge is None
    assert cost > 0


def test_negative_expectancy_reduces_size_but_keeps_exploration(monkeypatch):
    _paper(monkeypatch)
    monkeypatch.setenv("PAPER_STRATEGY_ECON_MIN_SAMPLES", "8")
    monkeypatch.setenv("PAPER_STRATEGY_EXPLORATION_FLOOR", "0.35")
    multiplier = econ._multiplier(
        sample_count=20,
        expectancy=-0.25,
        profit_factor=0.50,
        model_validated=False,
    )
    assert 0.35 <= multiplier < 1.0


def test_positive_size_boost_requires_model_validation(monkeypatch):
    _paper(monkeypatch)
    monkeypatch.setenv("PAPER_STRATEGY_MAX_SIZE_MULTIPLIER", "1.25")
    assert econ._multiplier(
        sample_count=40,
        expectancy=0.15,
        profit_factor=1.40,
        model_validated=False,
    ) == 1.0
    assert econ._multiplier(
        sample_count=40,
        expectancy=0.15,
        profit_factor=1.40,
        model_validated=True,
    ) == 1.25


def test_strategy_identity_prefers_explicit_strategy():
    signal = SimpleNamespace(strategy="momentum_v2", reason="generic")
    assert econ.strategy_identity(signal) == "momentum_v2"


def test_oracle_council_dynamic_rationales_share_stable_strategy_identity():
    first = {
        "strategy": "20d momentum +33.1%; RSI 78.5; trend strong. Oracle Council V3 consensus BUY"
    }
    second = {
        "strategy": "20d momentum +27.4%; RSI 71.2; trend mixed. Oracle Council V3 consensus BUY"
    }
    assert econ.strategy_identity(first) == "oracle_council_v3"
    assert econ.strategy_identity(second) == "oracle_council_v3"


def test_ledger_records_match_dynamic_entry_rationale_by_stable_key(monkeypatch):
    start = datetime.now(timezone.utc) - timedelta(hours=1)
    closed_at = datetime.now(timezone.utc)
    fake_database = types.SimpleNamespace(
        row=lambda sql, params: {"started_at": start},
        rows=lambda sql, params=None: [
            {
                "strategy": "20d momentum +18.3%; RSI 63.0; Oracle Council V3 consensus BUY",
                "symbol": "DOT-USD",
                "net_pnl": -0.12,
                "gross_pnl": -0.08,
                "fees": 0.04,
                "return_pct": -0.3,
                "entry_time": closed_at - timedelta(minutes=6),
                "exit_time": closed_at,
                "model": None,
                "model_version": None,
            },
            {
                "strategy": "other_strategy",
                "symbol": "BTC-USD",
                "net_pnl": 1.0,
                "gross_pnl": 1.1,
                "fees": 0.1,
                "return_pct": 1.0,
                "entry_time": closed_at - timedelta(minutes=30),
                "exit_time": closed_at,
                "model": None,
                "model_version": None,
            },
        ],
    )
    monkeypatch.setitem(sys.modules, "database", fake_database)

    records = econ._ledger_records("oracle_council_v3")

    assert len(records) == 1
    assert records[0]["symbol"] == "DOT-USD"
    assert records[0]["net_pnl"] == -0.12


def test_strategy_economics_accumulates_normalized_post_fix_closes(monkeypatch):
    _paper(monkeypatch)
    econ._CACHE.clear()
    records = [
        {
            "strategy": "dynamic",
            "symbol": "DOT-USD",
            "net_pnl": -0.10,
            "gross_pnl": -0.06,
            "fees": 0.04,
            "entry_time": datetime.now(timezone.utc) - timedelta(minutes=6),
            "exit_time": datetime.now(timezone.utc),
        },
        {
            "strategy": "dynamic",
            "symbol": "NEAR-USD",
            "net_pnl": 0.20,
            "gross_pnl": 0.24,
            "fees": 0.04,
            "entry_time": datetime.now(timezone.utc) - timedelta(minutes=7),
            "exit_time": datetime.now(timezone.utc),
        },
    ]
    monkeypatch.setattr(econ, "_ledger_records", lambda strategy: records if strategy == "oracle_council_v3" else [])
    monkeypatch.setattr(econ, "model_validation_ok", lambda signal: False)

    result = econ.strategy_economics(
        {"strategy": "20d momentum +22.0%; RSI 68.0; Oracle Council V3 consensus BUY"}
    )

    assert result.strategy == "oracle_council_v3"
    assert result.sample_count == 2
    assert round(result.net_pnl, 6) == 0.10
    assert round(result.fees, 6) == 0.08
    assert result.average_holding_minutes > 0
