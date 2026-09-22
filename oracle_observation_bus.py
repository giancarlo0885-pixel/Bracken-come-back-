from __future__ import annotations

"""Canonical, research-only observation bus for Oracle.

This module mirrors already-persisted Oracle evidence into one append-only,
deduplicated timeline. It never participates in order approval, sizing, broker
submission, or live-money controls. Event time and ingestion time remain
separate so downstream research can enforce point-in-time boundaries.
"""

import hashlib
import json
from datetime import datetime, timezone
from typing import Any

SOURCE_SPECS = (
    ("signals", "signal", "created_at", "market", "symbol"),
    ("oracle_decision_audit", "council_decision", "created_at", "market", "symbol"),
    ("global_decision_events", "decision_funnel", "created_at", "market", "symbol"),
    ("intelligence_events", "market_intelligence", "event_time", None, "symbol"),
)
DEFAULT_BATCH = 500
MAX_PAYLOAD_BYTES = 24000


def _json_obj(value: Any) -> Any:
    if value is None:
        return {}
    if isinstance(value, (dict, list, int, float, bool)):
        return value
    if isinstance(value, str):
        try:
            return json.loads(value)
        except Exception:
            return {"value": value}
    return {"value": str(value)}


def _safe_payload(row: dict[str, Any]) -> dict[str, Any]:
    blocked = {"api_key", "apikey", "token", "secret", "password", "authorization"}
    payload: dict[str, Any] = {}
    for key, value in row.items():
        if str(key).lower() in blocked:
            continue
        if key in {"details", "payload", "metadata"}:
            payload[key] = _json_obj(value)
        else:
            payload[key] = value
    encoded = json.dumps(payload, default=str, sort_keys=True, separators=(",", ":"))
    if len(encoded.encode("utf-8")) <= MAX_PAYLOAD_BYTES:
        return payload
    return {
        "truncated": True,
        "source_fields": sorted(payload),
        "sha256": hashlib.sha256(encoded.encode("utf-8")).hexdigest(),
    }


def _event_key(source_table: str, row: dict[str, Any]) -> str:
    source_id = row.get("id")
    if source_id is not None:
        return f"{source_table}:{source_id}"
    raw = json.dumps(row, default=str, sort_keys=True, separators=(",", ":"))
    return f"{source_table}:sha256:{hashlib.sha256(raw.encode('utf-8')).hexdigest()}"


def _sync_source(
    conn: Any,
    source_table: str,
    observation_type: str,
    event_time_column: str,
    market_column: str | None,
    symbol_column: str | None,
    *,
    limit: int,
) -> int:
    cursor = conn.execute(
        """SELECT COALESCE(MAX(source_id),0) AS last_id
           FROM oracle_brain_observations WHERE source_table=%s""",
        (source_table,),
    ).fetchone() or {}
    last_id = int(cursor.get("last_id") or 0)
    time_expr = (
        f"COALESCE(NULLIF({event_time_column}::text,''),created_at::text)"
        if event_time_column != "created_at"
        else "created_at::text"
    )
    rows = list(
        conn.execute(
            f"""SELECT *, {time_expr} AS _observation_event_time
                FROM {source_table}
                WHERE id > %s ORDER BY id ASC LIMIT %s""",
            (last_id, max(1, int(limit))),
        ).fetchall()
    )
    inserted = 0
    for raw in rows:
        row = dict(raw)
        source_id = int(row.get("id") or 0)
        event_time = row.pop("_observation_event_time", None) or datetime.now(timezone.utc).isoformat()
        market = str(row.get(market_column) or "global") if market_column else "global"
        symbol = str(row.get(symbol_column) or "").upper().strip() if symbol_column else ""
        payload = _safe_payload(row)
        result = conn.execute(
            """INSERT INTO oracle_brain_observations(
                   event_key,source_table,source_id,observation_type,market,symbol,
                   event_time,ingested_at,payload,execution_impact
               ) VALUES (%s,%s,%s,%s,%s,%s,%s,NOW(),%s::jsonb,'NONE')
               ON CONFLICT(event_key) DO NOTHING""",
            (
                _event_key(source_table, row),
                source_table,
                source_id,
                observation_type,
                market,
                symbol or None,
                event_time,
                json.dumps(payload, default=str),
            ),
        )
        inserted += max(0, int(getattr(result, "rowcount", 1) or 0))
    return inserted


def sync_observations(conn: Any, *, limit_per_source: int = DEFAULT_BATCH) -> dict[str, Any]:
    """Mirror canonical persisted evidence into the append-only observation bus."""
    counts: dict[str, int] = {}
    for source_table, observation_type, event_time, market, symbol in SOURCE_SPECS:
        counts[source_table] = _sync_source(
            conn,
            source_table,
            observation_type,
            event_time,
            market,
            symbol,
            limit=limit_per_source,
        )
    return {
        "status": "ok",
        "inserted": sum(counts.values()),
        "by_source": counts,
        "execution_impact": "NONE",
    }


def observations_as_of(
    conn: Any,
    *,
    decision_time: str,
    market: str | None = None,
    symbol: str | None = None,
    limit: int = 200,
) -> list[dict[str, Any]]:
    """Point-in-time research retrieval. Future observations are excluded."""
    clauses = ["event_time::timestamptz <= %s::timestamptz"]
    params: list[Any] = [decision_time]
    if market:
        clauses.append("market IN (%s,'global')")
        params.append(market)
    if symbol:
        clauses.append("(symbol=%s OR symbol IS NULL)")
        params.append(symbol.upper())
    params.append(max(1, int(limit)))
    return list(
        conn.execute(
            f"""SELECT event_key,source_table,source_id,observation_type,market,symbol,
                       event_time,ingested_at,payload,execution_impact
                FROM oracle_brain_observations
                WHERE {' AND '.join(clauses)}
                ORDER BY event_time::timestamptz DESC,id DESC
                LIMIT %s""",
            tuple(params),
        ).fetchall()
    )


__all__ = ["observations_as_of", "sync_observations"]
