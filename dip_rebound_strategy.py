from __future__ import annotations

from dataclasses import asdict, dataclass
import math
import os
from typing import Any

import pandas as pd

from technical_indicators import atr, rsi


DIP_LOOKBACK_BARS = max(6, int(os.getenv("DIP_REBOUND_LOOKBACK_BARS", "18")))
MIN_DIP_PCT = max(0.002, float(os.getenv("DIP_REBOUND_MIN_DIP_PCT", "0.012")))
MIN_REBOUND_PCT = max(0.001, float(os.getenv("DIP_REBOUND_MIN_REBOUND_PCT", "0.004")))
MAX_ENTRY_RSI = min(60.0, max(25.0, float(os.getenv("DIP_REBOUND_MAX_ENTRY_RSI", "48"))))
EXIT_RSI = min(90.0, max(55.0, float(os.getenv("DIP_REBOUND_EXIT_RSI", "68"))))
PROFIT_ATR_MULTIPLE = max(0.5, float(os.getenv("DIP_REBOUND_PROFIT_ATR_MULTIPLE", "1.6")))
TRAIL_ATR_MULTIPLE = max(0.25, float(os.getenv("DIP_REBOUND_TRAIL_ATR_MULTIPLE", "0.9")))
STOP_ATR_MULTIPLE = max(0.25, float(os.getenv("DIP_REBOUND_STOP_ATR_MULTIPLE", "0.8")))


