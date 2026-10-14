"""Read-only production-backed provenance audit; no execution or backfill.

Historical paper fills are counted separately from those carrying an exact
canonical trade ID. A reconstructed decision replay cannot prove that a Brain
episode was consumed at decision time.
"""
from __future__ import annotations

from typing import Any


def summarize_persisted_lineage(conn: Any, *, market: str = "crypto") -> dict:
    if market not in ("crypto", "cash"):
        raise ValueError("invalid market")
    conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
    conn.execute("SET LOCAL statement_timeout = '5000ms'")
    state = conn.execute("SELECT current_setting('transaction_read_only') AS ro").fetchone()
    if state["ro"] != "on":
        raise RuntimeError("audit requires read-only transaction")
    fills = conn.execute(
        """SELECT COUNT(*)::bigint AS total,
                  COUNT(*) FILTER (WHERE canonical_trade_id IS NOT NULL)::bigint AS linked,
                  COUNT(*) FILTER (WHERE canonical_trade_id IS NULL)::bigint AS isolated,
                  COUNT(*) FILTER (WHERE fee_amount IS NULL OR fee_amount < 0)::bigint AS invalid_fee
           FROM paper_fills WHERE market=%s""", (market,)
    ).fetchone()
    # These replay snapshots are created retrospectively from historical
    # observations. They are not immutable decision-time consumption receipts.
    replays = conn.execute(
        """SELECT COUNT(*)::bigint AS total FROM oracle_decision_replays
           WHERE market=%s AND execution_impact='NONE'""", (market,)
    ).fetchone()
    exact = conn.execute(
        """SELECT COUNT(*)::bigint AS total FROM oracle_brain_episodes
           WHERE market=%s AND provenance_status='exact'
             AND outcome_snapshot->>'accounting_version'='exact_round_trip_v1'
             AND outcome_snapshot->>'cost_provenance'='exact_lot'""", (market,)
    ).fetchone()
    return {
        "market": market,
        "paper_fills": dict(fills),
        "exact_brain_episodes": int(exact["total"]),
        "retrospective_decision_replays": int(replays["total"]),
        "verified_decision_consumption": "UNAVAILABLE",
        "reason": "No immutable decision-time consumed-episode receipt is persisted",
        "historical_repair": "NONE",
        "execution_impact": "NONE",
    }


def audit_persisted_lineage(*, market: str = "crypto", connect_factory=None) -> dict:
    if connect_factory is None:
        from database import connect
        connect_factory = connect
    with connect_factory() as conn:
        return summarize_persisted_lineage(conn, market=market)
