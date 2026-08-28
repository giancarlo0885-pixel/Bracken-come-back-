from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import hashlib
import json
import math
import uuid
from typing import Any, Protocol

from config import (
    BROKER_MODE,
    ENABLE_BROKER_SUBMISSION,
    LIVE_MAX_DAILY_LOSS_DOLLARS,
    LIVE_MAX_DAILY_NEW_EXPOSURE_DOLLARS,
    LIVE_MAX_POSITION_PCT,
    LIVE_MAX_SINGLE_ORDER_DOLLARS,
    LIVE_MAX_TOTAL_DEPLOYED_PCT,
    LIVE_TRADING_KILL_SWITCH,
)
from market_sessions import quote_is_fresh
from provider_router import normalize_symbol


LIVE_BROKER_MODES = {"paper", "live_read_only", "live_preview", "live_manual_approval"}


@dataclass(frozen=True)
class LiveOrderProposal:
    proposal_id: str
    symbol: str
    market: str
    side: str
    quantity: float
    estimated_notional: float
    reference_price: float
    stop_loss: float | None
    take_profit: float | None
    strategy: str
    tier: str | None
    confidence: float | None
    reward_risk_ratio: float | None
    reason: str
    quote: dict[str, Any]
    risk_checks: list[dict[str, Any]]
    broker_mode: str
    account_environment: str
    created_at: str
    status: str = "PROPOSED"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ManualApproval:
    approval_id: str
    proposal_id: str
    symbol: str
    side: str
    quantity: float
    reference_price: float
    maximum_notional: float
    approval_timestamp: str
    payload_hash: str
    status: str = "APPROVED"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class BrokerageAdapter(Protocol):
    def get_account(self) -> dict[str, Any]: ...
    def get_positions(self) -> list[dict[str, Any]]: ...
    def get_orders(self) -> list[dict[str, Any]]: ...
    def get_fills(self) -> list[dict[str, Any]]: ...
    def preview_order(self, proposal: LiveOrderProposal) -> dict[str, Any]: ...
    def submit_order(self, proposal: LiveOrderProposal) -> dict[str, Any]: ...


