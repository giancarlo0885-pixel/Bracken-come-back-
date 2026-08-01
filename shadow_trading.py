from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Any

from order_proposals import OrderProposal


@dataclass
class ShadowFill:
    order_id: str
    symbol: str
    side: str
    requested_quantity: float
    filled_quantity: float
    fill_price: float
    spread: float
    slippage: float
    fees: float
    status: str
    created_at: str


def simulate_shadow_order(proposal: OrderProposal, *, participation: float = 1.0) -> ShadowFill:
    quote_price = float(proposal.verified_quote.get("price") or proposal.limit_price or 0.0)
    spread = max(0.0, float(proposal.estimated_spread))
    slippage = max(0.0, float(proposal.expected_slippage))
    fill_ratio = max(0.0, min(1.0, participation))
    filled = proposal.quantity * fill_ratio
    direction = 1 if proposal.side.upper() == "BUY" else -1
    fill_price = quote_price * (1 + direction * (spread / 2.0 + slippage))
    status = "filled" if fill_ratio >= 0.999 else "partially-filled" if fill_ratio > 0 else "expired"
    return ShadowFill(
        order_id=proposal.idempotency_key,
        symbol=proposal.symbol,
        side=proposal.side,
        requested_quantity=proposal.quantity,
        filled_quantity=filled,
        fill_price=fill_price,
        spread=spread,
        slippage=slippage,
        fees=float(proposal.estimated_fees),
        status=status,
        created_at=datetime.now(timezone.utc).isoformat(),
    )


def compare_shadow_to_recommendation(recommendation: dict[str, Any], fill: ShadowFill, benchmark_return_pct: float = 0.0) -> dict[str, Any]:
    expected = float(recommendation.get("expected_return") or 0.0)
    return {
        "symbol": fill.symbol,
        "advisor_expected_return_pct": expected,
        "shadow_status": fill.status,
        "shadow_fill_price": fill.fill_price,
        "benchmark_return_pct": float(benchmark_return_pct),
        "fill": asdict(fill),
    }
