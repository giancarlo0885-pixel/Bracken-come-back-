from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
import hashlib
from typing import Any

from provider_router import normalize_symbol


PROPOSAL_STATUSES = {
    "proposed",
    "approved",
    "rejected",
    "expired",
    "shadow-submitted",
    "paper-submitted",
    "partially-filled",
    "filled",
    "cancelled",
    "failed",
}


@dataclass
class OrderProposal:
    idempotency_key: str
    symbol: str
    side: str
    quantity: float
    order_type: str
    limit_price: float | None
    verified_quote: dict[str, Any]
    estimated_spread: float
    expected_slippage: float
    estimated_fees: float
    strategy: str
    recommendation_id: str
    risk_checks: list[dict[str, Any]]
    approval_status: str
    expiration: str
    created_at: str
    rationale: str = ""
    events: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _key(symbol: str, side: str, recommendation_id: str, strategy: str) -> str:
    payload = "|".join([normalize_symbol(symbol), side.upper(), str(recommendation_id), str(strategy)])
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:32]


def create_order_proposal(
    *,
    symbol: str,
    side: str,
    quantity: float,
    verified_quote: dict[str, Any],
    strategy: str,
    recommendation_id: str,
    risk_checks: list[dict[str, Any]],
    order_type: str = "limit",
    limit_price: float | None = None,
    estimated_spread: float = 0.0,
    expected_slippage: float = 0.0,
    estimated_fees: float = 0.0,
    ttl_minutes: int = 30,
    rationale: str = "",
) -> OrderProposal:
    now = datetime.now(timezone.utc)
    proposal = OrderProposal(
        idempotency_key=_key(symbol, side, recommendation_id, strategy),
        symbol=normalize_symbol(symbol),
        side=side.upper(),
        quantity=float(quantity),
        order_type=order_type,
        limit_price=limit_price,
        verified_quote=dict(verified_quote),
        estimated_spread=float(estimated_spread),
        expected_slippage=float(expected_slippage),
        estimated_fees=float(estimated_fees),
        strategy=strategy,
        recommendation_id=str(recommendation_id),
        risk_checks=list(risk_checks),
        approval_status="proposed",
        expiration=(now + timedelta(minutes=max(1, ttl_minutes))).isoformat(),
        created_at=now.isoformat(),
        rationale=rationale,
    )
    proposal.events.append({"status": "proposed", "created_at": proposal.created_at})
    return proposal


def approve_proposal(proposal: OrderProposal) -> OrderProposal:
    proposal.approval_status = "approved"
    proposal.events.append({"status": "approved", "created_at": datetime.now(timezone.utc).isoformat()})
    return proposal


def reject_proposal(proposal: OrderProposal, reason: str) -> OrderProposal:
    proposal.approval_status = "rejected"
    proposal.events.append({"status": "rejected", "reason": reason, "created_at": datetime.now(timezone.utc).isoformat()})
    return proposal