@dataclass(frozen=True)
class DipReboundAssessment:
    available: bool
    side: str
    score: float
    confidence: float
    dip_depth_pct: float | None
    rebound_pct: float | None
    drawdown_from_recent_high_pct: float | None
    rsi_value: float | None
    rsi_change: float | None
    atr_pct: float | None
    reclaim_strength: float | None
    exit_rule: str
    reason: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _finite(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _clip(value: float, low: float = 0.0, high: float = 1.0) -> float:
    return max(low, min(high, value))


def _close(history: pd.DataFrame) -> pd.Series:
    if history is None or history.empty or "Close" not in history.columns:
        return pd.Series(dtype=float)
    value = history["Close"]
    if isinstance(value, pd.DataFrame):
        value = value.iloc[:, -1]
    return pd.to_numeric(value, errors="coerce").dropna()


def _paper_only_enabled(symbol: str) -> bool:
    crypto = str(symbol or "").upper().strip().endswith("-USD")
    return (
        crypto
        and str(os.getenv("EXECUTION_MODE", "paper") or "paper").strip().lower() == "paper"
        and str(os.getenv("PAPER_AUTONOMOUS_LEARNING", "false")).lower() == "true"
        and str(os.getenv("ENABLE_BROKER_SUBMISSION", "false")).lower() != "true"
        and str(os.getenv("LIVE_TRADING_ARMED", "false")).lower() != "true"
    )


def assess_dip_rebound(symbol: str, history: pd.DataFrame) -> DipReboundAssessment:
    """Detect a dip followed by observable rebound confirmation.

    This does not attempt to identify the eventual peak. A BUY requires both a
    meaningful recent drawdown and evidence that price has started reclaiming it.
    A SELL is an exit cue after a favorable rebound becomes extended. The actual
    paper execution layer remains responsible for verified quotes and fills.
    """
    if not _paper_only_enabled(symbol):
        return DipReboundAssessment(False, "HOLD", 0.0, 0.0, None, None, None, None, None, None, None, "ATR target + trailing exit", "paper crypto learning required")

    close = _close(history)
    if len(close) < max(30, DIP_LOOKBACK_BARS + 15):
        return DipReboundAssessment(False, "HOLD", 0.0, 0.0, None, None, None, None, None, None, None, "ATR target + trailing exit", "insufficient history")

    price = _finite(close.iloc[-1])
    if price is None or price <= 0:
        return DipReboundAssessment(False, "HOLD", 0.0, 0.0, None, None, None, None, None, None, None, "ATR target + trailing exit", "invalid current price")

    window = close.tail(DIP_LOOKBACK_BARS)
    recent_high = float(window.max())
    recent_low = float(window.min())
    low_pos = int(window.reset_index(drop=True).idxmin())
    bars_after_low = len(window) - 1 - low_pos
    if recent_high <= 0 or recent_low <= 0:
        return DipReboundAssessment(False, "HOLD", 0.0, 0.0, None, None, None, None, None, None, None, "ATR target + trailing exit", "invalid recent range")

    dip_depth = max(0.0, (recent_high - recent_low) / recent_high)
    rebound = max(0.0, (price - recent_low) / recent_low)
    drawdown = max(0.0, (recent_high - price) / recent_high)
    current_rsi = float(rsi(close, 14))
    previous_rsi = float(rsi(close.iloc[:-3], 14)) if len(close) >= 18 else 50.0
    rsi_change = current_rsi - previous_rsi
    atr_value = float(atr(history, 14)) if all(c in history.columns for c in ("High", "Low", "Close")) else 0.0
    atr_pct = atr_value / price if price > 0 else 0.0
    ema5 = float(close.ewm(span=5, adjust=False).mean().iloc[-1])
    ema12 = float(close.ewm(span=12, adjust=False).mean().iloc[-1])
    reclaim_strength = ((price / max(ema12, 1e-12)) - 1.0)

    dip_strength = _clip((dip_depth - MIN_DIP_PCT) / max(MIN_DIP_PCT * 2.0, 1e-9))
    rebound_strength = _clip((rebound - MIN_REBOUND_PCT) / max(MIN_REBOUND_PCT * 3.0, 1e-9))
    rsi_recovery = _clip((rsi_change + 2.0) / 12.0)
    reclaim = _clip((reclaim_strength + 0.002) / 0.012)
    score = _clip(0.35 * dip_strength + 0.30 * rebound_strength + 0.20 * rsi_recovery + 0.15 * reclaim)

    entry_confirmed = (
        dip_depth >= MIN_DIP_PCT
        and rebound >= MIN_REBOUND_PCT
        and bars_after_low >= 1
        and current_rsi <= MAX_ENTRY_RSI
        and rsi_change > 0.0
        and price >= ema5
    )

    # Exit only after a favorable rebound is extended; this deliberately does not
    # claim to locate the exact top. Paper trade accounting can then compare the
    # tested realized exit to alternatives after the fact.
    extension_from_low = rebound
    target_move = max(MIN_REBOUND_PCT * 2.0, atr_pct * PROFIT_ATR_MULTIPLE)
    exit_confirmed = (
        dip_depth >= MIN_DIP_PCT
        and extension_from_low >= target_move
        and current_rsi >= EXIT_RSI
        and price >= ema12
    )

    side = "BUY" if entry_confirmed else "SELL" if exit_confirmed else "HOLD"
    signed_score = score if side == "BUY" else -score if side == "SELL" else 0.0
    confidence = _clip(0.45 + 0.45 * score, 0.0, 0.92) if side != "HOLD" else 0.45
    exit_rule = (
        f"take favorable move near {PROFIT_ATR_MULTIPLE:.2f} ATR; "
        f"trail by {TRAIL_ATR_MULTIPLE:.2f} ATR; invalidate below dip by {STOP_ATR_MULTIPLE:.2f} ATR"
    )
    reason = (
        f"dip {dip_depth:.2%}, rebound {rebound:.2%}, drawdown {drawdown:.2%}, "
        f"RSI {current_rsi:.1f} ({rsi_change:+.1f}), reclaim {reclaim_strength:+.2%}; {side}."
    )
    return DipReboundAssessment(
        True, side, signed_score, confidence, dip_depth, rebound, drawdown,
        current_rsi, rsi_change, atr_pct, reclaim_strength, exit_rule, reason,
    )


def backtest_dip_rebound(symbol: str, history: pd.DataFrame, fee_bps: float = 10.0) -> dict[str, Any]:
    """Walk-forward paper test using only information available at each bar.

    Entry requires a confirmed dip/rebound. Exit captures a favorable move with a
    fixed ATR target, ATR trailing stop, or structural stop; the exact market peak
    is never used. Results are net of simulated round-trip fees.
    """
    close = _close(history)
    if len(close) < 60:
        return {"trades": 0, "net_return_pct": 0.0, "wins": 0, "losses": 0, "exit_reasons": {}}

    position: dict[str, float] | None = None
    completed: list[dict[str, Any]] = []
    for i in range(45, len(history)):
        window = history.iloc[: i + 1].copy()
        window.attrs.update(getattr(history, "attrs", {}) or {})
        assessment = assess_dip_rebound(symbol, window)
        px = float(_close(window).iloc[-1])
        atr_now = max(1e-12, float(assessment.atr_pct or 0.0) * px)
        if position is None and assessment.side == "BUY":
            position = {"entry": px, "peak": px, "atr": atr_now}
            continue
        if position is None:
            continue

        position["peak"] = max(position["peak"], px)
        target = position["entry"] + PROFIT_ATR_MULTIPLE * position["atr"]
        trail = position["peak"] - TRAIL_ATR_MULTIPLE * position["atr"]
        stop = position["entry"] - STOP_ATR_MULTIPLE * position["atr"]
        reason = None
        if px >= target:
            reason = "atr_profit_target"
        elif position["peak"] > position["entry"] and px <= trail:
            reason = "atr_trailing_exit"
        elif px <= stop:
            reason = "structural_stop"
        elif assessment.side == "SELL" and px > position["entry"]:
            reason = "extended_rebound_exit"
        if reason is None:
            continue

        gross = (px / position["entry"]) - 1.0
        net = gross - (2.0 * max(0.0, fee_bps) / 10_000.0)
        completed.append({"gross_return_pct": gross, "net_return_pct": net, "exit_reason": reason})
        position = None

    wins = sum(1 for trade in completed if trade["net_return_pct"] > 0)
    reasons: dict[str, int] = {}
    for trade in completed:
        reasons[trade["exit_reason"]] = reasons.get(trade["exit_reason"], 0) + 1
    return {
        "trades": len(completed),
        "net_return_pct": sum(trade["net_return_pct"] for trade in completed),
        "average_net_return_pct": (sum(trade["net_return_pct"] for trade in completed) / len(completed)) if completed else 0.0,
        "wins": wins,
        "losses": len(completed) - wins,
        "win_rate": wins / len(completed) if completed else 0.0,
        "exit_reasons": reasons,
    }
