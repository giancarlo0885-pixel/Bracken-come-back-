"""Read-only validation of explicit paper fill, execution and FIFO lot identity.

The durable graph is the graph written atomically by paper_execution_accounting.
No missing identity is reconstructed from a price, symbol, timestamp or later lot.
The provenance version below describes this validator, not a stored fee contract.
"""
from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
import json
from typing import Any

PROVENANCE_VERSION = "exact_fill_lot_v1"
ANNOTATIONS = (
    "_canonical_fill_fee", "_canonical_fill_id", "_canonical_lot_id",
    "_canonical_entry_trade_id", "_canonical_entry_fill_id",
    "_canonical_entry_fill_fee", "_canonical_entry_quantity",
    "_fill_accounting_provenance",
)


def number(value: Any) -> Decimal | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        result = Decimal(str(value))
        return result if result.is_finite() and abs(result) <= Decimal("1.7976931348623157e308") else None
    except (InvalidOperation, TypeError, ValueError):
        return None


def close(left: Any, right: Any, *, quantity: bool = False) -> bool:
    """Compare quantities relatively; currency precision cannot rescue tiny lots."""
    a, b = number(left), number(right)
    if a is None or b is None:
        return False
    if a == b:
        return True
    if not a or not b:
        return False
    floor = Decimal(0) if quantity else Decimal("1e-10")
    return abs(a - b) <= max(floor, max(abs(a), abs(b)) * Decimal("1e-9"))


def _time(value: Any) -> datetime | None:
    try:
        result = value if isinstance(value, datetime) else datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return result.astimezone(timezone.utc) if result.tzinfo else None
    except (TypeError, ValueError, OverflowError):
        return None


