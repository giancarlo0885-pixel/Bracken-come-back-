from __future__ import annotations

import logging
import os
from dataclasses import replace


_INSTALLED = False


def _truthy(name: str, default: str = "false") -> bool:
    return str(os.getenv(name, default) or default).strip().lower() == "true"


def _active() -> bool:
    return (
        str(os.getenv("EXECUTION_MODE", "paper") or "paper").strip().lower() == "paper"
        and _truthy("PAPER_AUTONOMOUS_LEARNING")
        and not _truthy("ENABLE_BROKER_SUBMISSION")
        and not _truthy("LIVE_TRADING_ARMED")
    )


def install_paper_autonomous_learning() -> bool:
    """Relax strategy-only vetoes for autonomous paper learning.

    The override is active only while execution is explicitly paper-only and
    both live submission controls are disarmed. It does not bypass quote
    integrity, buying power, cash reserve, duplicate-order protection, fill
    realism, severe-drawdown blocks, or broker submission policy.

    In learning mode, low confidence/liquidity no longer collapse an otherwise
    approved candidate to zero. If the normal risk formula still produces less
    than the configured minimum trade notional, the simulation is allowed to
    take a minimum viable sample position, bounded by available cash and the
    normal single-position ceiling. This creates outcome data without granting
    any live-money permission.
    """
    global _INSTALLED
    if _INSTALLED:
        return True
    if not _active():
        return False

    import capital_allocator

    original_confidence_multiplier = capital_allocator.confidence_multiplier
    original_liquidity_multiplier = capital_allocator.liquidity_multiplier
    original_adaptive_capital_allocation = capital_allocator.adaptive_capital_allocation

    confidence_floor = max(
        0.01,
        min(1.0, float(os.getenv("PAPER_LEARNING_CONFIDENCE_MULTIPLIER_FLOOR", "0.45"))),
    )
    liquidity_floor = max(
        0.01,
        min(1.0, float(os.getenv("PAPER_LEARNING_LIQUIDITY_MULTIPLIER_FLOOR", "0.40"))),
    )
    learning_notional = max(
        float(capital_allocator.MIN_TRADE_NOTIONAL),
        float(os.getenv("PAPER_LEARNING_MIN_NOTIONAL", str(capital_allocator.MIN_TRADE_NOTIONAL))),
    )

    def paper_confidence_multiplier(confidence: float) -> float:
        value = float(original_confidence_multiplier(confidence))
        return value if value > 0.0 else confidence_floor

    def paper_liquidity_multiplier(dollar_volume: float) -> float:
        value = float(original_liquidity_multiplier(dollar_volume))
        return value if value > 0.0 else liquidity_floor

    def paper_adaptive_capital_allocation(**kwargs):
        decision = original_adaptive_capital_allocation(**kwargs)
        if decision.approved or decision.reason != "BELOW_MINIMUM_NOTIONAL" or not _active():
            return decision

        price = max(0.0, float(kwargs.get("price") or 0.0))
        cash = max(0.0, float(kwargs.get("cash") or 0.0))
        if price <= 0.0 or cash <= 0.0:
            return decision

        spendable_cash = max(0.0, cash - float(decision.reserve_required or 0.0))
        if bool(kwargs.get("buying_power_validated")) and kwargs.get("buying_power") is not None:
            spendable_cash = min(spendable_cash, max(0.0, float(kwargs.get("buying_power") or 0.0)))

        # Keep the normal single-position ceiling. We are relaxing the
        # minimum-sample veto, not manufacturing leverage or ignoring reserves.
        position_room = max(
            0.0,
            float(decision.max_position_dollars or 0.0)
            - max(0.0, float(kwargs.get("existing_position_value") or 0.0)),
        )
        notional = min(learning_notional, spendable_cash, position_room)
        if notional < float(capital_allocator.MIN_TRADE_NOTIONAL):
            return decision

        is_crypto = str(kwargs.get("market") or "").lower() == "crypto" or str(kwargs.get("symbol") or "").upper().endswith("-USD")
        fractional_allowed = capital_allocator.ENABLE_FRACTIONAL_CRYPTO if is_crypto else capital_allocator.ENABLE_FRACTIONAL_EQUITIES
        if is_crypto and kwargs.get("fractional_crypto") is not None:
            fractional_allowed = bool(kwargs.get("fractional_crypto"))
        if not is_crypto and kwargs.get("fractional_equities") is not None:
            fractional_allowed = bool(kwargs.get("fractional_equities"))

        quantity = notional / price
        if not fractional_allowed:
            import math
            quantity = math.floor(quantity)
            notional = quantity * price
            if quantity <= 0 or notional < float(capital_allocator.MIN_TRADE_NOTIONAL):
                return decision

        cash_after = cash - notional
        if cash_after < float(decision.reserve_required or 0.0):
            return decision

        return replace(
            decision,
            calculated_notional=round(notional, 2),
            calculated_quantity=round(quantity, 10),
            cash_after_trade=round(cash_after, 2),
            approved=True,
            reason="PAPER_AUTONOMOUS_LEARNING_MINIMUM_SAMPLE",
        )

    capital_allocator.confidence_multiplier = paper_confidence_multiplier
    capital_allocator.liquidity_multiplier = paper_liquidity_multiplier
    capital_allocator.adaptive_capital_allocation = paper_adaptive_capital_allocation
    capital_allocator.PAPER_AUTONOMOUS_LEARNING_ACTIVE = True

    # oracle_bot imports the allocator function directly, so update its bound
    # reference explicitly after market_worker bootstrap.
    try:
        import oracle_bot
        oracle_bot.adaptive_capital_allocation = paper_adaptive_capital_allocation
    except Exception:
        pass

    _INSTALLED = True
    logging.getLogger("paper-autonomous-learning").info(
        "PAPER AUTONOMOUS LEARNING | active=True | confidence_floor=%.2f | liquidity_floor=%.2f | "
        "minimum_sample_notional=%.2f | execution_mode=paper | broker_submission=NONE | live_trading=DISARMED",
        confidence_floor,
        liquidity_floor,
        learning_notional,
    )
    return True
