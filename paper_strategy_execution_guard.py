from __future__ import annotations

import copy
from datetime import datetime, timezone
import logging
import os
from typing import Any

import paper_model_validation_gate as regime_gate
import paper_strategy_economics as economics


log = logging.getLogger("paper-strategy-execution-guard")
_INSTALLED = False


def _value(signal: Any, name: str, default: Any = None) -> Any:
    if isinstance(signal, dict):
        return signal.get(name, default)
    return getattr(signal, name, default)


def _number(value: Any, default: float = 0.0) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return default
    return parsed if parsed == parsed else default


def _parse_time(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except Exception:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _open_position_accumulation_allows(symbol: str) -> tuple[bool, str]:
    """Rate-limit repeated paper adds while a same-symbol position is already open.

    The optimizer amount is an executable allocation, not a durable target. Without
    a final cadence guard, a persistent BUY idea can therefore spend that allocation
    again on every worker cycle. This check is deliberately downstream of every
    optimizer/core-rebalance path and reads persisted position/trade state. It does
    not modify historical records, strategy attribution, or any live-money control.
    """
    interval = max(
        0.0,
        min(
            120.0,
            _number(os.getenv("PAPER_CRYPTO_OPEN_POSITION_ACCUMULATION_COOLDOWN_MINUTES", "5"), 5.0),
        ),
    )
    if interval <= 0:
        return True, "open_position_accumulation_cooldown_disabled"

    try:
        import oracle_bot

        position = oracle_bot.row(
            "SELECT symbol FROM positions WHERE market='crypto' AND symbol=%s LIMIT 1",
            (str(symbol or "").upper(),),
        )
        if not position:
            return True, "no_open_position"
        latest_buy = oracle_bot.row(
            """
            SELECT created_at
            FROM trades
            WHERE market='crypto' AND symbol=%s AND side='BUY'
            ORDER BY created_at DESC
            LIMIT 1
            """,
            (str(symbol or "").upper(),),
        ) or {}
    except Exception as exc:
        log.warning(
            "PAPER OPEN POSITION ACCUMULATION | symbol=%s | state=UNAVAILABLE | reason=%s | action=ALLOW_EXISTING_GUARDS",
            str(symbol or "").upper(),
            exc.__class__.__name__,
        )
        return True, "open_position_state_unavailable"

    last_buy = _parse_time(latest_buy.get("created_at"))
    if last_buy is None:
        return True, "open_position_buy_timestamp_unavailable"

    age_minutes = max(0.0, (datetime.now(timezone.utc) - last_buy).total_seconds() / 60.0)
    if age_minutes < interval:
        return False, f"open_position_accumulation_cooldown:{age_minutes:.2f}/{interval:.2f}m"
    return True, f"open_position_accumulation_cooldown_elapsed:{age_minutes:.2f}/{interval:.2f}m"


def _with_optimizer_target(signal: Any, target: float) -> Any:
    """Return a shallow signal copy with the paper optimizer target adjusted.

    The source signal is never mutated, preserving decision/audit provenance.
    """
    if isinstance(signal, dict):
        clone = dict(signal)
        if "v39_optimizer_approved_amount" in clone:
            clone["v39_optimizer_approved_amount"] = target
        allocation = clone.get("v39_optimizer_allocation")
        if isinstance(allocation, dict):
            copied = dict(allocation)
            copied["amount"] = min(float(copied.get("amount") or target), target)
            clone["v39_optimizer_allocation"] = copied
        return clone

    try:
        clone = copy.copy(signal)
        if hasattr(clone, "v39_optimizer_approved_amount"):
            setattr(clone, "v39_optimizer_approved_amount", target)
        allocation = getattr(clone, "v39_optimizer_allocation", None)
        if isinstance(allocation, dict):
            copied = dict(allocation)
            copied["amount"] = min(float(copied.get("amount") or target), target)
            setattr(clone, "v39_optimizer_allocation", copied)
        return clone
    except Exception:
        return signal


def install_paper_strategy_execution_guard() -> bool:
    """Install paper-only fee-aware entry filtering and adaptive strategy sizing.

    This wrapper is intentionally installed after the optimizer-size handoff so it
    governs the final BUY/ACCUMULATE entry path. It never changes SELL/EXIT/CLOSE,
    quote integrity, accounting, or live-capital controls.
    """
    global _INSTALLED
    if _INSTALLED:
        return True
    if not economics.active():
        return False

    import oracle_bot

    original_buy = oracle_bot._buy

    def guarded_buy(
        market: str,
        symbol: str,
        price: float,
        signal: Any,
        quant_assessment: Any | None = None,
        target_trade_value: float | None = None,
        rotation_candidate: dict[str, Any] | None = None,
        verified_quote: dict[str, Any] | None = None,
        rotation_verified_quote: dict[str, Any] | None = None,
    ):
        if not economics.active() or str(market or "").strip().lower() != "crypto":
            return original_buy(
                market, symbol, price, signal,
                quant_assessment=quant_assessment,
                target_trade_value=target_trade_value,
                rotation_candidate=rotation_candidate,
                verified_quote=verified_quote,
                rotation_verified_quote=rotation_verified_quote,
            )

        accumulation_allowed, accumulation_reason = _open_position_accumulation_allows(symbol)
        if not accumulation_allowed:
            log.info(
                "PAPER OPEN POSITION ACCUMULATION | symbol=%s | action=%s | allowed=False | reason=%s | "
                "mode=paper | broker_submission=NONE | live_trading=DISARMED",
                str(symbol or "").upper(),
                str(_value(signal, "action", "BUY") or "BUY").upper(),
                accumulation_reason,
            )
            return False

        allowed, edge_reason, edge, cost = economics.fee_edge_allows_entry(signal)
        if not allowed:
            log.info(
                "PAPER FEE AWARE ENTRY | symbol=%s | allowed=False | expected_edge_pct=%s | "
                "estimated_round_trip_cost_pct=%.4f | reason=%s | mode=paper | broker_submission=NONE | live_trading=DISARMED",
                str(symbol or "").upper(),
                "unknown" if edge is None else f"{edge:.4f}",
                cost,
                edge_reason,
            )
            return False

        optimizer_target = float(_value(signal, "v39_optimizer_approved_amount", 0.0) or 0.0)
        base_target = optimizer_target if optimizer_target > 0 else float(target_trade_value or 0.0)
        adjusted_signal = signal
        adjusted_target = target_trade_value
        if base_target > 0:
            sized, scorecard, size_reason = economics.adjusted_optimizer_target(signal, base_target)
            regime_ok, regime_reason = regime_gate.regime_validation_ok(signal)
            if sized > base_target and not regime_ok:
                sized = base_target
                size_reason = f"positive_boost_withheld:{regime_reason}"
            economics.log_economics(
                scorecard,
                symbol=symbol,
                original_target=base_target,
                adjusted_target=sized,
                reason=size_reason,
            )
            if sized <= 0:
                return False
            if optimizer_target > 0:
                adjusted_signal = _with_optimizer_target(signal, sized)
            else:
                adjusted_target = sized

        log.info(
            "PAPER FEE AWARE ENTRY | symbol=%s | allowed=True | expected_edge_pct=%s | estimated_round_trip_cost_pct=%.4f | "
            "reason=%s | accumulation_reason=%s | mode=paper | broker_submission=NONE | live_trading=DISARMED",
            str(symbol or "").upper(),
            "unknown" if edge is None else f"{edge:.4f}",
            cost,
            edge_reason,
            accumulation_reason,
        )
        return original_buy(
            market, symbol, price, adjusted_signal,
            quant_assessment=quant_assessment,
            target_trade_value=adjusted_target,
            rotation_candidate=rotation_candidate,
            verified_quote=verified_quote,
            rotation_verified_quote=rotation_verified_quote,
        )

    oracle_bot._buy = guarded_buy
    _INSTALLED = True
    accumulation_interval = max(
        0.0,
        min(120.0, _number(os.getenv("PAPER_CRYPTO_OPEN_POSITION_ACCUMULATION_COOLDOWN_MINUTES", "5"), 5.0)),
    )
    log.info(
        "PAPER STRATEGY EXECUTION GUARD | active=True | fee_aware_entry=ENFORCED_WHEN_EDGE_AVAILABLE | "
        "adaptive_strategy_sizing=ENABLED | model_regime_boost_gate=ENABLED | open_position_accumulation_cooldown=%.2fm | "
        "exploration_floor=PRESERVED | final_churn_and_capacity_guards=DOWNSTREAM | broker_submission=NONE | live_trading=DISARMED",
        accumulation_interval,
    )
    return True
