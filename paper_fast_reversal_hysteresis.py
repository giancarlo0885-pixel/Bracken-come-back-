from __future__ import annotations

import logging
import os
from datetime import datetime, timezone
from typing import Any


log = logging.getLogger("paper-fast-reversal-hysteresis")
_INSTALLED = False


def _truthy(name: str, default: str = "false") -> bool:
    return str(os.getenv(name, default) or default).strip().lower() == "true"


def _paper_only() -> bool:
    return (
        str(os.getenv("EXECUTION_MODE", "paper") or "paper").strip().lower() == "paper"
        and not _truthy("ENABLE_BROKER_SUBMISSION")
        and not _truthy("LIVE_TRADING_ARMED")
    )


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


def _recent_entry_age_minutes(symbol: str) -> float | None:
    try:
        import oracle_bot

        buy = oracle_bot.row(
            """
            SELECT created_at
            FROM trades
            WHERE market='crypto' AND symbol=%s AND side='BUY'
            ORDER BY created_at DESC
            LIMIT 1
            """,
            (symbol,),
        ) or {}
        created = _parse_time(buy.get("created_at"))
        if created is None:
            return None
        return max(0.0, (datetime.now(timezone.utc) - created).total_seconds() / 60.0)
    except Exception:
        log.exception("FAST REVERSAL HYSTERESIS | symbol=%s | entry_age_load=FAIL", symbol)
        return None


def _open_position_exists(symbol: str) -> bool:
    try:
        import oracle_bot

        position = oracle_bot.row(
            "SELECT symbol FROM positions WHERE market='crypto' AND symbol=%s",
            (symbol,),
        )
        return bool(position)
    except Exception:
        return False


def _bearish_evidence(signal: Any) -> tuple[int, list[str]]:
    evidence: list[str] = []
    if _safe_float(getattr(signal, "momentum_5d", 0.0)) <= -0.005:
        evidence.append("momentum_5d")
    if _safe_float(getattr(signal, "momentum_20d", 0.0)) <= -0.01:
        evidence.append("momentum_20d")
    if _safe_float(getattr(signal, "trend_strength", 0.0)) <= -0.005:
        evidence.append("trend")
    if _safe_float(getattr(signal, "macd_hist", 0.0)) < 0.0:
        evidence.append("macd")
    if _safe_float(getattr(signal, "rsi_14", 50.0), 50.0) >= 72.0:
        evidence.append("rsi_overbought")
    mr_side = str(getattr(signal, "mean_reversion_side", "") or "").upper().strip()
    mr_conf = _safe_float(getattr(signal, "mean_reversion_confidence", 0.0))
    if mr_side == "SELL" and mr_conf >= 0.60:
        evidence.append("mean_reversion")
    return len(evidence), evidence


def _should_suppress(signal: Any) -> tuple[bool, str]:
    symbol = str(getattr(signal, "symbol", "") or "").upper().strip()
    action = str(getattr(signal, "action", "") or "").upper().strip()
    if not symbol or action != "SELL":
        return False, "not_sell"
    if not _open_position_exists(symbol):
        return False, "no_open_position"

    age = _recent_entry_age_minutes(symbol)
    if age is None:
        return False, "unknown_entry_age"

    horizon = max(5.0, min(60.0, _safe_float(os.getenv("PAPER_FAST_REVERSAL_HYSTERESIS_MINUTES", "15"), 15.0)))
    if age >= horizon:
        return False, f"outside_hysteresis:{age:.2f}/{horizon:.2f}m"

    score = _safe_float(getattr(signal, "score", 0.5), 0.5)
    confidence = _safe_float(getattr(signal, "confidence", 0.5), 0.5)
    min_bearish = max(2, min(5, int(_safe_float(os.getenv("PAPER_FAST_REVERSAL_MIN_BEARISH_EVIDENCE", "3"), 3))))
    strong_score = max(0.25, min(0.47, _safe_float(os.getenv("PAPER_FAST_REVERSAL_STRONG_SELL_SCORE", "0.42"), 0.42)))
    strong_conf = max(0.55, min(0.95, _safe_float(os.getenv("PAPER_FAST_REVERSAL_STRONG_CONFIDENCE", "0.62"), 0.62)))
    count, evidence = _bearish_evidence(signal)

    # A fast SELL shortly after entry must be materially stronger than the narrow
    # paper-learning threshold that originally created the flip. Require a deep
    # bearish score, elevated confidence, and several independent bearish inputs.
    strong_reversal = score <= strong_score and confidence >= strong_conf and count >= min_bearish
    if strong_reversal:
        return False, (
            f"strong_reversal:score={score:.3f},confidence={confidence:.3f},"
            f"bearish={count},evidence={','.join(evidence)}"
        )
    return True, (
        f"weak_recent_reversal:age={age:.2f}m,score={score:.3f},confidence={confidence:.3f},"
        f"bearish={count},evidence={','.join(evidence) or 'none'}"
    )


def install_paper_fast_reversal_hysteresis(worker: Any) -> bool:
    """Downgrade weak fast SELL reversals after a fresh paper BUY to HOLD.

    This operates upstream of process_signals, so low-quality SELL noise is not
    persisted/routed as an execution candidate. risk_exits(), EXIT/CLOSE actions,
    and the existing downstream churn guard remain unchanged.
    """
    global _INSTALLED
    if _INSTALLED:
        return True
    if not _paper_only():
        return False

    original = getattr(worker, "_fast_discover_symbol", None)
    if not callable(original):
        raise RuntimeError("market worker has no _fast_discover_symbol")

    def wrapped_fast_discover_symbol(market: str, symbol: str, name: str):
        result = original(market, symbol, name)
        if result is None or not (_paper_only() and str(market or "").lower() == "crypto"):
            return result
        signal, history = result
        suppress, reason = _should_suppress(signal)
        if suppress:
            previous = str(getattr(signal, "action", "") or "").upper().strip()
            signal.action = "HOLD"
            signal.reason = (
                f"Fast reversal hysteresis suppressed {previous}: {reason}. "
                + str(getattr(signal, "reason", ""))
            ).strip()
            log.info(
                "FAST REVERSAL HYSTERESIS | symbol=%s | original=%s | action=HOLD | reason=%s | "
                "broker_submission=NONE | live_trading=DISARMED",
                str(symbol or "").upper(),
                previous,
                reason,
            )
        elif str(getattr(signal, "action", "") or "").upper().strip() == "SELL":
            log.info(
                "FAST REVERSAL HYSTERESIS | symbol=%s | action=SELL | reason=%s | "
                "broker_submission=NONE | live_trading=DISARMED",
                str(symbol or "").upper(),
                reason,
            )
        return signal, history

    worker._fast_discover_symbol = wrapped_fast_discover_symbol
    _INSTALLED = True
    log.info(
        "Installed paper fast reversal hysteresis | recent_entry_window=%sm | strong_sell_score<=%s | "
        "strong_confidence>=%s | broker_submission=NONE | live_trading=DISARMED",
        os.getenv("PAPER_FAST_REVERSAL_HYSTERESIS_MINUTES", "15"),
        os.getenv("PAPER_FAST_REVERSAL_STRONG_SELL_SCORE", "0.42"),
        os.getenv("PAPER_FAST_REVERSAL_STRONG_CONFIDENCE", "0.62"),
    )
    return True
