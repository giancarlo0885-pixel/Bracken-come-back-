from __future__ import annotations

from datetime import datetime, timedelta
import json
import os
from contextlib import contextmanager
import uuid

import pandas as pd
import pytest

import paper_dip_rebound as experiment
from dip_rebound_backtest import bar_exit, replay
from test_entry_patterns import observed_pattern, rebound_history
from test_paper_regime_fifo_provenance import paper_provenance_db


def quote(symbol="BTC-USD", price=101.3, now=None):
    now = now or datetime.fromisoformat(observed_pattern()["bar_end"])
    return {"symbol": symbol, "requested_symbol": symbol, "provider_symbol": symbol,
            "provider": "Robinhood Crypto", "source_capability": "best_bid_ask_realtime",
            "quote_verified": True, "source_interval": "5m", "price": price,
            "bid": price - .01, "ask": price + .01, "quote_timestamp": now.isoformat()}


def plan():
    pattern = observed_pattern()
    now = datetime.fromisoformat(pattern["bar_end"])
    state, reason = experiment.plan_entry(pattern, quote(now=now), now)
    assert reason == "qualified_after_costs"
    return state, now


@pytest.mark.parametrize("flag,value", [("EXECUTION_MODE", "live"), ("LIVE_TRADING_ARMED", "true"),
                                       ("ENABLE_BROKER_SUBMISSION", "true"), ("PAPER_DIP_REBOUND_EXPERIMENT", "false")])
def test_experiment_cannot_run_with_live_controls_or_disabled_flag(monkeypatch, flag, value):
    monkeypatch.setenv("EXECUTION_MODE", "paper")
    monkeypatch.setenv("PAPER_AUTONOMOUS_LEARNING", "true")
    monkeypatch.setenv("ENABLE_BROKER_SUBMISSION", "false")
    monkeypatch.setenv("LIVE_TRADING_ARMED", "false")
    monkeypatch.setenv(flag, value)
    assert experiment.observe([], {}) == {"observed": 0, "opened": 0, "closed": 0}
    assert experiment.open_symbols() == []


@pytest.mark.parametrize("change", [{"quote_verified": False}, {"provider_symbol": "ETH-USD"},
                                    {"provider": "Yahoo Finance"}, {"source_capability": "history_daily"},
                                    {"bid": 102}, {"ask": float("nan")}, {"stale": True},
                                    {"quote_timestamp": "2020-01-01T00:00:00+00:00"}])
def test_only_fresh_verified_identity_matched_broker_quotes_can_fill(change):
    _, now = plan()
    assert not experiment.verified_quote("BTC-USD", dict(quote(now=now), **change), now)


def test_valid_quote_and_costs_and_stale_setup_checks():
    state, now = plan()
    assert experiment.verified_quote("BTC-USD", quote(now=now), now)
    assert state["entry"] > quote()["ask"]
    assert state["entry_fill"]["fee_pct"] > 0
    expensive = dict(quote(now=now), bid=100.3, ask=102.3)
    assert experiment.plan_entry(observed_pattern(), expensive, now)[0] is None
    assert experiment.plan_entry(observed_pattern(), quote(now=now+timedelta(hours=1)), now+timedelta(hours=1))[0] is None
    assert experiment.plan_entry(observed_pattern(), quote(now=now-timedelta(seconds=1)), now)[0] is None


def test_target_trailing_stop_and_deadline_have_explicit_paths():
    state, now = plan()
    reason, _ = experiment.exit_decision(state, state["target"] + .1, now, experiment.TARGET_POLICY)
    assert reason == "rebound_target"
    assert experiment.exit_decision(state, state["target"] + .1, now, experiment.CONTROL_POLICY)[0] is None
    assert experiment.exit_decision(state, state["stop"] - 1, now, experiment.TARGET_POLICY)[0] == "invalidation_stop"
    peaked = dict(state, peak=state["entry"] + 1.2 * state["risk"])
    assert experiment.exit_decision(peaked, peaked["peak"] - state["risk"] - .01, now, experiment.TARGET_POLICY)[0] == "profit_trail"
    assert experiment.exit_decision(state, state["entry"], datetime.fromisoformat(state["deadline"]), experiment.CONTROL_POLICY)[0] == "time_exit"


