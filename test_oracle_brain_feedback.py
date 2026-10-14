import json
import sqlite3

from oracle_brain_feedback import outcome_memory_for_signal, summarize_outcome_memory
from oracle_brain_learning import _normalized_strategy_identity


def _rows(count: int, *, return_pct: float, net_pnl: float, strategy: str = "Oracle Council V3 dynamic rationale"):
    return [
        {
            "symbol": "AAVE-USD",
            "strategy": strategy,
            "regime": "risk_on",
            "return_pct": return_pct,
            "net_pnl": net_pnl,
            "confidence": 0.95,
            "freshness_score": 0.95,
        }
        for _ in range(count)
    ]


def test_brain_strategy_identity_matches_economics_identity():
    assert _normalized_strategy_identity("Oracle Council V3 | momentum=0.31 | rsi=54") == "oracle_council_v3"
    assert _normalized_strategy_identity("Always-on 5m market pulse. Momentum is changing.") == "always_on_5m_market_pulse"


def test_mature_positive_exact_outcomes_support_ranking():
    result = summarize_outcome_memory(
        _rows(30, return_pct=0.40, net_pnl=1.0),
        strategy="Oracle Council V3 | momentum=0.99",
        regime="risk_on",
        symbol="AAVE-USD",
        min_samples=30,
    )
    assert result["mature"] is True
    assert result["polarity"] == "positive"
    assert 0 < result["ranking_adjustment"] <= 3.0
    assert result["execution_impact"] == "BOUNDED_RANKING_ONLY"


def test_mature_negative_exact_outcomes_penalize_ranking():
    result = summarize_outcome_memory(
        _rows(30, return_pct=-0.40, net_pnl=-1.0),
        strategy="Oracle Council V3 | momentum=0.99",
        regime="risk_on",
        symbol="AAVE-USD",
        min_samples=30,
    )
    assert result["mature"] is True
    assert result["polarity"] == "negative"
    assert -4.0 <= result["ranking_adjustment"] < 0


def test_immature_outcomes_have_zero_ranking_influence():
    result = summarize_outcome_memory(
        _rows(12, return_pct=2.0, net_pnl=5.0),
        strategy="Oracle Council V3",
        regime="risk_on",
        symbol="AAVE-USD",
        min_samples=30,
    )
    assert result["mature"] is False
    assert result["polarity"] == "insufficient"
    assert result["ranking_adjustment"] == 0.0
    assert result["execution_impact"] == "NONE"


def test_runtime_ranking_excludes_legacy_winners_and_inexact_costs_in_both_query_paths(monkeypatch):
    """Execute the actual ranking SELECTs over retained and reconciled outcomes."""
    import database
    import oracle_brain

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("""CREATE TABLE oracle_brain_episodes(
        market TEXT,symbol TEXT,strategy TEXT,regime TEXT,net_pnl REAL,return_pct REAL,
        confidence REAL,freshness_score REAL,exit_time TEXT,feature_snapshot TEXT,
        outcome_snapshot TEXT,provenance_status TEXT)""")
    sources = [
        # Before round-trip reconciliation, these retained exact-entry records
        # were winners only because their entry fees had been omitted.
        (_rows(30, return_pct=2.0, net_pnl=5.0), {}),
        (_rows(30, return_pct=2.0, net_pnl=5.0), {
            "accounting_version": "exact_round_trip_v1", "cost_provenance": "legacy_fallback",
        }),
        (_rows(30, return_pct=-0.15, net_pnl=-0.15), {
            "accounting_version": "exact_round_trip_v1", "cost_provenance": "exact_lot",
        }),
    ]
    def insert_outcomes(records, accounting):
        conn.executemany("INSERT INTO oracle_brain_episodes VALUES (?,?,?,?,?,?,?,?,?,?,?,?)", [
            ("crypto", row["symbol"], row["strategy"], row["regime"], row["net_pnl"],
             row["return_pct"], row["confidence"], row["freshness_score"],
             "2026-10-01T00:00:00+00:00", "{}", json.dumps(accounting), "exact")
            for row in records
        ])

    for records, accounting in sources[:2]:
        insert_outcomes(records, accounting)

    def fetch_rows(sql, params=()):
        if "FROM oracle_brain_episodes" not in sql:
            return []
        # SQLite supports the same JSON text extraction used by these SELECTs;
        # translate only DB-API placeholder syntax, preserving the predicates.
        return [dict(row) for row in conn.execute(sql.replace("%s", "?"), params)]

    monkeypatch.setattr(database, "rows", fetch_rows)
    monkeypatch.setattr(oracle_brain, "runtime_safety_state", lambda: {"safe_research_boundary": True})
    try:
        for limit in (0, 500):
            insufficient = outcome_memory_for_signal(
                {"symbol": "AAVE-USD", "regime": "risk_on", "strategy": "Oracle Council V3"},
                market="crypto", min_samples=30, max_rows=limit,
            )
            assert insufficient["samples"] == 0
            assert insufficient["mature"] is False
            assert insufficient["ranking_adjustment"] == 0.0
            assert insufficient["execution_impact"] == "NONE"
        insert_outcomes(*sources[2])
        for limit in (0, 500):
            result = outcome_memory_for_signal(
                {"symbol": "AAVE-USD", "regime": "risk_on", "strategy": "Oracle Council V3"},
                market="crypto", min_samples=30, max_rows=limit,
            )
            assert result["status"] == "ok"
            assert result["samples"] == 30
            assert result["mature"] is True
            assert result["polarity"] == "negative"
            assert result["net_pnl"] == -4.5
            assert result["base_ranking_adjustment"] < 0
            assert result["ranking_adjustment"] < 0
        # Exclusion is a read-time eligibility boundary, not deletion of memory.
        assert conn.execute("SELECT COUNT(*) FROM oracle_brain_episodes").fetchone()[0] == 90
    finally:
        conn.close()