def _finite(value: Any, default: float = 0.0) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return number if math.isfinite(number) else default


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def proposal_payload_hash(proposal: LiveOrderProposal | dict[str, Any]) -> str:
    payload = proposal.to_dict() if isinstance(proposal, LiveOrderProposal) else dict(proposal)
    payload.pop("status", None)
    payload.pop("created_at", None)
    encoded = json.dumps(payload, sort_keys=True, default=str, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def verified_quote_for_proposal(symbol: str, quote: dict[str, Any]) -> tuple[bool, str]:
    requested = normalize_symbol(symbol)
    price = _finite(quote.get("price"))
    if price <= 0:
        return False, "proposal requires a finite positive reference price"
    if quote.get("quote_verified") is not True:
        return False, "proposal requires a verified quote"
    if normalize_symbol(quote.get("symbol")) != requested:
        return False, "proposal quote symbol mismatch"
    if normalize_symbol(quote.get("requested_symbol")) != requested:
        return False, "proposal requested symbol mismatch"
    if normalize_symbol(quote.get("provider_symbol")) != requested:
        return False, "proposal provider symbol mismatch"
    timestamp = quote.get("quote_timestamp")
    if not timestamp:
        return False, "proposal quote timestamp missing"
    if not quote_is_fresh(
        timestamp,
        str(quote.get("interval") or quote.get("source_interval") or "1d"),
        exchange=quote.get("exchange") or "",
        region=quote.get("region") or quote.get("country") or "",
        symbol=requested,
    ):
        return False, "proposal quote is stale"
    return True, "verified"


def validate_live_order_proposal(proposal: LiveOrderProposal, account: dict[str, Any]) -> tuple[bool, str]:
    if proposal.broker_mode not in LIVE_BROKER_MODES:
        return False, "unsupported broker mode"
    quote_ok, quote_reason = verified_quote_for_proposal(proposal.symbol, proposal.quote)
    if not quote_ok:
        return False, quote_reason
    if any(check.get("passed") is not True for check in proposal.risk_checks):
        return False, "proposal risk checks failed"
    if proposal.estimated_notional <= 0 or proposal.quantity <= 0:
        return False, "proposal requires positive quantity and notional"
    if proposal.estimated_notional > LIVE_MAX_SINGLE_ORDER_DOLLARS:
        return False, "proposal exceeds single live order limit"
    if _finite(account.get("daily_new_exposure")) + proposal.estimated_notional > LIVE_MAX_DAILY_NEW_EXPOSURE_DOLLARS:
        return False, "proposal exceeds daily new exposure limit"
    if abs(_finite(account.get("daily_realized_pnl"))) > LIVE_MAX_DAILY_LOSS_DOLLARS and _finite(account.get("daily_realized_pnl")) < 0:
        return False, "daily live loss limit reached"
    equity = _finite(account.get("equity"))
    if equity <= 0:
        return False, "live account equity must be reconciled before proposals"
    symbol_exposure = _finite(account.get("symbol_exposure", {}).get(normalize_symbol(proposal.symbol)) if isinstance(account.get("symbol_exposure"), dict) else 0.0)
    deployed = _finite(account.get("deployed_value"))
    if (symbol_exposure + proposal.estimated_notional) / equity > LIVE_MAX_POSITION_PCT:
        return False, "proposal exceeds live position concentration limit"
    if (deployed + proposal.estimated_notional) / equity > LIVE_MAX_TOTAL_DEPLOYED_PCT:
        return False, "proposal exceeds live total deployed limit"
    if account.get("reconciled") is not True:
        return False, "live account reconciliation required"
    return True, "proposal approved for current mode"


def create_live_order_proposal(
    *,
    symbol: str,
    market: str,
    side: str,
    quantity: float,
    quote: dict[str, Any],
    strategy: str,
    risk_checks: list[dict[str, Any]],
    broker_mode: str | None = None,
    tier: str | None = None,
    confidence: float | None = None,
    reward_risk_ratio: float | None = None,
    stop_loss: float | None = None,
    take_profit: float | None = None,
    reason: str = "",
) -> LiveOrderProposal:
    symbol = normalize_symbol(symbol)
    side = str(side or "").upper()
    if side not in {"BUY", "SELL"}:
        raise ValueError("live proposal side must be BUY or SELL")
    qty = _finite(quantity)
    price = _finite(quote.get("price"))
    if qty <= 0 or price <= 0:
        raise ValueError("live proposal requires positive quantity and quote price")
    return LiveOrderProposal(
        proposal_id=f"live-proposal:{uuid.uuid4()}",
        symbol=symbol,
        market=str(market or "cash").lower(),
        side=side,
        quantity=qty,
        estimated_notional=round(qty * price, 10),
        reference_price=price,
        stop_loss=stop_loss,
        take_profit=take_profit,
        strategy=strategy,
        tier=tier,
        confidence=confidence,
        reward_risk_ratio=reward_risk_ratio,
        reason=reason,
        quote=dict(quote),
        risk_checks=list(risk_checks),
        broker_mode=broker_mode or BROKER_MODE,
        account_environment="LIVE" if str(broker_mode or BROKER_MODE).startswith("live_") else "PAPER",
        created_at=_utc_now(),
    )


def approve_live_order_proposal(proposal: LiveOrderProposal, *, maximum_notional: float | None = None) -> ManualApproval:
    return ManualApproval(
        approval_id=f"live-approval:{uuid.uuid4()}",
        proposal_id=proposal.proposal_id,
        symbol=proposal.symbol,
        side=proposal.side,
        quantity=proposal.quantity,
        reference_price=proposal.reference_price,
        maximum_notional=_finite(maximum_notional, proposal.estimated_notional),
        approval_timestamp=_utc_now(),
        payload_hash=proposal_payload_hash(proposal),
    )


def approval_matches_proposal(proposal: LiveOrderProposal, approval: ManualApproval | None) -> bool:
    return bool(
        approval
        and approval.status == "APPROVED"
        and approval.proposal_id == proposal.proposal_id
        and approval.symbol == proposal.symbol
        and approval.side == proposal.side
        and approval.quantity == proposal.quantity
        and approval.maximum_notional >= proposal.estimated_notional
        and approval.payload_hash == proposal_payload_hash(proposal)
    )


def submit_live_order(
    adapter: BrokerageAdapter,
    proposal: LiveOrderProposal,
    *,
    account: dict[str, Any],
    approval: ManualApproval | None = None,
) -> dict[str, Any]:
    if LIVE_TRADING_KILL_SWITCH:
        return {"submitted": False, "status": "blocked", "reason": "live trading kill switch is active"}
    if not ENABLE_BROKER_SUBMISSION:
        return {"submitted": False, "status": "blocked", "reason": "broker submission switch is disabled"}
    valid, reason = validate_live_order_proposal(proposal, account)
    if not valid:
        return {"submitted": False, "status": "blocked", "reason": reason}
    if proposal.broker_mode in {"paper", "live_read_only", "live_preview"}:
        preview = adapter.preview_order(proposal)
        return {"submitted": False, "status": "preview", "reason": "live submission unavailable in this mode", "preview": preview}
    if proposal.broker_mode != "live_manual_approval":
        return {"submitted": False, "status": "blocked", "reason": "unsupported broker mode"}
    if not approval_matches_proposal(proposal, approval):
        return {"submitted": False, "status": "blocked", "reason": "manual approval does not match immutable proposal"}
    return adapter.submit_order(proposal)


def execution_from_broker_fill(fill: dict[str, Any], proposal: LiveOrderProposal) -> dict[str, Any]:
    fill_price = _finite(fill.get("fill_price") or fill.get("price"))
    if fill_price <= 0:
        raise ValueError("broker fill requires a positive fill price")
    quantity = _finite(fill.get("quantity"), proposal.quantity)
    return {
        "execution_id": str(fill.get("execution_id") or fill.get("id") or f"exec:{uuid.uuid4()}"),
        "order_id": str(fill.get("order_id") or proposal.proposal_id),
        "symbol": proposal.symbol,
        "market": proposal.market,
        "side": proposal.side,
        "quantity": quantity,
        "fill_price": fill_price,
        "fees": max(0.0, _finite(fill.get("fees"))),
        "executed_at": str(fill.get("executed_at") or _utc_now()),
        "broker_mode": proposal.broker_mode,
        "account_environment": proposal.account_environment,
    }
