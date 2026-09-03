"""Process-wide paper-learning overrides for GARIBALDI MARKET ORACLE.

Python imports ``sitecustomize`` during interpreter startup when this repository
is on ``sys.path``.  The override is deliberately narrow: it only activates
when the worker is explicitly in paper mode *and* PAPER_AUTONOMOUS_LEARNING is
true.  It never enables broker submission or live trading, and it does not
alter persisted signal confidence/liquidity observations.

The production allocator normally maps sub-threshold confidence or liquidity
to a zero multiplier, which acts as an execution veto.  Autonomous paper
learning needs those candidates to produce simulated outcomes, so in this mode
only the zero multiplier is replaced by a conservative non-zero starter
multiplier.  All downstream quote-integrity, buying-power, concentration,
accounting, duplicate-claim, fill-realism, and execution-policy checks remain
in force.
"""

from __future__ import annotations

import os


def _enabled() -> bool:
    return (
        str(os.getenv("EXECUTION_MODE", "paper") or "paper").strip().lower() == "paper"
        and str(os.getenv("PAPER_AUTONOMOUS_LEARNING", "false") or "false").strip().lower() == "true"
        and str(os.getenv("ENABLE_BROKER_SUBMISSION", "false") or "false").strip().lower() != "true"
        and str(os.getenv("LIVE_TRADING_ARMED", "false") or "false").strip().lower() != "true"
    )


if _enabled():
    try:
        import capital_allocator as _capital_allocator

        _original_confidence_multiplier = _capital_allocator.confidence_multiplier
        _original_liquidity_multiplier = _capital_allocator.liquidity_multiplier

        def _paper_learning_confidence_multiplier(confidence: float) -> float:
            multiplier = float(_original_confidence_multiplier(confidence))
            return multiplier if multiplier > 0.0 else 0.45

        def _paper_learning_liquidity_multiplier(dollar_volume: float) -> float:
            multiplier = float(_original_liquidity_multiplier(dollar_volume))
            return multiplier if multiplier > 0.0 else 0.40

        _capital_allocator.confidence_multiplier = _paper_learning_confidence_multiplier
        _capital_allocator.liquidity_multiplier = _paper_learning_liquidity_multiplier
        _capital_allocator.PAPER_AUTONOMOUS_LEARNING_ACTIVE = True
    except Exception:
        # Fail closed: if the learning shim cannot load, normal allocator gates
        # remain untouched and no execution permission is added.
        pass
