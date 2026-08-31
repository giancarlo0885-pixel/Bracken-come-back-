from __future__ import annotations

from dataclasses import dataclass
import json
import math
from typing import Any, Iterable

from database import connect, row, rows, utc_now


TERMINAL_STATES = {"FILLED", "CANCELED", "REJECTED"}
ORDER_STATES = {
    "CREATED",
    "PREFLIGHT",
    "PREVIEWED",
    "SUBMITTING",
    "SUBMITTED",
    "OPEN",
    "PARTIALLY_FILLED",
    "FILLED",
    "CANCELED",
    "REJECTED",
    "UNKNOWN_RECONCILE_REQUIRED",
}
_ALLOWED_TRANSITIONS = {
    "CREATED": {"PREFLIGHT", "PREVIEWED", "SUBMITTING", "CANCELED", "REJECTED"},
    "PREFLIGHT": {"PREVIEWED", "SUBMITTING", "CANCELED", "REJECTED"},
    "PREVIEWED": {"SUBMITTING", "CANCELED", "REJECTED"},
    "SUBMITTING": {"SUBMITTED", "OPEN", "PARTIALLY_FILLED", "FILLED", "REJECTED", "UNKNOWN_RECONCILE_REQUIRED"},
    "SUBMITTED": {"OPEN", "PARTIALLY_FILLED", "FILLED", "CANCELED", "REJECTED", "UNKNOWN_RECONCILE_REQUIRED"},
    "OPEN": {"OPEN", "PARTIALLY_FILLED", "FILLED", "CANCELED", "REJECTED", "UNKNOWN_RECONCILE_REQUIRED"},
    "PARTIALLY_FILLED": {"PARTIALLY_FILLED", "FILLED", "CANCELED", "REJECTED", "UNKNOWN_RECONCILE_REQUIRED"},
    "UNKNOWN_RECONCILE_REQUIRED": {"UNKNOWN_RECONCILE_REQUIRED", "SUBMITTED", "OPEN", "PARTIALLY_FILLED", "FILLED", "CANCELED", "REJECTED"},
    "FILLED": {"FILLED"},
    "CANCELED": {"CANCELED"},
    "REJECTED": {"REJECTED"},
}


def _finite(value: Any, default: float = 0.0) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return number if math.isfinite(number) else default


def _clean_state(value: Any) -> str:
    return str(value or "").strip().upper().replace("-", "_").replace(" ", "_")


def normalize_remote_state(value: Any) -> str:
    state = _clean_state(value)
    aliases = {
        "NEW": "SUBMITTED",
        "PENDING": "SUBMITTED",
        "CONFIRMED": "OPEN",
        "UNCONFIRMED": "SUBMITTED",
        "QUEUED": "SUBMITTED",
        "PARTIAL": "PARTIALLY_FILLED",
        "PARTIALLYFILLED": "PARTIALLY_FILLED",
        "PARTIALLY_FILLED": "PARTIALLY_FILLED",
        "COMPLETE": "FILLED",
        "COMPLETED": "FILLED",
        "FILLED": "FILLED",
        "CANCELLED": "CANCELED",
        "CANCELED": "CANCELED",
        "REJECTED": "REJECTED",
        "FAILED": "REJECTED",
        "OPEN": "OPEN",
    }
    return aliases.get(state, "UNKNOWN_RECONCILE_REQUIRED")


def _remote_client_id(remote: dict[str, Any]) -> str:
    return str(
        remote.get("client_order_id")
        or remote.get("client_order_uuid")
        or remote.get("client_id")
        or ""
    ).strip()


def _remote_broker_id(remote: dict[str, Any]) -> str:
    return str(remote.get("id") or remote.get("order_id") or remote.get("broker_order_id") or "").strip()


def _remote_state(remote: dict[str, Any]) -> str:
    return normalize_remote_state(remote.get("state") or remote.get("status"))


def _remote_fill_quantity(remote: dict[str, Any]) -> float:
    return max(
        0.0,
        _finite(
            remote.get("filled_asset_quantity")
            or remote.get("filled_quantity")
            or remote.get("executed_quantity")
            or 0.0
        ),
    )


def _remote_average_fill_price(remote: dict[str, Any]) -> float | None:
    value = _finite(
        remote.get("average_price")
        or remote.get("average_fill_price")
        or remote.get("executed_price")
        or 0.0
    )
    return value if value > 0 else None


@dataclass(frozen=True)
class JournalRecord:
    client_order_id: str
    state: str
    symbol: str
    side: str
    quantity: float
    notional: float
    broker_order_id: str | None = None


