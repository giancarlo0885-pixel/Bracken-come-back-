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


def _number(value: Any, default: float = 0.0) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    if number != number or number in (float("inf"), float("-inf")):
        return default
    return number


def _position_value(position: dict[str, Any]) -> float:
    for key in ("market_value", "position_value", "value", "notional"):
        value = _number(position.get(key), -1.0)
        if value >= 0.0:
            return value
    qty = max(0.0, _number(position.get("quantity")))
    price = 0.0
    for key in ("current_price", "price", "mark_price", "last_price", "avg_price", "average_price"):
        price = _number(position.get(key))
        if price > 0.0:
            break
    return qty * max(0.0, price)


def _equity(portfolio: Any) -> float:
    keys = ("equity", "current_equity", "portfolio_equity", "balance")
    if isinstance(portfolio, dict):
        for key in keys:
            value = _number(portfolio.get(key))
            if value > 0.0:
                return value
    else:
        for key in keys:
            value = _number(getattr(portfolio, key, None))
            if value > 0.0:
                return value
    return 0.0


def install_paper_crypto_learning_relaxation() -> bool:
    """Relax strategy-only crypto vetoes strictly for autonomous paper learning.

    This does not weaken quote identity/freshness, buying power, reserve checks,
    execution claims, duplicate protection, fill realism, accounting, or any
    live-order control. It only prevents stock-only penny rules and tactical
    correlation scores from blocking simulated crypto learning samples.
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
        positions = [p for p in list(updated.get("positions") or []) if isinstance(p, dict)]
        equity = _equity(updated.get("portfolio") or {})
        gross = sum(_position_value(position) for position in positions)

        # Use actual simulated portfolio exposure for paper learning instead of
        # a tactical/model correlation estimate. This keeps exposure measurable
        # while preventing a strategy veto from starving the learner of samples.
        quote["correlation_exposure_pct"] = min(1.0, gross / equity) if equity > 0.0 else (0.0 if not positions else 1.0)
        quote["correlation_source"] = "paper_learning_actual_gross_exposure"
        updated["quote"] = quote
        return original_shared_risk_gate(**updated)

    oracle_bot._penny_stock_gate = paper_penny_gate
    oracle_bot._shared_risk_gate = paper_shared_risk_gate
    _INSTALLED = True
    return True
