from __future__ import annotations

import logging
import os
import time
from datetime import datetime, timezone
from typing import Any

from paper_fast_reversal_hysteresis import install_paper_fast_reversal_hysteresis


log = logging.getLogger("paper-churn-guard")
_INSTALLED = False
_SELL_CONFIRMATIONS: dict[str, tuple[int, float]] = {}


def _truthy(name: str, default: str = "false") -> bool:
    return str(os.getenv(name, default) or default).strip().lower() == "true"


def _paper_only() -> bool:
    return (
        str(os.getenv("EXECUTION_MODE", "paper") or "paper").strip().lower() == "paper"
        and not _truthy("ENABLE_BROKER_SUBMISSION")
        and not _truthy("LIVE_TRADING_ARMED")
    )


def _unbounded_learning() -> bool:
    return (
        _paper_only()
        and _truthy("PAPER_AUTONOMOUS_LEARNING")
        and _truthy("PAPER_UNBOUNDED_LEARNING")
    )


def _value(signal: Any, name: str, default: Any = None) -> Any:
    if isinstance(signal, dict):
        return signal.get(name, default)
    return getattr(signal, name, default)


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        result = float(value)
        return result if result == result else default
    except (TypeError, ValueError):
        return default


def _parse_time(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)
    except Exception:
        return None


def _settings() -> tuple[float, int, float, float, float, float]:
    min_hold = max(0.0, min(60.0, _safe_float(os.getenv("PAPER_CRYPTO_MIN_SIGNAL_HOLD_MINUTES", "5"), 5.0)))
    confirmations = max(1, min(5, int(_safe_float(os.getenv("PAPER_CRYPTO_SELL_CONFIRMATIONS", "2"), 2))))
    window = max(10.0, min(600.0, _safe_float(os.getenv("PAPER_CRYPTO_SELL_CONFIRMATION_WINDOW_SECONDS", "120"), 120.0)))
    emergency_loss = max(0.5, min(25.0, _safe_float(os.getenv("PAPER_CRYPTO_EMERGENCY_EXIT_LOSS_PCT", "6"), 6.0)))
    reentry_cooldown = max(0.0, min(120.0, _safe_float(os.getenv("PAPER_CRYPTO_REENTRY_COOLDOWN_MINUTES", "10"), 10.0)))
    loss_multiplier = max(1.0, min(6.0, _safe_float(os.getenv("PAPER_CRYPTO_LOSS_REENTRY_COOLDOWN_MULTIPLIER", "3"), 3.0)))
    return min_hold, confirmations, window, emergency_loss, reentry_cooldown, loss_multiplier


def _loss_streak_step() -> float:
    return max(0.0, min(2.0, _safe_float(os.getenv("PAPER_CRYPTO_CONSECUTIVE_LOSS_COOLDOWN_STEP", "0.5"), 0.5)))


def _position_and_last_buy(symbol: str) -> tuple[dict[str, Any] | None, datetime | None]:
    try:
        import oracle_bot

        position = oracle_bot.row(
            "SELECT * FROM positions WHERE market='crypto' AND symbol=%s",
            (symbol,),
        )
        buy = oracle_bot.row(
            """
            SELECT created_at, price
            FROM trades
            WHERE market='crypto' AND symbol=%s AND side='BUY'
            ORDER BY created_at DESC
            LIMIT 1
            """,
            (symbol,),
        ) or {}
        return position, _parse_time(buy.get("created_at"))
    except Exception:
        log.exception("PAPER CHURN GUARD | symbol=%s | state_load=FAIL", symbol)
        return None, None


def _last_trade(symbol: str, side: str) -> dict[str, Any]:
    try:
        import oracle_bot

        return oracle_bot.row(
            """
            SELECT created_at, price
            FROM trades
            WHERE market='crypto' AND symbol=%s AND side=%s
            ORDER BY created_at DESC
            LIMIT 1
            """,
            (symbol, side.upper()),
        ) or {}
    except Exception:
        log.exception("PAPER CHURN GUARD | symbol=%s | side=%s | trade_load=FAIL", symbol, side)
        return {}