class PersistentOrderJournal:
    """PostgreSQL-backed broker order journal.

    This class never sends orders. It provides durable idempotency, state
    transitions, timeout ambiguity handling, and restart reconciliation.
    """

    durable = True

    def create(
        self,
        *,
        client_order_id: str,
        symbol: str,
        side: str,
        quantity: float,
        notional: float,
        market: str = "crypto",
        proposal_id: str | None = None,
        approval_hash: str | None = None,
        order_type: str = "market",
        time_in_force: str = "gtc",
        correlation_id: str | None = None,
        payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        client_order_id = str(client_order_id or "").strip()
        symbol = str(symbol or "").upper().strip()
        side = str(side or "").upper().strip()
        quantity = _finite(quantity)
        notional = _finite(notional)
        if not client_order_id or not symbol or side not in {"BUY", "SELL"} or quantity <= 0:
            raise ValueError("journal requires client_order_id, symbol, BUY/SELL, and positive quantity")
        if notional < 0:
            raise ValueError("journal notional cannot be negative")

        now = utc_now()
        encoded = json.dumps(payload or {}, sort_keys=True, default=str)
        with connect() as conn:
            existing = conn.execute(
                "SELECT * FROM broker_order_journal WHERE client_order_id=%s FOR UPDATE",
                (client_order_id,),
            ).fetchone()
            if existing:
                same_intent = (
                    str(existing.get("symbol") or "").upper() == symbol
                    and str(existing.get("side") or "").upper() == side
                    and abs(_finite(existing.get("quantity")) - quantity) <= 1e-12
                    and abs(_finite(existing.get("notional")) - notional) <= 1e-8
                )
                if not same_intent:
                    raise RuntimeError("client_order_id already exists with different immutable order intent")
                return dict(existing)
            created = conn.execute(
                """
                INSERT INTO broker_order_journal (
                    client_order_id, proposal_id, approval_hash, symbol, market, side,
                    quantity, notional, order_type, time_in_force, state,
                    correlation_id, payload, created_at, updated_at
                ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,'CREATED',%s,%s::jsonb,%s,%s)
                RETURNING *
                """,
                (
                    client_order_id,
                    proposal_id,
                    approval_hash,
                    symbol,
                    str(market or "crypto").lower(),
                    side,
                    quantity,
                    notional,
                    str(order_type or "market").lower(),
                    str(time_in_force or "gtc").lower(),
                    correlation_id,
                    encoded,
                    now,
                    now,
                ),
            ).fetchone()
            return dict(created)

    def get(self, client_order_id: str) -> dict[str, Any] | None:
        return row("SELECT * FROM broker_order_journal WHERE client_order_id=%s", (client_order_id,))

    def unfinished(self) -> list[dict[str, Any]]:
        return rows(
            """
            SELECT * FROM broker_order_journal
            WHERE state NOT IN ('FILLED','CANCELED','REJECTED')
            ORDER BY created_at ASC
            """
        )

    def has_conflict(self, symbol: str, side: str | None = None) -> bool:
        symbol = str(symbol or "").upper().strip()
        params: tuple[Any, ...]
        if side:
            query = """
                SELECT 1 AS found FROM broker_order_journal
                WHERE symbol=%s AND side=%s
                  AND state NOT IN ('FILLED','CANCELED','REJECTED') LIMIT 1
            """
            params = (symbol, str(side).upper())
        else:
            query = """
                SELECT 1 AS found FROM broker_order_journal
                WHERE symbol=%s AND state NOT IN ('FILLED','CANCELED','REJECTED') LIMIT 1
            """
            params = (symbol,)
        return row(query, params) is not None

    def transition(self, client_order_id: str, new_state: str, **fields: Any) -> dict[str, Any]:
        new_state = _clean_state(new_state)
        if new_state not in ORDER_STATES:
            raise ValueError(f"unknown broker order state: {new_state}")
        now = utc_now()
        with connect() as conn:
            current = conn.execute(
                "SELECT * FROM broker_order_journal WHERE client_order_id=%s FOR UPDATE",
                (client_order_id,),
            ).fetchone()
            if not current:
                raise KeyError(f"unknown client_order_id: {client_order_id}")
            old_state = _clean_state(current.get("state"))
            if new_state not in _ALLOWED_TRANSITIONS.get(old_state, set()):
                raise RuntimeError(f"invalid broker order transition {old_state}->{new_state}")

            broker_order_id = fields.get("broker_order_id") or current.get("broker_order_id")
            broker_state = fields.get("broker_state") or current.get("broker_state")
            filled_quantity = max(_finite(current.get("filled_quantity")), _finite(fields.get("filled_quantity")))
            average_fill_price = fields.get("average_fill_price")
            if average_fill_price is None:
                average_fill_price = current.get("average_fill_price")
            fees = max(_finite(current.get("fees")), _finite(fields.get("fees")))
            reject_reason = fields.get("reject_reason") or current.get("reject_reason")
            submitted_at = fields.get("submitted_at") or current.get("submitted_at")
            last_checked_at = fields.get("last_checked_at") or now
            payload = dict(current.get("payload") or {})
            payload_update = fields.get("payload")
            if isinstance(payload_update, dict):
                payload.update(payload_update)

            updated = conn.execute(
                """
                UPDATE broker_order_journal
                SET state=%s, broker_order_id=%s, broker_state=%s,
                    filled_quantity=%s, average_fill_price=%s, fees=%s,
                    reject_reason=%s, submitted_at=%s, last_checked_at=%s,
                    payload=%s::jsonb, updated_at=%s
                WHERE client_order_id=%s
                RETURNING *
                """,
                (
                    new_state,
                    broker_order_id,
                    broker_state,
                    filled_quantity,
                    average_fill_price,
                    fees,
                    reject_reason,
                    submitted_at,
                    last_checked_at,
                    json.dumps(payload, sort_keys=True, default=str),
                    now,
                    client_order_id,
                ),
            ).fetchone()
            return dict(updated)

    def mark_submit_timeout(self, client_order_id: str) -> dict[str, Any]:
        return self.transition(
            client_order_id,
            "UNKNOWN_RECONCILE_REQUIRED",
            reject_reason="submission result unknown; broker reconciliation required before retry",
        )

    def reconcile(self, remote_orders: Iterable[dict[str, Any]], *, account_number_present: bool = True) -> dict[str, Any]:
        remote_items = [dict(item) for item in remote_orders if isinstance(item, dict)]
        remote_by_client = {key: item for item in remote_items if (key := _remote_client_id(item))}
        remote_by_broker = {key: item for item in remote_items if (key := _remote_broker_id(item))}
        local = self.unfinished()
        discrepancies: list[dict[str, Any]] = []
        reconciled = 0

        for item in local:
            client_id = str(item.get("client_order_id") or "")
            broker_id = str(item.get("broker_order_id") or "")
            remote = remote_by_client.get(client_id) or (remote_by_broker.get(broker_id) if broker_id else None)
            if remote is None:
                self.transition(
                    client_id,
                    "UNKNOWN_RECONCILE_REQUIRED",
                    last_checked_at=utc_now(),
                    payload={"reconciliation": "remote order not found"},
                )
                discrepancies.append({"client_order_id": client_id, "reason": "REMOTE_ORDER_NOT_FOUND"})
                continue

            state = _remote_state(remote)
            self.transition(
                client_id,
                state,
                broker_order_id=_remote_broker_id(remote) or broker_id or None,
                broker_state=str(remote.get("state") or remote.get("status") or ""),
                filled_quantity=_remote_fill_quantity(remote),
                average_fill_price=_remote_average_fill_price(remote),
                last_checked_at=utc_now(),
                payload={"last_remote_state": state},
            )
            reconciled += 1
            if state == "UNKNOWN_RECONCILE_REQUIRED":
                discrepancies.append({"client_order_id": client_id, "reason": "UNKNOWN_REMOTE_STATE"})

        status = "PASS" if not discrepancies else "FAIL_CLOSED"
        run_payload = {
            "reconciled": reconciled,
            "discrepancies": discrepancies,
        }
        with connect() as conn:
            conn.execute(
                """
                INSERT INTO broker_reconciliation_runs (
                    status, account_number_present, local_unfinished, remote_orders,
                    discrepancies, payload, created_at
                ) VALUES (%s,%s,%s,%s,%s,%s::jsonb,%s)
                """,
                (
                    status,
                    bool(account_number_present),
                    len(local),
                    len(remote_items),
                    len(discrepancies),
                    json.dumps(run_payload, sort_keys=True, default=str),
                    utc_now(),
                ),
            )
        return {
            "status": status,
            "local_unfinished": len(local),
            "remote_orders": len(remote_items),
            "reconciled": reconciled,
            "discrepancies": discrepancies,
        }


def latest_reconciliation() -> dict[str, Any] | None:
    return row("SELECT * FROM broker_reconciliation_runs ORDER BY id DESC LIMIT 1")


def durable_journal_health() -> dict[str, Any]:
    try:
        count = row("SELECT COUNT(*)::int AS count FROM broker_order_journal") or {"count": 0}
        unresolved = row(
            """
            SELECT COUNT(*)::int AS count FROM broker_order_journal
            WHERE state='UNKNOWN_RECONCILE_REQUIRED'
            """
        ) or {"count": 0}
        latest = latest_reconciliation()
        return {
            "ok": True,
            "durable": True,
            "records": int(count.get("count") or 0),
            "unresolved": int(unresolved.get("count") or 0),
            "latest_reconciliation_status": str((latest or {}).get("status") or "NEVER_RUN"),
        }
    except Exception as exc:
        return {
            "ok": False,
            "durable": False,
            "records": 0,
            "unresolved": None,
            "latest_reconciliation_status": "UNAVAILABLE",
            "reason": exc.__class__.__name__,
        }
