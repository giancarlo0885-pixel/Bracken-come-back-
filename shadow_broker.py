from __future__ import annotations

from dataclasses import dataclass, asdict
import json
import math
import uuid
from typing import Any

from database import connect, row, utc_now
from robinhood_crypto_api import best_bid_ask


def _finite(value: Any, default: float = 0.0) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return number if math.isfinite(number) else default


@dataclass(frozen=True)
class ShadowBrokerRecord:
    shadow_order_id: str
    paper_fill_id: str | None
    decision_id: str | None
    proposal_id: str | None
    symbol: str
    market: str
    side: str
    quantity: float
    notional: float
    oracle_reference_price: float
    paper_fill_price: float | None
    broker_bid: float
    broker_ask: float
    broker_mid: float
    broker_spread_pct: float
    hypothetical_fill_price: float
    created_at: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def record_shadow_order(
    *,
    symbol: str,
    side: str,
    quantity: float,
    oracle_reference_price: float,
    broker_quote: dict[str, Any],
    paper_fill_id: str | None = None,
    decision_id: str | None = None,
    proposal_id: str | None = None,
    paper_fill_price: float | None = None,
    market: str = "crypto",
    payload: dict[str, Any] | None = None,
) -> ShadowBrokerRecord:
    """Persist the order the Oracle would have submitted using broker market truth.

    No broker mutation occurs here. BUY assumes the executable ask; SELL assumes
    the executable bid. This intentionally avoids favorable mid-price fills.
    """
    symbol = str(symbol or "").upper().strip()
    side = str(side or "").upper().strip()
    quantity = _finite(quantity)
    reference = _finite(oracle_reference_price)
    if not symbol or side not in {"BUY", "SELL"} or quantity <= 0 or reference <= 0:
        raise ValueError("shadow order requires symbol, BUY/SELL, positive quantity, and positive reference price")
    quote_symbol = str(broker_quote.get("symbol") or "").upper().strip()
    if quote_symbol != symbol:
        raise ValueError("shadow broker quote symbol mismatch")
    book = best_bid_ask(broker_quote)
    if book is None:
        raise ValueError("shadow broker quote is invalid")

    broker_bid = float(book["bid"])
    broker_ask = float(book["ask"])
    broker_mid = float(book["mid"])
    spread_pct = float(book["spread_pct"])
    hypothetical_fill = broker_ask if side == "BUY" else broker_bid
    paper_fill = _finite(paper_fill_price) if paper_fill_price is not None else None
    if paper_fill is not None and paper_fill <= 0:
        paper_fill = None
    notional = quantity * hypothetical_fill
    now = utc_now()
    shadow_id = f"shadow:{uuid.uuid4()}"
    record = ShadowBrokerRecord(
        shadow_order_id=shadow_id,
        paper_fill_id=str(paper_fill_id) if paper_fill_id else None,
        decision_id=str(decision_id) if decision_id else None,
        proposal_id=str(proposal_id) if proposal_id else None,
        symbol=symbol,
        market=str(market or "crypto").lower(),
        side=side,
        quantity=quantity,
        notional=notional,
        oracle_reference_price=reference,
        paper_fill_price=paper_fill,
        broker_bid=broker_bid,
        broker_ask=broker_ask,
        broker_mid=broker_mid,
        broker_spread_pct=spread_pct,
        hypothetical_fill_price=hypothetical_fill,
        created_at=now,
    )
    paper_error = None
    if paper_fill is not None and hypothetical_fill > 0:
        paper_error = abs(paper_fill - hypothetical_fill) / hypothetical_fill * 100.0
    with connect() as conn:
        conn.execute(
            """
            INSERT INTO shadow_broker_orders (
                shadow_order_id, paper_fill_id, decision_id, proposal_id, symbol, market, side,
                quantity, notional, oracle_reference_price, paper_fill_price,
                broker_bid, broker_ask, broker_mid, broker_spread_pct,
                broker_quote_at, hypothetical_fill_price, paper_vs_broker_error_pct,
                status, payload, created_at
            ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,'OPEN',%s::jsonb,%s)
            """,
            (
                record.shadow_order_id,
                record.paper_fill_id,
                record.decision_id,
                record.proposal_id,
                record.symbol,
                record.market,
                record.side,
                record.quantity,
                record.notional,
                record.oracle_reference_price,
                record.paper_fill_price,
                record.broker_bid,
                record.broker_ask,
                record.broker_mid,
                record.broker_spread_pct,
                str(broker_quote.get("timestamp") or broker_quote.get("updated_at") or now),
                record.hypothetical_fill_price,
                paper_error,
                json.dumps(payload or {}, sort_keys=True, default=str),
                now,
            ),
        )
    return record