def load_fill_lot_allocations(
    conn: Any, *, trade_ids: Iterable[str] | None = None, market: str | None = None,
    as_of: datetime | str | None = None,
) -> list[dict[str, Any]]:
    """Read explicit links only, normalized for the accounting readers.

    Allocation fees are the proportional share of the actual stored fill fee.
    BUY links are joined by the exact lot ID. More than one BUY for a lot remains
    visible as ambiguity and can never receive an exact annotation.
    """
    cutoff = _time(as_of) if as_of is not None else datetime.now(timezone.utc)
    if cutoff is None:
        raise ValueError("as_of requires a valid timezone-aware timestamp")
    if not conn.execute("SELECT to_regclass('paper_fill_lot_links') AS relation").fetchone()["relation"]:
        return []
    conditions, params = [], []
    if trade_ids is not None:
        ids = list(dict.fromkeys(trade_ids))
        if not ids:
            return []
        conditions.append("a.ledger_trade_id=ANY(%s)")
        params.append(ids)
    if market is not None:
        if market not in {"cash", "crypto"}:
            raise ValueError("invalid market")
        conditions.append("ledger.market=%s")
        params.append(market)
    where = " WHERE " + " AND ".join(conditions) if conditions else ""
    rows = conn.execute("""
        SELECT a.ledger_trade_id AS trade_id,a.lot_id,a.fill_id,f.side,
          a.quantity AS allocated_quantity,
          f.fee_amount*a.quantity/NULLIF(f.quantity,0) AS allocated_fee,
          to_jsonb(f) AS fill,to_jsonb(o) AS paper_order,
          to_jsonb(t) AS execution,to_jsonb(l) AS lot,to_jsonb(ledger) AS ledger,
          tot.qty AS total_quantity,tot.fee AS total_fee,tot.gross AS total_gross_pnl,
          CASE WHEN entry.ledger_trade_id IS NOT NULL THEN jsonb_build_object(
            'trade_id',entry.ledger_trade_id,'lot_id',entry.lot_id,'fill_id',entry.fill_id,
            'side',ef.side,'quantity',entry.quantity,
            'fee_amount',ef.fee_amount*entry.quantity/NULLIF(ef.quantity,0)) END AS entry_allocation,
          to_jsonb(ef) AS entry_fill,to_jsonb(eo) AS entry_order,
          to_jsonb(et) AS entry_execution,to_jsonb(el) AS entry_ledger,
          etot.qty AS entry_total_quantity,etot.fee AS entry_total_fee,
          etot.gross AS entry_total_gross_pnl
        FROM paper_fill_lot_links a
        JOIN paper_fills f ON f.fill_id=a.fill_id
        LEFT JOIN paper_orders o ON o.order_id=f.order_id
        LEFT JOIN trades t ON t.id=f.canonical_trade_id
        JOIN position_lots l ON l.lot_id=a.lot_id
        JOIN trade_ledger ledger ON ledger.trade_id=a.ledger_trade_id
        LEFT JOIN LATERAL (
          SELECT SUM(x.quantity) qty,
            SUM(f.fee_amount*x.quantity/NULLIF(f.quantity,0)) fee,
            SUM(CASE WHEN f.side='BUY' THEN 0 ELSE (f.fill_price*1.0-xl.entry_price)*x.quantity END) gross
          FROM paper_fill_lot_links x JOIN trade_ledger xl ON xl.trade_id=x.ledger_trade_id
          WHERE x.fill_id=a.fill_id
        ) tot ON TRUE
        LEFT JOIN (paper_fill_lot_links entry JOIN paper_fills entry_candidate
          ON entry_candidate.fill_id=entry.fill_id AND entry_candidate.side='BUY')
          ON entry.lot_id=a.lot_id
        LEFT JOIN paper_fills ef ON ef.fill_id=entry.fill_id
        LEFT JOIN paper_orders eo ON eo.order_id=ef.order_id
        LEFT JOIN trades et ON et.id=ef.canonical_trade_id
        LEFT JOIN trade_ledger el ON el.trade_id=entry.ledger_trade_id
        LEFT JOIN LATERAL (
          SELECT SUM(x.quantity) qty,
            SUM(ef.fee_amount*x.quantity/NULLIF(ef.quantity,0)) fee,
            SUM(CASE WHEN ef.side='BUY' THEN 0 ELSE (ef.fill_price*1.0-xl.entry_price)*x.quantity END) gross
          FROM paper_fill_lot_links x JOIN trade_ledger xl ON xl.trade_id=x.ledger_trade_id
          WHERE x.fill_id=entry.fill_id
        ) etot ON TRUE
        """ + where, tuple(params)).fetchall()
    # Count every committed SELL edge for each lot, including IDs outside the
    # requested subset. A later close must not allocate the same entry fees twice.
    # Parse TEXT timestamps in Python: malformed dates cannot safely be classified
    # before/after the cutoff and invalidate that lot, without aborting other IDs.
    lot_ids = list({row["lot_id"] for row in rows})
    closed, invalid_lots = {lot_id: Decimal(0) for lot_id in lot_ids}, set()
    if lot_ids:
        exits = conn.execute("""SELECT a.lot_id,a.quantity,f.created_at
            FROM paper_fill_lot_links a JOIN paper_fills f ON f.fill_id=a.fill_id
            WHERE a.lot_id=ANY(%s) AND f.side='SELL'""", (lot_ids,)).fetchall()
        for exit_row in exits:
            observed = _time(exit_row["created_at"])
            if observed is None:
                invalid_lots.add(exit_row["lot_id"])
                continue
            if observed > cutoff:
                continue
            quantity = number(exit_row["quantity"])
            if quantity is None or quantity <= 0:
                invalid_lots.add(exit_row["lot_id"])
                continue
            closed[exit_row["lot_id"]] += quantity
    normalized = []
    for raw in rows:
        row = dict(raw)
        observed = _time((row.get("fill") or {}).get("created_at"))
        entry_observed = _time((row.get("entry_fill") or {}).get("created_at"))
        if ((observed is not None and observed > cutoff)
                or (entry_observed is not None and entry_observed > cutoff)):
            continue
        row.update(provenance_version=PROVENANCE_VERSION,
                   lot_closed_quantity=None if row["lot_id"] in invalid_lots else str(closed[row["lot_id"]]))
        normalized.append(row)
    return normalized


