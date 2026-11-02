"""Fail-first: episode ranking cannot consume outcomes after signal decision time.

Synthetic exact-cost episodes are not trades or profitability evidence.
"""
import json
import sqlite3

import oracle_brain
import oracle_brain_feedback as feedback
import database


SIGNAL = {
    "symbol": "TEST-USD",
    "strategy": "Oracle Council V3",
    "regime": "risk_on",
    "decision_timestamp": "2026-10-01T12:00:00+00:00",
}


def test_episode_ranking_uses_only_exits_known_at_signal_time(monkeypatch):
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("""CREATE TABLE oracle_brain_episodes(
        market TEXT, symbol TEXT, strategy TEXT, regime TEXT,
        net_pnl REAL, return_pct REAL, confidence REAL, freshness_score REAL,
        exit_time TEXT, feature_snapshot TEXT, outcome_snapshot TEXT,
        provenance_status TEXT
    )""")
    exact = json.dumps({
        "accounting_version": "exact_round_trip_v1",
        "cost_provenance": "exact_lot",
    })
    for index in range(60):
        known = index < 30
        conn.execute(
            "INSERT INTO oracle_brain_episodes VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            ("crypto", "TEST-USD", "Oracle Council V3", "risk_on",
             -1.0 if known else 1.0, -0.5 if known else 0.5,
             1.0, 1.0,
             "2026-09-30T11:00:00+00:00" if known
             else "2026-10-02T11:00:00+00:00",
             "{}", exact, "exact"),
        )

    def fetch_rows(sql, params=()):
        if "FROM oracle_brain_episodes" not in sql:
            return []
        return [dict(row) for row in conn.execute(sql.replace("%s", "?"), params)]

    monkeypatch.setattr(database, "rows", fetch_rows)
    monkeypatch.setattr(oracle_brain, "runtime_safety_state",
                        lambda: {"safe_research_boundary": True})
    monkeypatch.setattr(feedback, "_counterfactual_memory_for_signal",
                        lambda *args, **kwargs: {"adjustment": 0.0})
    try:
        for limit in (0, 500):
            result = feedback.outcome_memory_for_signal(
                SIGNAL, market="crypto", min_samples=10, max_rows=limit,
            )
            assert result["status"] == "ok"
            assert result["samples"] == 30
            assert result["polarity"] == "negative"
            assert result["net_pnl"] == -30.0
            assert result["base_ranking_adjustment"] < 0
        assert conn.execute("SELECT COUNT(*) FROM oracle_brain_episodes").fetchone()[0] == 60
    finally:
        conn.close()


def test_invalid_explicit_decision_time_cannot_query_episode_memory(monkeypatch):
    calls = []

    def fetch_rows(sql, params=()):
        if "FROM oracle_brain_episodes" in sql:
            calls.append((sql, params))
        return []

    monkeypatch.setattr(database, "rows", fetch_rows)
    monkeypatch.setattr(oracle_brain, "runtime_safety_state",
                        lambda: {"safe_research_boundary": True})
    result = feedback.outcome_memory_for_signal(
        {**SIGNAL, "decision_timestamp": "not-a-timestamp"},
        market="crypto", min_samples=10,
    )
    assert result["status"] == "invalid_decision_time"
    assert result["ranking_adjustment"] == 0.0
    assert result["execution_impact"] == "NONE"
    assert calls == []
