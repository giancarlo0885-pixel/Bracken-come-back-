"""Bounded, read-only committed-trade receipts for internal worker logs.

An evaluator log is not a commit receipt. This module opens its own PostgreSQL
snapshot, after the learning transaction, and never creates or repairs evidence.
Brain materialization proves durable ingestion, not use by a later decision.
"""
from __future__ import annotations

import json
import logging
import math
import os
from datetime import datetime, timezone
from typing import Any

log = logging.getLogger(__name__)
MAX_TRADE_IDS = 12


def _ids(values: Any) -> list[str]:
    if isinstance(values, (str, bytes)):
        raise ValueError("trade_ids must be a sequence")
    supplied = list(values)
    if any(
        not isinstance(value, str) or not value.strip() or len(value) > 128
        or any(ord(char) < 32 for char in value) for value in supplied
    ):
        raise ValueError("invalid bounded trade IDs")
    result = list(dict.fromkeys(value.strip() for value in supplied))
    if len(result) > MAX_TRADE_IDS:
        raise ValueError("invalid bounded trade IDs")
    return result


def _time(value: Any) -> datetime | None:
    try:
        parsed = value if isinstance(value, datetime) else datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        # Naive timestamps cannot independently establish a UTC chronology.
        return parsed.astimezone(timezone.utc) if parsed.tzinfo else None
    except (TypeError, ValueError, OverflowError):
        return None