def _valid_link(a, fill, order, execution, ledger, lot, total_qty, total_fee, total_gross, lot_closed_quantity) -> bool:
    objects = (a, fill, order, execution, ledger, lot)
    if not all(isinstance(o, dict) and o for o in objects):
        return False
    side = a.get("side")
    q, fee = number(a.get("quantity")), number(a.get("fee_amount"))
    fill_q, fill_fee, price, opened = (number(fill.get("quantity")), number(fill.get("fee_amount")),
                                      number(fill.get("fill_price")), number(lot.get("quantity_opened")))
    closed = number(lot_closed_quantity)
    if (side not in {"BUY", "SELL"} or any(v is None for v in (q, fee, fill_q, fill_fee, price, opened))
            or min(q, fill_q, price, opened) <= 0 or min(fee, fill_fee) < 0
            or closed is None or closed < 0
            or (closed > opened and not close(closed, opened, quantity=True))
            or (side == "SELL" and q > closed and not close(q, closed, quantity=True))
            or not a.get("trade_id") or a.get("trade_id") != ledger.get("trade_id")
            or not a.get("lot_id") or a.get("lot_id") != lot.get("lot_id")
            or not a.get("fill_id") or a.get("fill_id") != fill.get("fill_id")
            or type(fill.get("canonical_trade_id")) is not int or fill.get("canonical_trade_id") <= 0
            or type(execution.get("id")) is not int
            or fill.get("canonical_trade_id") != execution.get("id")
            or not fill.get("order_id") or fill.get("order_id") != order.get("order_id")
            or ledger.get("market") not in {"cash", "crypto"} or not ledger.get("symbol")
            or any(o.get("market") != ledger.get("market") or o.get("symbol") != ledger.get("symbol") for o in (fill, order, execution, lot))
            or any(o.get("side") != side for o in (fill, order, execution, ledger))
            or any(o.get("broker_mode") != "PAPER" or o.get("account_environment") != "PAPER" for o in (ledger, lot))
            or not close(q, ledger.get("quantity"), quantity=True) or q > fill_q or q > opened
            or not close(fee, fill_fee*q/fill_q, quantity=True)
            or not close(fee, ledger.get("fees"))
            or not close(total_qty, fill_q, quantity=True) or not close(total_fee, fill_fee)
            or not close(order.get("filled_quantity"), fill_q, quantity=True)
            or not close(execution.get("quantity"), fill_q, quantity=True)
            or not close(order.get("fee_amount"), fill_fee) or not close(execution.get("fees"), fill_fee)
            or not close(order.get("average_fill_price"), price) or not close(execution.get("price"), price)
            or not close(ledger.get("entry_price") if side == "BUY" else ledger.get("exit_price"), price)
            or not close(fill.get("notional"), fill_q*price)
            or not close(execution.get("value"), fill.get("notional"))
            or not close(order.get("filled_notional"), fill.get("notional"))
            or not close(ledger.get("entry_price"), lot.get("entry_price"))
            or _time(ledger.get("entry_time")) is None
            or _time(ledger.get("entry_time")) != _time(lot.get("opened_at"))
            or _time(ledger.get("entry_time") if side == "BUY" else ledger.get("exit_time")) != _time(execution.get("created_at"))):
        return False
    entry_price = number(ledger.get("entry_price"))
    gross = number(total_gross)
    if entry_price is None or entry_price <= 0 or gross is None:
        return False
    allocated_gross = Decimal(0) if side == "BUY" else q*(price-entry_price)
    if (not close(ledger.get("gross_pnl"), allocated_gross)
            or not close(ledger.get("net_pnl"), allocated_gross-fee)
            or not close(execution.get("gross_realized_pnl"), gross)
            or not close(execution.get("realized_pnl"), gross-fill_fee if side == "SELL" else Decimal(0))):
        return False
    event, observed = _time(execution.get("created_at")), _time(fill.get("created_at"))
    if event is None or observed is None or observed < event:
        return False
    if side == "BUY":
        return (close(q, opened, quantity=True) and close(fee, lot.get("entry_fees"))
                and close(gross, 0))
    return _time(ledger.get("exit_time")) >= _time(ledger.get("entry_time"))



def _same_json(left: Any, right: Any) -> bool:
    """Compare canonical feature evidence without bool/number or NaN matches."""
    if isinstance(left, dict) and isinstance(right, dict):
        return (all(isinstance(key, str) for key in left) and left.keys() == right.keys()
                and all(_same_json(left[key], right[key]) for key in left))
    if isinstance(left, list) and isinstance(right, list):
        return len(left) == len(right) and all(_same_json(a, b) for a, b in zip(left, right))
    if isinstance(left, bool) or isinstance(right, bool):
        return type(left) is type(right) and left == right
    if isinstance(left, (int, float)) and isinstance(right, (int, float)):
        return number(left) is not None and number(right) is not None and left == right
    return (left is None and right is None) or (isinstance(left, str) and isinstance(right, str) and left == right)


