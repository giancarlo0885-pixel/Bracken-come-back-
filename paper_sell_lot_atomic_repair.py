from __future__ import annotations

from datetime import datetime, timezone
import logging
import math
import os
import uuid
from typing import Any


log = logging.getLogger("paper-sell-lot-atomic-repair")


def _finite(value: Any, default: float = 0.0) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return number if math.isfinite(number) else default


def _false_env(name: str) -> bool:
    return str(os.getenv(name, "false") or "false").strip().lower() in {"", "0", "false", "no", "off"}


def _active() -> bool:
    return (
        str(os.getenv("EXECUTION_MODE", "") or "").strip().lower() == "paper"
        and _false_env("ENABLE_BROKER_SUBMISSION")
        and _false_env("LIVE_TRADING_ARMED")
    )


def _opened_at(position: dict[str, Any], now: Any) -> datetime:
    value = position.get("opened_at") or position.get("updated_at") or now
    if isinstance(value, datetime):
        result = value
    else:
        try:
            result = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except (TypeError, ValueError):
            result = datetime.now(timezone.utc)
    if result.tzinfo is None:
        result = result.replace(tzinfo=timezone.utc)
    return result


def install_atomic_paper_sell_lot_repair(worker: Any) -> None:
    """Repair historical paper lot gaps inside the active SELL transaction.

    Older paper positions can predate the durable position-lot ledger. A previous
    repair attempted to reopen ``positions`` from a second connection after the
    SELL path had already deleted that row, so attribution could still fail with
    ``not enough open lot quantity to close``.

    This wrapper runs immediately before the existing sell-attribution function,
    on the caller's transaction and connection. It locks current open lots and,
    only in provably paper-only mode, backfills a missing historical lot from the
    canonical position snapshot already used by the SELL. Cost basis is derived
    from canonical position cost minus known open-lot cost; no price is invented.
    Any contradictory/insufficient evidence still fails closed and the enclosing
    SELL transaction rolls back.
    """
    original = worker._record_sell_attribution
    if getattr(original, "_oracle_atomic_paper_lot_repair", False):
        return

    def repaired(conn: Any, **kwargs: Any):
        if not _active():
            return original(conn, **kwargs)

        market = str(kwargs.get("market") or "").strip().lower()
        position = dict(kwargs.get("position") or {})
        symbol = str(position.get("symbol") or "").strip().upper()
        requested = max(0.0, _finite(kwargs.get("quantity")))
        canonical_qty = max(0.0, _finite(position.get("quantity")))
        canonical_avg = _finite(position.get("average_price", position.get("entry_price")))
        tolerance = max(1e-9, requested * 1e-9)

        if not market or not symbol or requested <= 0:
            return original(conn, **kwargs)

        lots = conn.execute(
            """
            SELECT *
            FROM position_lots
            WHERE market=%s AND symbol=%s AND quantity_remaining > 0
            ORDER BY opened_at ASC, id ASC
            FOR UPDATE
            """,
            (market, symbol),
        ).fetchall()
        open_qty = sum(max(0.0, _finite(row.get("quantity_remaining"))) for row in lots)

        if open_qty + tolerance < requested:
            if canonical_qty + tolerance < requested or canonical_qty <= 0 or canonical_avg <= 0:
                raise ValueError("paper sell lot repair lacks sufficient canonical position evidence")

            missing_qty = canonical_qty - open_qty
            if missing_qty <= tolerance:
                raise ValueError("paper sell lot repair found no defensible missing quantity")

            known_cost = sum(
                max(0.0, _finite(row.get("quantity_remaining")))
                * max(0.0, _finite(row.get("entry_price")))
                for row in lots
            )
            canonical_cost = canonical_qty * canonical_avg
            missing_cost = canonical_cost - known_cost
            implied_entry = missing_cost / missing_qty if missing_qty > 0 else 0.0
            if not math.isfinite(implied_entry) or implied_entry <= 0:
                raise ValueError("paper sell lot repair could not derive positive historical cost basis")

            lot_id = f"lot:atomic-reconcile:{market}:{symbol}:{uuid.uuid4()}"
            opened = _opened_at(position, kwargs.get("now"))
            created = kwargs.get("now") or datetime.now(timezone.utc)
            conn.execute(
                """
                INSERT INTO position_lots (
                    lot_id, symbol, market, bucket, strategy, opened_at,
                    quantity_opened, quantity_remaining, entry_price, entry_fees,
                    decision_id, broker_mode, account_environment, created_at
                )
                VALUES (%s,%s,%s,'Historical','atomic_canonical_lot_reconciliation',%s,%s,%s,%s,0,NULL,'PAPER','PAPER',%s)
                """,
                (
                    lot_id,
                    symbol,
                    market,
                    opened,
                    missing_qty,
                    missing_qty,
                    implied_entry,
                    created,
                ),
            )
            log.warning(
                "PAPER SELL LOT ATOMIC REPAIR | market=%s | symbol=%s | canonical_qty=%.10f | "
                "prior_open_qty=%.10f | backfilled_qty=%.10f | implied_entry=%.10f",
                market,
                symbol,
                canonical_qty,
                open_qty,
                missing_qty,
                implied_entry,
            )

        return original(conn, **kwargs)

    repaired._oracle_atomic_paper_lot_repair = True
    worker._record_sell_attribution = repaired
