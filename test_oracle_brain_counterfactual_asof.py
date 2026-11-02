"""Fail-first: Brain counterfactual feedback must be decision-time causal.

These are synthetic research records, not executed fills or profitability evidence.
"""
import sqlite3

import oracle_brain_feedback as feedback


AS_OF = "2026-10-01T12:00:00+00:00"


def test_counterfactual_cannot_consume_outcomes_evaluated_after_signal(monkeypatch):
    import database

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript("""
        CREATE TABLE oracle_decision_audit(id INTEGER PRIMARY KEY, payload TEXT);
        CREATE TABLE oracle_counterfactual_outcomes(
            decision_id INTEGER, market TEXT, symbol TEXT,
            decision_time TEXT, evaluated_at TEXT,
            outcome_class TEXT, return_pct REAL,
            entry_price REAL, horizon_price REAL
        );
    """)
    # All original decisions predate the target signal. Only the first cohort's
    # horizons were evaluated before the signal; the others are future labels.
    for idx in range(90):
        before = idx < 30
        conn.execute("INSERT INTO oracle_decision_audit VALUES (?, ?)", (idx, "{}"))
        conn.execute(
            "INSERT INTO oracle_counterfactual_outcomes VALUES (?,?,?,?,?,?,?,?,?)",
            (idx, "crypto", "TEST-USD",
             "2026-09-30T09:00:00+00:00" if idx < 60 else "2026-10-02T09:00:00+00:00",
             "2026-09-30T11:00:00+00:00" if before else "2026-10-02T11:00:00+00:00",
             "avoided_loss" if before else "missed_winner",
             -10.0 if before else 10.0, 100.0, 90.0 if before else 110.0),
        )

    def fetch_rows(sql, params):
        return [dict(row) for row in conn.execute(sql.replace("%s", "?"), params)]

    monkeypatch.setattr(database, "rows", fetch_rows)
    try:
        for max_rows in (0, 500):
            result = feedback._counterfactual_memory_for_signal(
                {"decision_timestamp": AS_OF}, market="crypto",
                symbol="TEST-USD", min_samples=10, max_rows=max_rows,
            )
            assert result["status"] == "ok"
            assert result["samples"] == 30
            assert result["avoided_losses"] == 30
            assert result["simulated_post_cost_profitable_missed"] == 0
            assert result["adjustment"] < 0
    finally:
        conn.close()


def test_invalid_explicit_decision_time_cannot_read_future_feedback(monkeypatch):
    import database

    def unexpected_query(*args, **kwargs):
        raise AssertionError("Invalid decision-time provenance must not query Brain outcomes")

    monkeypatch.setattr(database, "rows", unexpected_query)
    result = feedback._counterfactual_memory_for_signal(
        {"decision_timestamp": "invalid-date"}, market="crypto",
        symbol="TEST-USD", min_samples=10,
    )
    assert result["status"] == "invalid_decision_time"
    assert result["adjustment"] == 0.0
    assert result["execution_impact"] == "NONE"
