from __future__ import annotations

from typing import Any

from database import rows


PENDING_STATUS = "AWAITING_HUMAN_APPROVAL"


def _payload(record: dict[str, Any]) -> dict[str, Any]:
    value = record.get("payload")
    return dict(value) if isinstance(value, dict) else {}


def list_trade_proposals(*, limit: int = 100, pending_only: bool = True) -> list[dict[str, Any]]:
    """Return broker-verified crypto proposals without performing broker mutations."""
    records = rows(
        """
        SELECT proposal_id, shadow_order_id, paper_fill_id, decision_id, symbol, side,
               quantity, notional, oracle_reference_price, paper_fill_price,
               broker_bid, broker_ask, broker_mid, broker_spread_pct,
               hypothetical_fill_price, status AS shadow_status, payload, created_at
        FROM shadow_broker_orders
        WHERE proposal_id IS NOT NULL
        ORDER BY created_at DESC
        LIMIT %s
        """,
        (max(1, min(500, int(limit))),),
    )
    proposals: list[dict[str, Any]] = []
    for record in records:
        payload = _payload(record)
        proposal_status = str(payload.get("proposal_status") or PENDING_STATUS)
        if pending_only and proposal_status != PENDING_STATUS:
            continue
        proposals.append(
            {
                "proposal_id": record.get("proposal_id"),
                "created_at": record.get("created_at"),
                "symbol": record.get("symbol"),
                "side": record.get("side"),
                "quantity": record.get("quantity"),
                "notional": record.get("notional"),
                "broker_executable_price": record.get("hypothetical_fill_price"),
                "broker_bid": record.get("broker_bid"),
                "broker_ask": record.get("broker_ask"),
                "broker_spread_pct": record.get("broker_spread_pct"),
                "oracle_reference_price": record.get("oracle_reference_price"),
                "paper_fill_price": record.get("paper_fill_price"),
                "proposal_status": proposal_status,
                "human_approval_required": bool(payload.get("human_approval_required", True)),
                "submission_allowed": bool(payload.get("submission_allowed", False)),
                "strategy": payload.get("strategy"),
                "reason": payload.get("reason") or payload.get("paper_order_reason"),
                "score": payload.get("score"),
                "confidence": payload.get("confidence"),
                "risk_reward_ratio": payload.get("risk_reward_ratio"),
                "target_price": payload.get("target_price"),
                "stop_loss": payload.get("stop_loss"),
                "mean_reversion_zscore": payload.get("mean_reversion_zscore"),
                "short_horizon_return": payload.get("short_horizon_return"),
                "regime": payload.get("regime"),
                "broker_estimated_price": payload.get("broker_estimated_price"),
                "paper_fee_pct": payload.get("paper_fee_pct"),
                "paper_slippage_pct": payload.get("paper_slippage_pct"),
                "paper_market_impact_pct": payload.get("paper_market_impact_pct"),
                "shadow_status": record.get("shadow_status"),
                "paper_fill_id": record.get("paper_fill_id"),
                "shadow_order_id": record.get("shadow_order_id"),
                "decision_id": record.get("decision_id"),
            }
        )
    return proposals


def pending_trade_proposal_count() -> int:
    return len(list_trade_proposals(limit=500, pending_only=True))
