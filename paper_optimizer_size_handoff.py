from __future__ import annotations

from contextvars import ContextVar
from dataclasses import replace
import logging
import math
import os
from typing import Any

import paper_crypto_churn_guard as churn_guard
import runtime_integrity_patch as patch


log = logging.getLogger("paper-optimizer-size-handoff")
_INSTALLED = False
_OPTIMIZER_TARGET = ContextVar("paper_optimizer_target", default=0.0)
_OPTIMIZER_DOLLAR_VOLUME = ContextVar("paper_optimizer_dollar_volume", default=0.0)


class _OptimizerSizingAssessment:
    """Delegate quant evidence while neutralizing duplicate soft size scaling."""

    def __init__(self, base: Any | None, position_multiplier: float) -> None:
        self._base = base
        self.position_multiplier = position_multiplier
        base_reason = str(getattr(base, "reason", "") or "") if base is not None else ""
        self.reason = (
            f"{base_reason}; paper optimizer approved-size handoff"
            if base_reason
            else "paper optimizer approved-size handoff"
        )

    def __getattr__(self, name: str) -> Any:
        if self._base is None:
            raise AttributeError(name)
        return getattr(self._base, name)


def _truthy(name: str, default: str = "false") -> bool:
    return str(os.getenv(name, default) or default).strip().lower() in {"1", "true", "yes", "on"}


def _active() -> bool:
    return bool(
        str(os.getenv("EXECUTION_MODE", "paper") or "paper").strip().lower() == "paper"
        and _truthy("PAPER_AUTONOMOUS_LEARNING")
        and _truthy("PAPER_UNBOUNDED_LEARNING")
        and not _truthy("ENABLE_BROKER_SUBMISSION")
        and not _truthy("LIVE_TRADING_ARMED")
    )


def _number(value: Any, default: float = 0.0) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return number if math.isfinite(number) else default


def _risk_limited_spendable(
    *,
    cash: float,
    buying_power: float,
    equity: float,
    current_exposure: float,
    leverage_limit: float,
    margin_utilization_pct: float,
) -> tuple[float, float]:
    """Return executable paper BUY capacity using the same gross-risk ceiling as _buy."""
    cash = max(0.0, _number(cash))
    buying_power = max(0.0, _number(buying_power))
    equity = max(0.0, _number(equity))
    current_exposure = max(0.0, _number(current_exposure))
    leverage_limit = max(1.0, _number(leverage_limit, 1.0))
    margin_utilization_pct = max(0.0, _number(margin_utilization_pct, 1.0))

    maximum_gross = equity * leverage_limit * margin_utilization_pct
    gross_room = max(0.0, maximum_gross - current_exposure)
    spendable = min(cash, buying_power, gross_room)
    return max(0.0, spendable), gross_room


def _optimizer_target(signal: Any) -> tuple[float, float]:
    approved = max(0.0, _number(patch._signal_value(signal, "v39_optimizer_approved_amount", 0.0)))
    allocation = patch._signal_value(signal, "v39_optimizer_allocation", {}) or {}
    if approved <= 0 or not isinstance(allocation, dict) or not allocation:
        return 0.0, 0.0

    allocation_amount = max(0.0, _number(allocation.get("amount"), approved))
    if allocation_amount > 0:
        approved = min(approved, allocation_amount)

    liquidity = allocation.get("liquidity") if isinstance(allocation.get("liquidity"), dict) else {}
    dollar_volume = max(0.0, _number(liquidity.get("average_dollar_volume")))
    return approved, dollar_volume


def _is_configured_core(signal: Any) -> bool:
    intent = patch._core_rebalance_intent(signal)
    source = str(patch._signal_value(signal, "core_rebalance_source", "") or "")
    return bool(
        intent == patch.CORE_REBALANCE_BUY_INTENT
        and source == "configured_core_allocation_gap"
    )