def evaluate_shadow_order(shadow_order_id: str, followup_price: float, *, payload: dict[str, Any] | None = None) -> dict[str, Any]:
    followup = _finite(followup_price)
    if followup <= 0:
        raise ValueError("shadow follow-up price must be positive")
    with connect() as conn:
        current = conn.execute(
            "SELECT * FROM shadow_broker_orders WHERE shadow_order_id=%s FOR UPDATE",
            (shadow_order_id,),
        ).fetchone()
        if not current:
            raise KeyError(f"unknown shadow order: {shadow_order_id}")
        fill = _finite(current.get("hypothetical_fill_price"))
        if fill <= 0:
            raise RuntimeError("shadow order has no valid hypothetical broker fill")
        side = str(current.get("side") or "").upper()
        direction = 1.0 if side == "BUY" else -1.0
        outcome = direction * ((followup / fill) - 1.0) * 100.0
        merged = dict(current.get("payload") or {})
        if isinstance(payload, dict):
            merged.update(payload)
        updated = conn.execute(
            """
            UPDATE shadow_broker_orders
            SET followup_price=%s, outcome_return_pct=%s, status='EVALUATED',
                payload=%s::jsonb, evaluated_at=%s
            WHERE shadow_order_id=%s
            RETURNING *
            """,
            (followup, outcome, json.dumps(merged, sort_keys=True, default=str), utc_now(), shadow_order_id),
        ).fetchone()
        return dict(updated)


def shadow_readiness_summary(*, minimum_samples: int = 100, maximum_paper_error_pct: float = 1.0) -> dict[str, Any]:
    try:
        stats = row(
            """
            SELECT
                COUNT(*) FILTER (WHERE status='EVALUATED')::int AS evaluated,
                COUNT(*) FILTER (WHERE status='OPEN')::int AS open_count,
                AVG(outcome_return_pct) FILTER (WHERE status='EVALUATED') AS avg_outcome,
                AVG(paper_vs_broker_error_pct) FILTER (WHERE paper_vs_broker_error_pct IS NOT NULL) AS avg_paper_error,
                PERCENTILE_CONT(0.95) WITHIN GROUP (ORDER BY paper_vs_broker_error_pct)
                    FILTER (WHERE paper_vs_broker_error_pct IS NOT NULL) AS p95_paper_error
            FROM shadow_broker_orders
            WHERE payload->>'evidence_kind'='passive_paper_execution_model'
            """
        ) or {}
        evaluated = int(stats.get("evaluated") or 0)
        p95_error = stats.get("p95_paper_error")
        p95_ok = p95_error is not None and float(p95_error) <= float(maximum_paper_error_pct)
        return {
            "ok": evaluated >= int(minimum_samples) and p95_ok,
            "evaluated_samples": evaluated,
            "open_samples": int(stats.get("open_count") or 0),
            "minimum_samples": int(minimum_samples),
            "average_outcome_return_pct": _finite(stats.get("avg_outcome")),
            "average_paper_vs_broker_error_pct": None if stats.get("avg_paper_error") is None else _finite(stats.get("avg_paper_error")),
            "p95_paper_vs_broker_error_pct": None if p95_error is None else _finite(p95_error),
            "maximum_paper_error_pct": float(maximum_paper_error_pct),
            "status": "PASS" if evaluated >= int(minimum_samples) and p95_ok else "INSUFFICIENT_FORWARD_EVIDENCE",
        }
    except Exception as exc:
        return {
            "ok": False,
            "evaluated_samples": 0,
            "minimum_samples": int(minimum_samples),
            "status": "UNAVAILABLE",
            "reason": exc.__class__.__name__,
        }