def test_ambiguous_backtest_candle_takes_stop_and_gap_gets_worse_fill():
    state, now = plan()
    bar = pd.Series({"Open": state["entry"], "High": state["target"]+1, "Low": state["stop"]-1, "Close": state["target"]})
    reason, price, _ = bar_exit(state, bar, now, experiment.TARGET_POLICY)
    assert reason == "invalidation_stop" and price == state["stop"]
    bar.Open = state["stop"] - 2
    assert bar_exit(state, bar, now, experiment.TARGET_POLICY)[1] == bar.Open


def test_replay_enters_next_bar_and_does_not_count_unresolved_control_as_a_win():
    frame = rebound_history()
    next_time = frame.index[-1] + pd.Timedelta(minutes=5)
    future = pd.DataFrame({"Open": [101.3], "High": [104.0], "Low": [101.2], "Close": [103.8], "Volume": [1000]}, index=[next_time])
    history = pd.concat([frame, future]); history.attrs = frame.attrs.copy()
    result = replay(history, spread_bps=2)
    [trade] = result["trades"]
    assert trade["entry_at"] == next_time.isoformat()
    assert trade["exit_reason"] == "rebound_target"
    assert trade["net_return_pct"] == pytest.approx((trade["exit_fill"]/trade["entry_fill"]-1)*100)
    assert result["unresolved_trials"] == 1
    assert result["paired_closes"] == 0


def test_postgres_dip_experiment_persists_idempotent_paired_lifecycle(paper_provenance_db, monkeypatch):
    conn = paper_provenance_db
    monkeypatch.setenv("PAPER_DIP_REBOUND_EXPERIMENT", "true")
    experiment.ensure_schema()
    symbol = f"DIP-{uuid.uuid4().hex[:8].upper()}-USD"
    pattern = observed_pattern(); now = datetime.fromisoformat(pattern["bar_end"])
    signal = {"symbol": symbol, "entry_pattern": pattern}
    canonical_before = conn.execute("SELECT COUNT(*) AS n FROM trades").fetchone()["n"]
    assert experiment.observe([signal], {symbol: quote(symbol, now=now)}, now=now) == {"observed": 1, "opened": 2, "closed": 0}
    assert experiment.observe([signal], {symbol: quote(symbol, now=now)}, now=now)["opened"] == 0
    assert symbol in experiment.open_symbols()
    trials = conn.execute("SELECT * FROM paper_dip_rebound_trials WHERE symbol=%s ORDER BY policy", (symbol,)).fetchall()
    assert trials[0]["state"]["entry"] == trials[1]["state"]["entry"]
    later = now + timedelta(minutes=1)
    assert experiment.observe([], {symbol: quote(symbol, 104, later)}, now=later)["closed"] == 1
    # Duplicate and older quote timestamps cannot move persistent peak or close again.
    assert experiment.observe([], {symbol: quote(symbol, 90, now)}, now=later)["closed"] == 0
    deadline = datetime.fromisoformat(trials[0]["state"]["deadline"]) + timedelta(seconds=1)
    assert experiment.observe([], {symbol: quote(symbol, 102, deadline)}, now=deadline)["closed"] == 1
    assert symbol not in experiment.open_symbols()
    closed = conn.execute("SELECT * FROM paper_dip_rebound_trials WHERE symbol=%s", (symbol,)).fetchall()
    assert {row["exit_reason"] for row in closed} == {"rebound_target", "time_exit"}
    for row in closed:
        assert row["state"]["pattern"] == pattern
        assert row["net_return_pct"] == pytest.approx((row["exit_fill"]["fill_price"]/row["state"]["entry"]-1)*100)
    assert conn.execute("SELECT COUNT(*) AS n FROM trades").fetchone()["n"] == canonical_before