def install_paper_optimizer_size_handoff() -> bool:
    """Carry V39-approved paper notional through the final BUY sizing path.

    The optimizer has already ranked the opportunity and allocated simulated
    capital. In unbounded paper-learning mode this layer prevents the downstream
    minimum-sample wrapper from collapsing a valid optimizer allocation to $2.
    It remains fail-closed for live trading and still clips to simulated cash,
    validated buying power, remaining gross-exposure capacity, a positive
    current price, verified liquidity participation capacity, and the final
    same-symbol churn cooldown. Quote and execution integrity remain downstream.
    """
    global _INSTALLED
    if _INSTALLED:
        return True
    if not _active():
        return False

    import capital_allocator
    import oracle_bot

    # The generic execution function historically classified any asset priced in
    # the penny-stock dollar range as a penny stock, regardless of market. That
    # incorrectly clipped low-priced crypto (for example DOT/NEAR) to the stock
    # 1% penny position size even when V39 approved the normal 8% crypto target.
    # This module exists only in the isolated crypto paper worker, so neutralize
    # those stock-only caps here without changing the stock-worker process.
    oracle_bot.PENNY_STOCK_MAX_TRADE_VALUE_PCT = max(
        _number(getattr(oracle_bot, "PENNY_STOCK_MAX_TRADE_VALUE_PCT", 0.0)),
        _number(getattr(oracle_bot, "MAX_TRADE_VALUE_PCT", 0.0)),
    )
    oracle_bot.PENNY_STOCK_MAX_PORTFOLIO_PCT = 1.0

    original_adaptive = oracle_bot.adaptive_capital_allocation
    original_buy = oracle_bot._buy

    def optimizer_target_allocation(**kwargs: Any):
        decision = original_adaptive(**kwargs)
        target = max(0.0, _number(_OPTIMIZER_TARGET.get()))
        if (
            not _active()
            or str(kwargs.get("market") or "").strip().lower() != "crypto"
            or target <= 0
        ):
            return decision

        price = max(0.0, _number(kwargs.get("price")))
        cash = max(0.0, _number(kwargs.get("cash")))
        if price <= 0 or cash <= 0:
            return replace(
                decision,
                calculated_notional=0.0,
                calculated_quantity=0.0,
                approved=False,
                reason="PAPER_OPTIMIZER_TARGET_INVALID_CAPACITY",
            )

        buying_power = cash
        if bool(kwargs.get("buying_power_validated")) and kwargs.get("buying_power") is not None:
            buying_power = max(0.0, _number(kwargs.get("buying_power")))

        spendable, gross_room = _risk_limited_spendable(
            cash=cash,
            buying_power=buying_power,
            equity=max(0.0, _number(kwargs.get("equity"))),
            current_exposure=max(0.0, _number(kwargs.get("current_exposure"))),
            leverage_limit=max(
                1.0,
                _number(oracle_bot.market_leverage_limit(str(kwargs.get("market") or "crypto")), 1.0),
            ),
            margin_utilization_pct=max(
                0.0,
                _number(getattr(oracle_bot, "PAPER_MAX_MARGIN_UTILIZATION_PCT", 1.0), 1.0),
            ),
        )

        dollar_volume = max(
            0.0,
            _number(kwargs.get("dollar_volume")),
            _number(_OPTIMIZER_DOLLAR_VOLUME.get()),
        )
        participation_pct = max(
            0.0,
            _number(getattr(capital_allocator, "MAX_POSITION_VS_DAILY_DOLLAR_VOLUME_PCT", 0.0)),
        )
        liquidity_room = dollar_volume * participation_pct
        if dollar_volume <= 0 or participation_pct <= 0 or liquidity_room <= 0:
            return replace(
                decision,
                calculated_notional=0.0,
                calculated_quantity=0.0,
                approved=False,
                reason="PAPER_OPTIMIZER_TARGET_MISSING_LIQUIDITY_CAPACITY",
            )

        notional = min(target, spendable, liquidity_room)
        minimum = max(0.0, _number(getattr(capital_allocator, "MIN_TRADE_NOTIONAL", 0.0)))
        final = round(notional, 2) if notional >= minimum else 0.0
        if final <= 0:
            return replace(
                decision,
                calculated_notional=0.0,
                calculated_quantity=0.0,
                approved=False,
                reason="PAPER_OPTIMIZER_TARGET_BELOW_EXECUTABLE_MINIMUM",
            )

        quantity = final / price
        cash_after = max(0.0, cash - final)
        prior = max(0.0, _number(getattr(decision, "calculated_notional", 0.0)))
        capacity_clipped = final + 1e-9 < target
        log.info(
            "PAPER_OPTIMIZER_SIZE_HANDOFF | symbol=%s | approved_target=%.2f | prior_downstream=%.2f | "
            "final=%.2f | gross_room=%.2f | liquidity_room=%.2f | capacity_clipped=%s | "
            "sample_clamp=BYPASSED | broker_submission=NONE | live_trading=DISARMED",
            str(kwargs.get("symbol") or "").upper(),
            target,
            prior,
            final,
            gross_room,
            liquidity_room,
            capacity_clipped,
        )
        return replace(
            decision,
            calculated_notional=final,
            calculated_quantity=round(quantity, 10),
            cash_after_trade=round(cash_after, 2),
            approved=True,
            reason=(
                "PAPER_UNBOUNDED_OPTIMIZER_TARGET_CAPACITY_CLIPPED"
                if capacity_clipped
                else "PAPER_UNBOUNDED_OPTIMIZER_TARGET"
            ),
        )

    def optimizer_target_buy(
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
        # This wrapper is installed after the process_signals churn guard and can
        # receive BUYs generated downstream by the strategic/core rebalance path.
        # Re-check the durable same-symbol cooldown at the final paper BUY handoff
        # so no optimizer path can bypass a losing-exit lockout.
        if _active() and str(market or "").strip().lower() == "crypto":
            allowed, reason = churn_guard._allow_generic_buy(signal)
            if not allowed:
                log.info(
                    "PAPER_OPTIMIZER_PRE_EXECUTION_CHURN_GUARD | symbol=%s | allowed=False | reason=%s | "
                    "broker_submission=NONE | live_trading=DISARMED",
                    str(symbol or "").upper(),
                    reason,
                )
                return False

        if (
            not _active()
            or str(market or "").strip().lower() != "crypto"
            or _is_configured_core(signal)
        ):
            return original_buy(
                market,
                symbol,
                price,
                signal,
                quant_assessment=quant_assessment,
                target_trade_value=target_trade_value,
                rotation_candidate=rotation_candidate,
                verified_quote=verified_quote,
                rotation_verified_quote=rotation_verified_quote,
            )

        target, dollar_volume = _optimizer_target(signal)
        if target <= 0:
            return original_buy(
                market,
                symbol,
                price,
                signal,
                quant_assessment=quant_assessment,
                target_trade_value=target_trade_value,
                rotation_candidate=rotation_candidate,
                verified_quote=verified_quote,
                rotation_verified_quote=rotation_verified_quote,
            )

        confidence = oracle_bot.normalized_confidence(signal)
        score = oracle_bot.normalized_score(signal)
        strength = max(0.55, min(1.0, max(confidence, score / 100.0)))
        compensation_multiplier = 1.0 / max(strength, 1e-9)
        effective_quant = _OptimizerSizingAssessment(quant_assessment, compensation_multiplier)

        target_token = _OPTIMIZER_TARGET.set(target)
        volume_token = _OPTIMIZER_DOLLAR_VOLUME.set(dollar_volume)
        try:
            log.info(
                "PAPER_OPTIMIZER_HANDOFF | market=crypto | symbol=%s | approved_target=%.2f | "
                "approved_dollar_volume=%.2f | soft_size_scaling=BYPASSED | mode=paper | "
                "broker_submission=NONE | live_trading=DISARMED",
                str(symbol or "").upper(),
                target,
                dollar_volume,
            )
            return original_buy(
                market,
                symbol,
                price,
                signal,
                quant_assessment=effective_quant,
                target_trade_value=target,
                rotation_candidate=rotation_candidate,
                verified_quote=verified_quote,
                rotation_verified_quote=rotation_verified_quote,
            )
        finally:
            _OPTIMIZER_DOLLAR_VOLUME.reset(volume_token)
            _OPTIMIZER_TARGET.reset(target_token)

    oracle_bot.adaptive_capital_allocation = optimizer_target_allocation
    oracle_bot._buy = optimizer_target_buy
    _INSTALLED = True
    log.info(
        "PAPER OPTIMIZER SIZE HANDOFF | active=True | minimum_sample_clamp=BYPASSED_FOR_OPTIMIZER_APPROVED_BUYS | "
        "stock_penny_crypto_misclassification=BYPASSED | final_churn_guard=ENFORCED | "
        "gross_execution_capacity=ENFORCED | max_trade_pct=%.4f | cash_and_liquidity_capacity=ENFORCED | "
        "broker_submission=NONE | live_trading=DISARMED",
        _number(getattr(oracle_bot, "MAX_TRADE_VALUE_PCT", 0.0)),
    )
    return True