def _recent_realized_loss_streak(symbol: str, limit: int = 6) -> int:
    """Return consecutive negative realized-P&L SELLs, newest first.

    This is intentionally read-only and paper-economic only. If realized P&L is
    unavailable, return zero so the existing gross-price loss classification
    still supplies the baseline 30-minute protection.
    """
    try:
        from database import rows

        records = rows(
            """
            SELECT realized_pnl
            FROM trades
            WHERE market='crypto' AND symbol=%s AND side='SELL'
            ORDER BY created_at DESC
            LIMIT %s
            """,
            (symbol, max(1, min(12, int(limit)))),
        ) or []
    except Exception:
        log.exception("PAPER CHURN GUARD | symbol=%s | realized_loss_streak_load=FAIL", symbol)
        return 0

    streak = 0
    for record in records:
        value = record.get("realized_pnl") if isinstance(record, dict) else None
        if value is None:
            break
        if _safe_float(value) < 0:
            streak += 1
            continue
        break
    return streak


def _last_trade_time(symbol: str, side: str) -> datetime | None:
    return _parse_time(_last_trade(symbol, side).get("created_at"))


def _current_price(signal: Any, prices: dict[str, Any], symbol: str) -> float:
    quote = dict((prices or {}).get(symbol) or {})
    for value in (
        quote.get("price"), quote.get("mid"), _value(signal, "price", None), _value(signal, "current_price", None)
    ):
        price = _safe_float(value)
        if price > 0:
            return price
    return 0.0


def _allow_generic_buy(signal: Any) -> tuple[bool, str]:
    symbol = str(_value(signal, "symbol", "") or "").upper().strip()
    action = str(_value(signal, "action", "") or "").upper().strip()
    if action != "BUY" or not symbol:
        return True, "not_generic_buy"

    _, _, _, _, cooldown_minutes, loss_multiplier = _settings()
    if cooldown_minutes <= 0:
        return True, "reentry_cooldown_disabled"

    sell_trade = _last_trade(symbol, "SELL")
    last_sell = _parse_time(sell_trade.get("created_at"))
    if last_sell is None:
        return True, "no_prior_sell"

    buy_trade = _last_trade(symbol, "BUY")
    last_buy = _parse_time(buy_trade.get("created_at"))
    if last_buy is not None and last_buy > last_sell:
        return True, "already_reentered"

    sell_price = _safe_float(sell_trade.get("price"))
    buy_price = _safe_float(buy_trade.get("price"))
    losing_exit = bool(sell_price > 0 and buy_price > 0 and sell_price < buy_price)
    effective_cooldown = cooldown_minutes
    loss_streak = 0
    if losing_exit:
        loss_streak = max(1, _recent_realized_loss_streak(symbol))
        streak_multiplier = 1.0 + max(0, loss_streak - 1) * _loss_streak_step()
        effective_cooldown = cooldown_minutes * loss_multiplier * streak_multiplier
    effective_cooldown = min(360.0, effective_cooldown)

    age_minutes = max(0.0, (datetime.now(timezone.utc) - last_sell).total_seconds() / 60.0)
    if age_minutes < effective_cooldown:
        if losing_exit:
            return False, f"loss_reentry_cooldown:streak={loss_streak}:{age_minutes:.2f}/{effective_cooldown:.2f}m"
        return False, f"reentry_cooldown:{age_minutes:.2f}/{effective_cooldown:.2f}m"
    if losing_exit:
        return True, f"loss_reentry_cooldown_elapsed:streak={loss_streak}"
    return True, "reentry_cooldown_elapsed"


def _allow_generic_sell(signal: Any, prices: dict[str, Any]) -> tuple[bool, str]:
    symbol = str(_value(signal, "symbol", "") or "").upper().strip()
    action = str(_value(signal, "action", "") or "").upper().strip()
    if action != "SELL" or not symbol:
        return True, "not_generic_sell"

    min_hold, required_confirmations, window, emergency_loss, _, _ = _settings()
    position, last_buy = _position_and_last_buy(symbol)
    if not position:
        return True, "no_open_position"
    if last_buy is None:
        return False, "missing_entry_timestamp"

    now = datetime.now(timezone.utc)
    age_minutes = max(0.0, (now - last_buy).total_seconds() / 60.0)
    entry = _safe_float(position.get("average_price", position.get("entry_price", 0.0)))
    current = _current_price(signal, prices, symbol)
    return_pct = ((current / entry) - 1.0) * 100.0 if entry > 0 and current > 0 else None

    if return_pct is not None and return_pct <= -emergency_loss:
        _SELL_CONFIRMATIONS.pop(symbol, None)
        return True, f"emergency_loss_override:{return_pct:.3f}%"

    if age_minutes < min_hold:
        _SELL_CONFIRMATIONS.pop(symbol, None)
        return False, f"minimum_hold:{age_minutes:.2f}/{min_hold:.2f}m"

    monotonic_now = time.monotonic()
    count, previous = _SELL_CONFIRMATIONS.get(symbol, (0, 0.0))
    if previous <= 0 or monotonic_now - previous > window:
        count = 0
    count += 1
    _SELL_CONFIRMATIONS[symbol] = (count, monotonic_now)
    if count < required_confirmations:
        return False, f"sell_confirmation:{count}/{required_confirmations}"

    _SELL_CONFIRMATIONS.pop(symbol, None)
    return True, f"sell_confirmed:{required_confirmations}/{required_confirmations}"


