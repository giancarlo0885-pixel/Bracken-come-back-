from __future__ import annotations

import logging
import os


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
    """Relax strategy-only allocator vetoes for autonomous paper learning.

    This does not arm live trading and does not bypass quote integrity, buying
    power, position accounting, duplicate-order protection, fill realism,
    drawdown blocks, or broker submission policy. It only prevents sub-threshold
    confidence/liquidity multipliers from collapsing otherwise approved paper
    candidates to a zero allocation.
    """
    global _INSTALLED
    if _INSTALLED:
        return True
    if not _active():
        return False

    import capital_allocator

    original_confidence_multiplier = capital_allocator.confidence_multiplier
    original_liquidity_multiplier = capital_allocator.liquidity_multiplier

    confidence_floor = max(
        0.01,
        min(1.0, float(os.getenv("PAPER_LEARNING_CONFIDENCE_MULTIPLIER_FLOOR", "0.45"))),
    )
    liquidity_floor = max(
        0.01,
        min(1.0, float(os.getenv("PAPER_LEARNING_LIQUIDITY_MULTIPLIER_FLOOR", "0.40"))),
    )

    def paper_confidence_multiplier(confidence: float) -> float:
        value = float(original_confidence_multiplier(confidence))
        return value if value > 0.0 else confidence_floor

    def paper_liquidity_multiplier(dollar_volume: float) -> float:
        value = float(original_liquidity_multiplier(dollar_volume))
        return value if value > 0.0 else liquidity_floor

    capital_allocator.confidence_multiplier = paper_confidence_multiplier
    capital_allocator.liquidity_multiplier = paper_liquidity_multiplier
    capital_allocator.PAPER_AUTONOMOUS_LEARNING_ACTIVE = True
    _INSTALLED = True

    logging.getLogger("paper-autonomous-learning").info(
        "PAPER AUTONOMOUS LEARNING | active=True | confidence_floor=%.2f | liquidity_floor=%.2f | "
        "execution_mode=paper | broker_submission=NONE | live_trading=DISARMED",
        confidence_floor,
        liquidity_floor,
    )
    return True
