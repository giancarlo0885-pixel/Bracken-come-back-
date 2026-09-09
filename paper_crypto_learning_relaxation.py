from __future__ import annotations

import logging
import os
from datetime import datetime, timedelta, timezone
from typing import Any

_INSTALLED = False
log = logging.getLogger("paper-crypto-learning")


def _truthy(name: str, default: str = "false") -> bool:
    return str(os.getenv(name, default) or default).strip().lower() == "true"


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        number = float(value)
        return number if number == number else default
    except (TypeError, ValueError):
        return default


def _active() -> bool:
    return (
        str(os.getenv("EXECUTION_MODE", "paper") or "paper").strip().lower() == "paper"
        and _truthy("PAPER_AUTONOMOUS_LEARNING")
        and not _truthy("ENABLE_BROKER_SUBMISSION")
        and not _truthy("LIVE_TRADING_ARMED")
    )


def _unbounded_learning() -> bool:
    return _active() and _truthy("PAPER_UNBOUNDED_LEARNING")


def _paper_limits() -> tuple[float, int, int]:
    """Return paper cadence limits; unbounded mode removes cadence throttles."""
    if _unbounded_learning():
        return 1_000_000_000.0, 2_147_483_647, 0
    max_turnover = max(0.20, min(5.0, _safe_float(os.getenv("PAPER_CRYPTO_MAX_DAILY_TURNOVER_PCT", "1.00"), 1.0)))
    max_entries = max(3, min(250, int(_safe_float(os.getenv("PAPER_CRYPTO_MAX_DAILY_ENTRIES", "48"), 48))))
    same_symbol_cooldown = max(1, min(120, int(_safe_float(os.getenv("PAPER_CRYPTO_ENTRY_COOLDOWN_MINUTES", "15"), 15))))
    return max_turnover, max_entries, same_symbol_cooldown


def install_paper_crypto_learning_relaxation() -> bool:
    """Relax production risk/cadence vetoes for autonomous crypto paper learning.

    PAPER_UNBOUNDED_LEARNING removes paper-only cadence, drawdown, concentration,
    correlation, spread/slippage and same-symbol cooldown vetoes while execution
    is explicitly paper-only. Quote identity/freshness, finite execution inputs,
    simulated accounting, execution claims, and every live-order control remain
    authoritative so learning records are grounded in real market observations.
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
    original_recent_trade = oracle_bot.recent_trade

    def paper_penny_gate(market: str, symbol: str, price: float, signal: Any, score: float, confidence: float):
        if _active() and str(market or "").strip().lower() == "crypto":
            return True, "not applicable to crypto paper learning"
        return original_penny_gate(market, symbol, price, signal, score, confidence)

    def paper_recent_trade(market: str, symbol: str):
        market_text = str(market or "").strip().lower()
        if not (_active() and market_text == "crypto"):
            return original_recent_trade(market, symbol)
        if _unbounded_learning():
            return None
        _, _, cooldown_minutes = _paper_limits()
        cutoff = (datetime.now(timezone.utc) - timedelta(minutes=cooldown_minutes)).isoformat()
        try:
            recent = oracle_bot.row(
                """
                SELECT *
                FROM trades
                WHERE market=%s AND symbol=%s AND created_at >= %s
                ORDER BY created_at DESC
                LIMIT 1
                """,
                (market_text, str(symbol or "").upper().strip(), cutoff),
            )
        except Exception:
            return original_recent_trade(market, symbol)
        if recent:
            log.info(
                "PAPER ENTRY PACING | symbol=%s | blocked=True | cooldown=%dm | last_side=%s | "
                "broker_submission=NONE | live_trading=DISARMED",
                str(symbol or "").upper().strip(),
                cooldown_minutes,
                str(recent.get("side") or "UNKNOWN").upper(),
            )
        return recent

    def paper_pre_trade_risk_checks(**kwargs):
        market = str(kwargs.get("market") or "").strip().lower()
        intent = str(kwargs.get("intent") or "").strip().lower()
        side = str(kwargs.get("side") or "").strip().upper()
        is_entry = intent in {"entry", "rotation_in"} or (not intent and side == "BUY")
        if not (_active() and market == "crypto" and is_entry):
            return original_pre_trade_risk_checks(**kwargs)

        max_turnover, max_entries, _ = _paper_limits()
        actual_turnover = max(0.0, _safe_float(kwargs.get("turnover_pct_today"), 0.0))
        actual_entries = max(0, int(_safe_float(kwargs.get("new_entries_today"), 0.0)))

        updated = dict(kwargs)
        updated["new_entries_today"] = 0
        updated["positions"] = []
        updated["turnover_pct_today"] = 0.0

        if _unbounded_learning():
            updated["daily_loss_pct"] = 0.0
            updated["weekly_loss_pct"] = 0.0
            updated["spread_pct"] = 0.0
            updated["slippage_pct"] = 0.0
            updated["correlation_exposure_pct"] = 0.0
            updated["concentration_pct"] = 0.0
            result = original_pre_trade_risk_checks(**updated)
            result.metrics["paper_learning_actual_turnover_pct"] = actual_turnover
            result.metrics["paper_learning_actual_entries"] = float(actual_entries)
            result.metrics["paper_unbounded_learning"] = 1.0
            return result

        result = original_pre_trade_risk_checks(**updated)
        result.add(
            "paper_learning_turnover",
            actual_turnover <= max_turnover,
            f"paper crypto daily turnover limit reached ({actual_turnover:.2%}/{max_turnover:.2%})",
        )
        result.add(
            "paper_learning_entries",
            actual_entries < max_entries,
            f"paper crypto daily entry limit reached ({actual_entries}/{max_entries})",
        )
        result.metrics["paper_learning_actual_turnover_pct"] = actual_turnover
        result.metrics["paper_learning_max_turnover_pct"] = max_turnover
        result.metrics["paper_learning_actual_entries"] = float(actual_entries)
        result.metrics["paper_learning_max_entries"] = float(max_entries)
        return result

    def paper_shared_risk_gate(**kwargs):
        market = str(kwargs.get("market") or "").strip().lower()
        if not (_active() and market == "crypto"):
            return original_shared_risk_gate(**kwargs)
        if _unbounded_learning():
            return True, "paper unbounded learning", {"paper_unbounded_learning": True}

        updated = dict(kwargs)
        quote = dict(updated.get("quote") or {})
        quote["correlation_exposure_pct"] = 0.0
        quote["correlation_source"] = "paper_learning_non_veto_observation"
        updated["quote"] = quote
        return original_shared_risk_gate(**updated)

    oracle_bot._penny_stock_gate = paper_penny_gate
    oracle_bot.pre_trade_risk_checks = paper_pre_trade_risk_checks
    oracle_bot._shared_risk_gate = paper_shared_risk_gate
    oracle_bot.recent_trade = paper_recent_trade
    _INSTALLED = True
    max_turnover, max_entries, cooldown = _paper_limits()
    if _unbounded_learning():
        log.info(
            "Installed UNBOUNDED crypto paper learning | cadence_limits=OFF | cooldown=OFF | "
            "risk_throttles=OBSERVE_ONLY | broker_submission=NONE | live_trading=DISARMED"
        )
    else:
        log.info(
            "Installed bounded crypto paper learning | max_daily_turnover=%.0f%% | max_daily_entries=%d | "
            "same_symbol_cooldown=%dm | broker_submission=NONE | live_trading=DISARMED",
            max_turnover * 100.0,
            max_entries,
            cooldown,
        )
    return True