def test_postgres_pattern_provenance_survives_compact_signal_and_fifo_close(paper_provenance_db):
    import database
    import market_worker
    import oracle_bot
    from entry_patterns import pattern_memory_features

    conn = paper_provenance_db
    symbol = f"PATTERN-{uuid.uuid4().hex[:8].upper()}-USD"
    pattern = observed_pattern(); now = pattern["bar_end"]
    original = {"symbol": symbol, "price": 101.3, "action": "BUY", "entry_pattern": pattern}
    signal_id = database.save_json_signal("crypto", symbol, 101.3, .6, "BUY", .6, original, created_at=now)
    signal = {"symbol": symbol, "signal_id": signal_id, "feature_snapshot": {"alpha": .6}, "strategy": "dip_test"}
    oracle_bot._record_buy_attribution(conn, market="crypto", symbol=symbol, quantity=1, price=101.3, fees=.1,
                                      signal=signal, quote_metadata=quote(symbol), now=now)
    database.save_json_signal("crypto", symbol, 90, .1, "SELL", .1,
                              dict(original, entry_pattern=dict(pattern, rsi=5, context_regime=-1)), created_at=now)
    [row] = oracle_bot._record_sell_attribution(conn, market="crypto", position={"symbol": symbol, "quantity": 1, "average_price": 101.3},
                                               price=103, quantity=1, fees=.1, reason="test_exit", quote_metadata=quote(symbol,103), now=now)
    expected = pattern_memory_features(original)
    assert {key: row["feature_snapshot"][key] for key in expected} == expected
    assert row["entry_signal_id"] == str(signal_id)
    from market_memory import record_closed_trade_memory
    assert record_closed_trade_memory(market="crypto", symbol=symbol,
        position={"symbol": symbol, "average_price": 101.3, "opened_at": now},
        exit_price=103, pnl=row["net_pnl"], exit_reason="test_exit", quantity=1, entry_provenance=row)
    payload = conn.execute("SELECT payload FROM trade_dna WHERE symbol=%s ORDER BY id DESC LIMIT 1", (symbol,)).fetchone()["payload"]
    payload = json.loads(payload) if isinstance(payload, str) else payload
    assert {key: payload["features"][key] for key in expected} == expected


def test_postgres_experiment_advisory_lock_excludes_another_runner(paper_provenance_db, monkeypatch):
    import database
    import psycopg
    from psycopg.rows import dict_row
    conn = paper_provenance_db
    experiment.ensure_schema()
    conn.execute("SELECT pg_advisory_xact_lock(hashtext('paper_dip_rebound_cycle_v1'))")
    with psycopg.connect(os.environ["DATABASE_URL"], row_factory=dict_row) as other:
        @contextmanager
        def connect_other():
            with other.transaction(force_rollback=True):
                yield other
        monkeypatch.setattr(database, "connect", connect_other)
        assert experiment.observe([], {}) == {"observed": 0, "opened": 0, "closed": 0}


def test_postgres_retention_keeps_open_trials(paper_provenance_db, monkeypatch):
    conn = paper_provenance_db
    monkeypatch.setattr(experiment, "_LAST_SUMMARY", 0.0)
    experiment.ensure_schema()
    symbol = f"KEEP-{uuid.uuid4().hex[:8].upper()}-USD"
    now = datetime.fromisoformat(observed_pattern()["bar_end"])
    experiment.observe([{"symbol": symbol, "entry_pattern": observed_pattern()}], {symbol: quote(symbol,now=now)}, now=now)
    conn.execute("UPDATE paper_dip_rebound_trials SET entry_at='2001-01-01' WHERE symbol=%s", (symbol,))
    conn.execute("UPDATE paper_dip_rebound_trials SET exit_at='2001-01-02' WHERE symbol=%s AND policy=%s", (symbol, experiment.TARGET_POLICY))
    experiment.summarize_and_retain()
    remaining = conn.execute("SELECT policy FROM paper_dip_rebound_trials WHERE symbol=%s", (symbol,)).fetchall()
    assert [row["policy"] for row in remaining] == [experiment.CONTROL_POLICY]