def _number(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        result = float(value)
        return result if math.isfinite(result) else None
    except (TypeError, ValueError, OverflowError):
        return None


def _object(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    try:
        result = json.loads(value)
        return result if isinstance(result, dict) else {}
    except (TypeError, ValueError):
        return {}


def _same_features(left: Any, right: Any) -> bool:
    """JSON numeric equality without Python's True == 1 false match."""
    if isinstance(left, dict) and isinstance(right, dict):
        return left.keys() == right.keys() and all(_same_features(left[key], right[key]) for key in left)
    if isinstance(left, list) and isinstance(right, list):
        return len(left) == len(right) and all(_same_features(a, b) for a, b in zip(left, right))
    if isinstance(left, bool) or isinstance(right, bool):
        return type(left) is type(right) and left == right
    if isinstance(left, (int, float)) and isinstance(right, (int, float)):
        return _number(left) is not None and _number(right) is not None and left == right
    return type(left) is type(right) and left == right


def _receipt(trade_id: str, market: str, rows: dict[str, list[dict]], available: dict[str, bool], checked_at: Any) -> dict:
    by_id = {key: [row for row in values if row["trade_id"] == trade_id] for key, values in rows.items()}
    ledger, metrics, brain, aeve = (by_id[key] for key in ("ledger", "metrics", "brain", "aeve"))
    result = {
        "trade_id": trade_id,
        "ledger_status": "COMMITTED" if len(ledger) == 1 else "MISSING" if not ledger else "AMBIGUOUS",
        "ledger_count": len(ledger),
        "brain_status": "PENDING" if available["brain"] else "UNAVAILABLE",
        "brain_count": len(brain),
        "aeve_status": "COMMITTED" if aeve else "MISSING" if available["aeve"] else "UNAVAILABLE",
        "aeve_committed_count": len(aeve),
        "aeve_generations": sorted({row["generation"] for row in aeve}),
        "ranking_decision_consumption": "UNAVAILABLE",
        "canonical_fill_linkage": "UNAVAILABLE",
        "execution_impact": "NONE",
    }
    if len(ledger) != 1:
        if brain:
            result["brain_status"] = "ORPHANED" if not ledger else "AMBIGUOUS"
        return result
    canonical = ledger[0]
    result.update(symbol=canonical["symbol"], market=canonical["market"], side=canonical["side"])
    if canonical["market"] != market:
        result.update(ledger_status="SCOPE_MISMATCH", brain_status="MISMATCH")
        return result
    if canonical["broker_mode"] != "PAPER" or canonical["account_environment"] != "PAPER":
        result["brain_status"] = "NON_PAPER"
        return result
    if canonical["side"] != "SELL" or not canonical["exit_time"]:
        result["brain_status"] = "NOT_APPLICABLE"
        return result
    if len(brain) > 1 or len(metrics) > 1:
        result["brain_status"] = "AMBIGUOUS"
        return result
    if not brain:
        return result
    if not available["metrics"]:
        result["brain_status"] = "UNAVAILABLE"
        return result
    if not metrics:
        result["brain_status"] = "UNQUALIFIED"
        return result
    outcome, metric = brain[0], metrics[0]
    snapshot = _object(outcome["outcome_snapshot"])
    entry, exit_ = _time(canonical["entry_time"]), _time(canonical["exit_time"])
    now = _time(checked_at)
    net, fees = _number(metric["round_trip_net_pnl"]), _number(metric["round_trip_fees"])
    if (canonical["side"] != "SELL" or not entry or not exit_ or not now
            or not entry <= exit_ <= now or not canonical["entry_signal_id"]
            or not _object(canonical["feature_snapshot"])
            or metric["cost_provenance"] != "exact_lot" or net is None or fees is None or fees < 0
            or outcome["provenance_status"] != "exact"
            or snapshot.get("accounting_version") != "exact_round_trip_v1"
            or snapshot.get("cost_provenance") != "exact_lot"
            or outcome["execution_impact"] != "NONE"):
        result["brain_status"] = "UNQUALIFIED"
        return result
    if (outcome["episode_key"] != f"trade:{trade_id}"
            or any(row["market"] != market or row["symbol"] != canonical["symbol"] for row in (metric, outcome))
            or snapshot.get("entry_signal_id") != canonical["entry_signal_id"]
            or snapshot.get("outcome") != ("positive" if net > 0 else "negative" if net < 0 else "flat")
            or not _same_features(_object(outcome["feature_snapshot"]), _object(canonical["feature_snapshot"]))
            or any(_time(row["entry_time"]) != entry or _time(row["exit_time"]) != exit_ for row in (metric, outcome))
            or any(_number(row.get("net_pnl")) != net or _number(row.get("fees")) != fees for row in (outcome, snapshot))):
        result["brain_status"] = "MISMATCH"
        return result
    result.update(brain_status="MATERIALIZED_EXACT", round_trip_net_pnl=net, round_trip_fees=fees,
                  entry_signal_id=canonical["entry_signal_id"], episode_key=outcome["episode_key"])
    return result


def verify_committed_trade_evidence(trade_ids=None, market: str = "crypto", *, connect_factory=None) -> dict:
    """Read exact IDs, or at most twelve recent closed/evaluated trades.

    Optional absent learning tables are UNAVAILABLE, never proof of absent rows.
    SQL failures propagate so callers cannot accidentally report MISSING.
    """
    if market not in {"crypto", "cash"}:
        raise ValueError("invalid market")
    requested = None if trade_ids is None else _ids(trade_ids)
    if connect_factory is None:
        from database import connect
        connect_factory = connect
    with connect_factory() as conn:
        conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
        conn.execute("SET LOCAL statement_timeout = '5000ms'")
        snapshot = dict(conn.execute("""SELECT CURRENT_TIMESTAMP AS checked_at,
            pg_current_snapshot()::text AS snapshot,
            current_setting('transaction_read_only') AS read_only""").fetchone())
        if snapshot["read_only"] != "on":
            raise RuntimeError("verification transaction is not read only")
        tables = {"metrics": "paper_regime_trade_metrics", "brain": "oracle_brain_episodes", "aeve": "paper_aeve_generation_outcomes",
                  "fill_links": "paper_fill_lot_links"}
        available = {key: bool(conn.execute("SELECT to_regclass(%s) AS relation", (table,)).fetchone()["relation"])
                     for key, table in tables.items()}
        if requested is None:
            recent = conn.execute("""SELECT trade_id FROM trade_ledger
                WHERE market=%s AND side='SELL' AND broker_mode='PAPER' AND account_environment='PAPER'
                ORDER BY id DESC LIMIT 2""", (market,)).fetchall()
            # AEVE currently evaluates crypto only; its rows lack a market field.
            # Include orphan IDs so a missing canonical row stays detectable.
            evaluated = conn.execute("""SELECT trade_id FROM paper_aeve_generation_outcomes
                GROUP BY trade_id ORDER BY MAX(observed_at) DESC,trade_id LIMIT 10""").fetchall() if available["aeve"] and market == "crypto" else []
            requested = _ids([row["trade_id"] for row in recent + evaluated])
        rows = {"ledger": list(conn.execute("""SELECT * FROM trade_ledger
            WHERE trade_id=ANY(%s)""", (requested,)).fetchall()), "metrics": [], "brain": [], "aeve": []}
        if available["metrics"]:
            rows["metrics"] = list(conn.execute("""SELECT trade_id,market,symbol,entry_time,exit_time,
                round_trip_net_pnl,round_trip_fees,cost_provenance FROM paper_regime_trade_metrics
                WHERE trade_id=ANY(%s)""", (requested,)).fetchall())
        if available["brain"]:
            rows["brain"] = list(conn.execute("""SELECT trade_id,episode_key,market,symbol,entry_time,exit_time,
                net_pnl,fees,provenance_status,feature_snapshot,outcome_snapshot,execution_impact FROM oracle_brain_episodes
                WHERE trade_id=ANY(%s)""", (requested,)).fetchall())
        if available["aeve"]:
            rows["aeve"] = list(conn.execute("""SELECT trade_id,generation FROM paper_aeve_generation_outcomes
                WHERE trade_id=ANY(%s)""", (requested,)).fetchall())
        records = [_receipt(trade_id, market, rows, available, snapshot["checked_at"]) for trade_id in requested]
        if available["fill_links"]:
            from paper_fill_provenance import attach_exact_fill_lot_evidence, load_fill_lot_allocations, close

            allocations = load_fill_lot_allocations(conn, trade_ids=requested, market=market, as_of=snapshot["checked_at"])
            linked = attach_exact_fill_lot_evidence(rows["ledger"], allocations, as_of=snapshot["checked_at"])
            by_id = {row["trade_id"]: row for row in linked}
            for receipt in records:
                row = by_id.get(receipt["trade_id"], {})
                if (receipt["ledger_status"] != "COMMITTED" or row.get("side") != "SELL"
                        or row.get("_fill_accounting_provenance") != "exact_fill_lot_v1"):
                    continue
                entry_fee = float(row["_canonical_entry_fill_fee"]) * float(row["quantity"]) / float(row["_canonical_entry_quantity"])
                fees = entry_fee + float(row["_canonical_fill_fee"])
                net = (float(row["exit_price"]) - float(row["entry_price"])) * float(row["quantity"]) - fees
                receipt.update(canonical_fill_linkage="EXACT_LINKED", canonical_lot_id=row["_canonical_lot_id"],
                               entry_fill_id=row["_canonical_entry_fill_id"], exit_fill_id=row["_canonical_fill_id"],
                               entry_ledger_trade_id=row["_canonical_entry_trade_id"],
                               fill_accounting_provenance=row["_fill_accounting_provenance"],
                               linked_round_trip_fees=fees, linked_round_trip_net_pnl=net,
                               brain_fill_accounting_status="UNAVAILABLE")
                if receipt["brain_status"] == "MATERIALIZED_EXACT":
                    receipt["brain_fill_accounting_status"] = "MATCH" if (
                        close(fees, receipt["round_trip_fees"]) and close(net, receipt["round_trip_net_pnl"])) else "MISMATCH"
    # No receipt is returned or logged until the independent transaction exits.
    return {"status": "ok", "market": market, "checked_at": snapshot["checked_at"].isoformat(),
            "snapshot": snapshot["snapshot"], "transaction_read_only": True,
            "records": records, "execution_impact": "NONE"}


def emit_committed_trade_verification(market: str) -> dict:
    """Worker diagnostic only: errors cannot affect execution or learning sync."""
    try:
        configured = os.getenv("ORACLE_VERIFY_TRADE_IDS", "").strip()
        result = verify_committed_trade_evidence(configured.split(",") if configured else None, market)
        for receipt in result["records"]:
            log.info("ORACLE COMMITTED TRADE EVIDENCE | %s", json.dumps(
                {**receipt, "checked_at": result["checked_at"], "snapshot": result["snapshot"], "transaction_read_only": True},
                sort_keys=True, allow_nan=False))
        if not result["records"]:
            log.info("ORACLE COMMITTED TRADE EVIDENCE | market=%s | status=NO_RECENT_TRADES | execution_impact=NONE", market)
        return result
    except Exception as exc:
        log.warning("ORACLE COMMITTED TRADE EVIDENCE | market=%s | status=UNAVAILABLE | reason=%s | execution_impact=NONE", market, type(exc).__name__)
        return {"status": "unavailable", "market": market, "execution_impact": "NONE"}


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("trade_ids", nargs="+")
    parser.add_argument("--market", choices=("crypto", "cash"), default="crypto")
    args = parser.parse_args()
    print(json.dumps(verify_committed_trade_evidence(args.trade_ids, args.market), sort_keys=True, allow_nan=False))