def install_paper_crypto_churn_guard(worker: Any) -> bool:
    """Reduce fee-heavy paper churn without tightening strategy signal thresholds.

    Generic SELL flips use a minimum hold and confirmation requirement. Fresh BUY
    re-entry after a completed SELL is cooled down, with a longer cooldown after a
    losing exit. Consecutive realized losing exits progressively extend that same-
    symbol cooldown so repeated failed setups consume fewer simulated fees while
    exploration remains available. In unbounded mode upstream hysteresis remains
    off, preserving the relaxed signal stream. EXIT/CLOSE and emergency-loss exits
    are never delayed. This guard is paper-only and cannot activate broker submission.
    """
    global _INSTALLED
    if _INSTALLED:
        return True
    if not _paper_only():
        return False

    unbounded = _unbounded_learning()
    if not unbounded:
        install_paper_fast_reversal_hysteresis(worker)

    original = worker.process_signals
    if not callable(original):
        raise RuntimeError("market worker has no process_signals")

    def guarded_process_signals(market: str, signals: Any, prices: dict[str, Any] | None = None, *args: Any, **kwargs: Any):
        if not (_paper_only() and str(market or "").strip().lower() == "crypto"):
            return original(market, signals, prices, *args, **kwargs)

        accepted: list[Any] = []
        for signal in list(signals or []):
            action = str(_value(signal, "action", "") or "").upper().strip()
            symbol = str(_value(signal, "symbol", "") or "").upper().strip()

            if action == "BUY":
                allowed, reason = _allow_generic_buy(signal)
                if not allowed:
                    log.info(
                        "PAPER CHURN GUARD | symbol=%s | action=BUY | allowed=False | reason=%s | "
                        "broker_submission=NONE | live_trading=DISARMED",
                        symbol,
                        reason,
                    )
                    continue
                accepted.append(signal)
                continue

            if action != "SELL":
                accepted.append(signal)
                continue

            allowed, reason = _allow_generic_sell(signal, prices or {})
            if not allowed:
                log.info(
                    "PAPER CHURN GUARD | symbol=%s | action=SELL | allowed=False | reason=%s | "
                    "broker_submission=NONE | live_trading=DISARMED",
                    symbol,
                    reason,
                )
                continue
            log.info("PAPER CHURN GUARD | symbol=%s | action=SELL | allowed=True | reason=%s", symbol, reason)
            accepted.append(signal)

        if not accepted:
            return []
        return original(market, accepted, prices, *args, **kwargs)

    worker.process_signals = guarded_process_signals
    _INSTALLED = True
    min_hold, confirmations, window, emergency_loss, reentry_cooldown, loss_multiplier = _settings()
    log.info(
        "Installed paper crypto churn guard | mode=%s | min_hold=%.2fm | confirmations=%d | window=%.0fs | "
        "emergency_loss=%.2f%% | reentry_cooldown=%.2fm | loss_reentry_multiplier=%.2fx | loss_streak_step=%.2fx | "
        "upstream_hysteresis=%s | relaxed_signal_thresholds=UNCHANGED | broker_submission=NONE | live_trading=DISARMED",
        "UNBOUNDED_CONTROLLED" if unbounded else "BOUNDED",
        min_hold,
        confirmations,
        window,
        emergency_loss,
        reentry_cooldown,
        loss_multiplier,
        _loss_streak_step(),
        "OFF" if unbounded else "ACTIVE",
    )
    return True
