from __future__ import annotations

import json
from typing import Any, Iterable

from broker_order_journal import PersistentOrderJournal, normalize_remote_state
from database import connect, utc_now


PRE_SUBMISSION_STATES = {"CREATED", "PREFLIGHT", "PREVIEWED"}
BROKER_VISIBLE_STATES = {"SUBMITTING", "SUBMITTED", "OPEN", "PARTIALLY_FILLED", "UNKNOWN_RECONCILE_REQUIRED"}


def _client_id(remote: dict[str, Any]) -> str:
    return str(remote.get("client_order_id") or remote.get("client_order_uuid") or remote.get("client_id") or "").strip()


def _broker_id(remote: dict[str, Any]) -> str:
    return str(remote.get("id") or remote.get("order_id") or remote.get("broker_order_id") or "").strip()


def _float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def reconcile_persistent_journal(
    journal: PersistentOrderJournal,
    remote_orders: Iterable[dict[str, Any]],
    *,
    account_number_present: bool = True,
) -> dict[str, Any]:
    """Reconcile only states that could have reached the broker.

    CREATED/PREFLIGHT/PREVIEWED are local-only states and therefore do not
    become ambiguous merely because Robinhood has no matching remote order.
    SUBMITTING and later non-terminal states require broker confirmation and
    fail closed when that confirmation is missing.
    """
    remote_items = [dict(item) for item in remote_orders if isinstance(item, dict)]
    remote_by_client = {key: item for item in remote_items if (key := _client_id(item))}
    remote_by_broker = {key: item for item in remote_items if (key := _broker_id(item))}
    local = journal.unfinished()
    discrepancies: list[dict[str, Any]] = []
    reconciled = 0
    local_only = 0

    for item in local:
        client_id = str(item.get("client_order_id") or "")
        state = str(item.get("state") or "").upper()
        if state in PRE_SUBMISSION_STATES:
            local_only += 1
            continue
        if state not in BROKER_VISIBLE_STATES:
            discrepancies.append({"client_order_id": client_id, "reason": "INVALID_LOCAL_STATE"})
            continue

        broker_id = str(item.get("broker_order_id") or "")
        remote = remote_by_client.get(client_id) or (remote_by_broker.get(broker_id) if broker_id else None)
        if remote is None:
            journal.transition(
                client_id,
                "UNKNOWN_RECONCILE_REQUIRED",
                last_checked_at=utc_now(),
                payload={"reconciliation": "remote order not found"},
            )
            discrepancies.append({"client_order_id": client_id, "reason": "REMOTE_ORDER_NOT_FOUND"})
            continue

        remote_state = normalize_remote_state(remote.get("state") or remote.get("status"))
        filled_quantity = max(
            0.0,
            _float(remote.get("filled_asset_quantity") or remote.get("filled_quantity") or remote.get("executed_quantity") or 0.0),
        )
        average_price = _float(remote.get("average_price") or remote.get("average_fill_price") or remote.get("executed_price") or 0.0)
        journal.transition(
            client_id,
            remote_state,
            broker_order_id=_broker_id(remote) or broker_id or None,
            broker_state=str(remote.get("state") or remote.get("status") or ""),
            filled_quantity=filled_quantity,
            average_fill_price=average_price if average_price > 0 else None,
            last_checked_at=utc_now(),
            payload={"last_remote_state": remote_state},
        )
        reconciled += 1
        if remote_state == "UNKNOWN_RECONCILE_REQUIRED":
            discrepancies.append({"client_order_id": client_id, "reason": "UNKNOWN_REMOTE_STATE"})

    status = "PASS" if account_number_present and not discrepancies else "FAIL_CLOSED"
    payload = {
        "reconciled": reconciled,
        "local_only_pre_submission": local_only,
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
                json.dumps(payload, sort_keys=True, default=str),
                utc_now(),
            ),
        )
    return {
        "status": status,
        "local_unfinished": len(local),
        "local_only_pre_submission": local_only,
        "remote_orders": len(remote_items),
        "reconciled": reconciled,
        "discrepancies": discrepancies,
    }
