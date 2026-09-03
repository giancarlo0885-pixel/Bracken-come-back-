from __future__ import annotations

import os
from typing import Any

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


def install_paper_crypto_learning_relaxation() -> bool:
    """Relax strategy-only crypto vetoes strictly for autonomous paper learning.

    Quote identity/freshness, buying power, reserve checks, execution claims,
    duplicate protection, fill realism, accounting, and all live-order controls
    remain intact. Stock-only penny rules and tactical correlation limits do not
    block simulated crypto samples while autonomous paper learning is active.
    """
    global _INSTALLED
    if _INSTALLED:
        return True
    if not _active():
        return False

    import oracle_bot

    original_penny_gate = oracle_bot._penny_stock_gate
    original_shared_risk_gate = oracle_bot._shared_risk_gate

    def paper_penny_gate(market: str, symbol: str, price: float, signal: Any, score: float, confidence: float):
        if _active() and str(market or "").strip().lower() == "crypto":
            return True, "not applicable to crypto paper learning"
        return original_penny_gate(market, symbol, price, signal, score, confidence)

    def paper_shared_risk_gate(**kwargs):
        market = str(kwargs.get("market") or "").strip().lower()
        if not (_active() and market == "crypto"):
            return original_shared_risk_gate(**kwargs)

        updated = dict(kwargs)
        quote = dict(updated.get("quote") or {})
        # Correlation remains measurable in signal/model evidence, but it is a
        # strategy constraint rather than a data-integrity constraint. Setting
        # the execution-gate exposure to zero prevents it from starving an
        # explicitly no-threshold autonomous paper-learning experiment.
        quote["correlation_exposure_pct"] = 0.0
        quote["correlation_source"] = "paper_learning_non_veto_observation"
        updated["quote"] = quote
        return original_shared_risk_gate(**updated)

    oracle_bot._penny_stock_gate = paper_penny_gate
    oracle_bot._shared_risk_gate = paper_shared_risk_gate
    _INSTALLED = True
    return True
