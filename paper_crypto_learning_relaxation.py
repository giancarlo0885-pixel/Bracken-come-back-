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
    """Relax strategy/capacity vetoes strictly for autonomous crypto paper learning.

    Autonomous paper learning is intentionally allowed to keep generating samples
    without production daily-entry, open-position-count, correlation, or daily-
    turnover strategy caps. Quote identity/freshness, finite metrics,
    spread/slippage, liquidity, cash/buying-power/reserve checks, accounting,
    duplicate protection, fill realism, execution claims, and every live-order
    control remain intact. The relaxation cannot activate when broker submission
    or live trading is armed.
    """
    global _INSTALLED
    if _INSTALLED:
        return True
    if not _active():
        return False

    import oracle_bot

    original_penny_gate = oracle_bot._penny_stock_gate
    original_shared_risk_gate = oracle_bot._shared_risk_gate
    original_pre_trade_risk_checks = oracle_bot.pre_trade_risk_checks

    def paper_penny_gate(market: str, symbol: str, price: float, signal: Any, score: float, confidence: float):
        if _active() and str(market or "").strip().lower() == "crypto":
            return True, "not applicable to crypto paper learning"
        return original_penny_gate(market, symbol, price, signal, score, confidence)

    def paper_pre_trade_risk_checks(**kwargs):
        market = str(kwargs.get("market") or "").strip().lower()
        intent = str(kwargs.get("intent") or "").strip().lower()
        side = str(kwargs.get("side") or "").strip().upper()
        is_entry = intent in {"entry", "rotation_in"} or (not intent and side == "BUY")
        if not (_active() and market == "crypto" and is_entry):
            return original_pre_trade_risk_checks(**kwargs)

        updated = dict(kwargs)
        # These metrics only impose production cadence/capacity strategy limits.
        # Neutralize them inside the isolated paper-learning sandbox while the
        # canonical portfolio remains authoritative for solvency and accounting.
        updated["new_entries_today"] = 0
        updated["positions"] = []
        updated["turnover_pct_today"] = 0.0
        return original_pre_trade_risk_checks(**updated)

    def paper_shared_risk_gate(**kwargs):
        market = str(kwargs.get("market") or "").strip().lower()
        if not (_active() and market == "crypto"):
            return original_shared_risk_gate(**kwargs)

        updated = dict(kwargs)
        quote = dict(updated.get("quote") or {})
        # Correlation remains observable in model evidence, but is not allowed to
        # starve an explicitly free-form autonomous paper-learning experiment.
        quote["correlation_exposure_pct"] = 0.0
        quote["correlation_source"] = "paper_learning_non_veto_observation"
        updated["quote"] = quote
        return original_shared_risk_gate(**updated)

    oracle_bot._penny_stock_gate = paper_penny_gate
    oracle_bot.pre_trade_risk_checks = paper_pre_trade_risk_checks
    oracle_bot._shared_risk_gate = paper_shared_risk_gate
    _INSTALLED = True
    return True