def attach_exact_fill_lot_evidence(
    ledger: Iterable[dict[str, Any]], allocations: Iterable[dict[str, Any]],
    *, as_of: datetime | str | None = None,
) -> list[dict[str, Any]]:
    """Return copies with exact annotations only for a validated entry/exit graph.

    Identical repeated raw rows are deduplicated. Conflicting or malformed rows
    invalidate that trade alone; they cannot break validation of other trades.
    Stale caller annotations are always removed before this validation.
    """
    by_id, malformed = {}, set()
    for candidate in allocations:
        if not isinstance(candidate, dict):
            continue
        key = candidate.get("trade_id")
        if not isinstance(key, str) or not key:
            continue
        try:
            fingerprint = json.dumps(candidate, sort_keys=True, default=str, allow_nan=False)
            # Decimal NaN/Inf and booleans in accounting fields are rejected by
            # number() during validation, even though default=str serializes them.
            by_id.setdefault(key, {})[fingerprint] = candidate
        except (ValueError, TypeError, OverflowError):
            malformed.add(key)
    cutoff = _time(as_of) if as_of is not None else datetime.now(timezone.utc)
    result = []
    for raw in ledger:
        row = {key: value for key, value in raw.items() if key not in ANNOTATIONS}
        candidates = list(by_id.get(row.get("trade_id"), {}).values())
        if row.get("trade_id") in malformed or len(candidates) != 1 or (as_of is not None and cutoff is None):
            result.append(row)
            continue
        evidence = candidates[0]
        a = {"trade_id": evidence.get("trade_id"), "lot_id": evidence.get("lot_id"), "fill_id": evidence.get("fill_id"),
             "side": evidence.get("side"), "quantity": evidence.get("allocated_quantity"), "fee_amount": evidence.get("allocated_fee")}
        fill, order, execution, lot, stored = (evidence.get(k) for k in ("fill", "paper_order", "execution", "lot", "ledger"))
        keys = ("trade_id", "market", "symbol", "side", "entry_signal_id", "entry_decision_id", "entry_forecast_id", "entry_quote_id")
        matches = isinstance(stored, dict) and all(row.get(k) == stored.get(k) for k in keys)
        numeric = ("quantity", "entry_price", "fees", "gross_pnl", "net_pnl") + (("exit_price",) if row.get("side") == "SELL" else ())
        matches = matches and all(close(row.get(k), stored.get(k), quantity=k == "quantity") for k in numeric)
        matches = matches and all(_time(row.get(k)) == _time(stored.get(k)) for k in ("entry_time", "exit_time"))
        matches = matches and _same_json(row.get("feature_snapshot"), stored.get("feature_snapshot"))
        if not matches or not _valid_link(a, fill, order, execution, stored, lot, evidence.get("total_quantity"), evidence.get("total_fee"), evidence.get("total_gross_pnl"), evidence.get("lot_closed_quantity")):
            result.append(row)
            continue
        entry_a = a if row["side"] == "BUY" else evidence.get("entry_allocation")
        entry_fill = fill if row["side"] == "BUY" else evidence.get("entry_fill")
        entry_order = order if row["side"] == "BUY" else evidence.get("entry_order")
        entry_execution = execution if row["side"] == "BUY" else evidence.get("entry_execution")
        entry_ledger = stored if row["side"] == "BUY" else evidence.get("entry_ledger")
        entry_total_qty = evidence.get("total_quantity") if row["side"] == "BUY" else evidence.get("entry_total_quantity")
        entry_total_fee = evidence.get("total_fee") if row["side"] == "BUY" else evidence.get("entry_total_fee")
        entry_total_gross = evidence.get("total_gross_pnl") if row["side"] == "BUY" else evidence.get("entry_total_gross_pnl")
        if (not isinstance(entry_a, dict) or entry_a.get("side") != "BUY"
                or not _valid_link(entry_a, entry_fill, entry_order, entry_execution, entry_ledger, lot, entry_total_qty, entry_total_fee, entry_total_gross, evidence.get("lot_closed_quantity"))
                or any(stored.get(k) != entry_ledger.get(k) for k in ("entry_signal_id", "entry_decision_id", "entry_forecast_id", "entry_quote_id"))
                or (cutoff is not None and any(_time(f.get("created_at")) > cutoff for f in (fill, entry_fill)))):
            result.append(row)
            continue
        row.update(_canonical_fill_fee=str(number(a["fee_amount"])), _canonical_fill_id=a["fill_id"],
                   _canonical_lot_id=a["lot_id"], _canonical_entry_trade_id=entry_a["trade_id"],
                   _canonical_entry_fill_id=entry_fill["fill_id"], _canonical_entry_fill_fee=str(number(entry_a["fee_amount"])),
                   _canonical_entry_quantity=str(number(entry_a["quantity"])), _fill_accounting_provenance=PROVENANCE_VERSION)
        result.append(row)
    return result
