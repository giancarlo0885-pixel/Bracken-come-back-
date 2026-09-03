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


def _safe_number(value, default=None):
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    if number != number or number in (float("inf"), float("-inf")):
        return default
    return number


def _position_value(position: dict) -> float:
    for key in ("market_value", "position_value", "value", "notional"):
        value = _safe_number(position.get(key))
        if value is not None and value >= 0:
            return value
    quantity = _safe_number(position.get("quantity"), 0.0) or 0.0
    price = None
    for key in ("current_price", "price", "mark_price", "last_price", "avg_price", "average_price"):
        price = _safe_number(position.get(key))
        if price is not None and price > 0:
            break
    return max(0.0, quantity * (price or 0.0))


def install_paper_autonomous_learning() -> bool:
    """Relax strategy-only vetoes for autonomous paper learning.

    Active only while execution is explicitly paper-only and both live
    submission controls are disarmed. Quote identity/freshness, cash reserve,
    buying power, duplicate-order protection, fill realism, accounting,
    severe-drawdown blocks, and broker submission policy remain intact.

    Missing slippage/correlation inputs are repaired with deterministic paper
    proxies rather than bypassing the shared risk engine: slippage uses half the
    verified spread when available (otherwise a configurable paper assumption),
    and correlation exposure uses current gross paper exposure/equity as a
    conservative proxy when no model metric exists.
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
    default_slippage_pct = max(
        0.0,
        min(0.05, float(os.getenv("PAPER_LEARNING_DEFAULT_SLIPPAGE_PCT", "0.001"))),
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

    try:
        import oracle_bot

        oracle_bot.adaptive_capital_allocation = paper_adaptive_capital_allocation
        original_shared_risk_gate = oracle_bot._shared_risk_gate

        def paper_shared_risk_gate(**kwargs):
            if not _active():
                return original_shared_risk_gate(**kwargs)

            quote = dict(kwargs.get("quote") or {})
            positions = list(kwargs.get("positions") or [])
            portfolio = kwargs.get("portfolio") or {}

            if _safe_number(quote.get("slippage_pct")) is None and _safe_number(quote.get("estimated_slippage_pct")) is None:
                spread = None
                try:
                    spread = oracle_bot._quote_spread_pct(quote)
                except Exception:
                    spread = None
                quote["slippage_pct"] = max(0.0, float(spread) / 2.0) if spread is not None else default_slippage_pct
                quote["slippage_source"] = "paper_learning_spread_proxy" if spread is not None else "paper_learning_default_proxy"

            if _safe_number(quote.get("correlation_exposure_pct")) is None:
                equity = 0.0
                if isinstance(portfolio, dict):
                    for key in ("equity", "current_equity", "portfolio_equity", "balance"):
                        equity = _safe_number(portfolio.get(key), 0.0) or 0.0
                        if equity > 0:
                            break
                else:
                    for key in ("equity", "current_equity", "portfolio_equity", "balance"):
                        equity = _safe_number(getattr(portfolio, key, None), 0.0) or 0.0
                        if equity > 0:
                            break
                gross = sum(_position_value(position) for position in positions if isinstance(position, dict))
                quote["correlation_exposure_pct"] = min(1.0, gross / equity) if equity > 0 else (0.0 if not positions else 1.0)
                quote["correlation_source"] = "paper_learning_gross_exposure_proxy"

            updated = dict(kwargs)
            updated["quote"] = quote
            return original_shared_risk_gate(**updated)

        oracle_bot._shared_risk_gate = paper_shared_risk_gate
    except Exception:
        pass

    _INSTALLED = True
    logging.getLogger("paper-autonomous-learning").info(
        "PAPER AUTONOMOUS LEARNING | active=True | confidence_floor=%.2f | liquidity_floor=%.2f | "
        "minimum_sample_notional=%.2f | default_slippage_pct=%.4f | risk_metric_proxies=ENABLED | "
        "execution_mode=paper | broker_submission=NONE | live_trading=DISARMED",
        confidence_floor,
        liquidity_floor,
        learning_notional,
        default_slippage_pct,
    )
    return True
