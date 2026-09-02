from __future__ import annotations

import math
from typing import Any

import numpy as np
import pandas as pd


MODEL_NAME = "crypto selective flow reversal"
MODEL_VERSION = "v45-flow-shadow"
SUPPORTED_SYMBOLS = frozenset({"BTC-USD", "ETH-USD"})
HORIZON_BARS = 3
MIN_VALIDATION_SAMPLES = 50
MIN_VALIDATION_ACCURACY = 0.52
MIN_VALIDATION_BRIER_SKILL = 0.0
MIN_PROBABILITY_EDGE = 0.07


def _symbol(value: Any) -> str:
    text = str(value or "").strip().upper().replace("/", "-")
    if text in {"BTCUSD", "BTCUSDT", "BTC-USDT"}:
        return "BTC-USD"
    if text in {"ETHUSD", "ETHUSDT", "ETH-USDT"}:
        return "ETH-USD"
    return text


def _column(frame: pd.DataFrame, *names: str) -> pd.Series | None:
    lookup = {str(column).lower(): column for column in frame.columns}
    for name in names:
        column = lookup.get(name.lower())
        if column is not None:
            return pd.to_numeric(frame[column], errors="coerce")
    return None


def _brier(probability: np.ndarray, outcome: np.ndarray) -> float:
    return float(np.mean((probability - outcome) ** 2)) if len(outcome) else math.inf


def _metrics(events: pd.DataFrame, reversal_probability: float, baseline_probability: float) -> dict[str, float]:
    if events.empty:
        return {"n": 0, "accuracy": 0.0, "brier_skill": -math.inf}
    lag = events["lag_return"].to_numpy(dtype=float)
    outcome = events["target_up"].to_numpy(dtype=float)
    probability = np.where(lag < 0.0, reversal_probability, 1.0 - reversal_probability)
    baseline = np.full(len(outcome), baseline_probability, dtype=float)
    brier = _brier(probability, outcome)
    baseline_brier = _brier(baseline, outcome)
    skill = 1.0 - brier / baseline_brier if baseline_brier > 1e-12 else -math.inf
    accuracy = float(np.mean((probability >= 0.5) == (outcome >= 0.5)))
    return {"n": int(len(outcome)), "accuracy": accuracy, "brier_skill": float(skill)}


def _event_rows(frame: pd.DataFrame, shock_threshold: float, flow_threshold: float, require_alignment: bool) -> pd.DataFrame:
    mask = (frame["shock"] >= shock_threshold) & (frame["flow"].abs() >= flow_threshold)
    if require_alignment:
        mask &= frame["alignment"] > 0.0
    return frame.loc[mask]


def predict_v45_flow_shadow(history: pd.DataFrame, symbol: str) -> dict[str, Any]:
    """Return a research-only V45 prediction or an explicit abstention.

    The function has no order/execution side effects. Configuration selection,
    probability calibration, and the final prediction use strictly ordered
    historical slices separated by a horizon embargo. Only BTC and ETH are
    eligible because current research evidence did not support SOL promotion.
    """
    normalized = _symbol(symbol)
    base = {
        "model": MODEL_NAME,
        "model_version": MODEL_VERSION,
        "symbol": normalized,
        "mode": "shadow",
        "execution_allowed": False,
        "horizon_bars": HORIZON_BARS,
    }
    if normalized not in SUPPORTED_SYMBOLS:
        return {**base, "status": "ABSTAIN", "reason": "symbol_not_validated"}
    if history is None or history.empty or len(history) < 1500:
        return {**base, "status": "ABSTAIN", "reason": "insufficient_history"}

    close = _column(history, "Close", "close", "c")
    quote_volume = _column(history, "QuoteVolume", "quote_volume", "qv")
    taker_quote = _column(history, "TakerBuyQuoteVolume", "taker_buy_quote_volume", "tbq")
    if close is None or quote_volume is None or taker_quote is None:
        return {**base, "status": "ABSTAIN", "reason": "missing_flow_fields"}

    log_close = np.log(close.where(close > 0.0))
    lag_return = log_close.diff(HORIZON_BARS)
    forward_return = log_close.shift(-HORIZON_BARS) - log_close
    rolling_quote = quote_volume.rolling(HORIZON_BARS).sum()
    rolling_taker = taker_quote.rolling(HORIZON_BARS).sum()
    flow = 2.0 * rolling_taker / (rolling_quote + 1e-12) - 1.0
    shock_scale = lag_return.abs().shift(1).rolling(288, min_periods=96).median()
    shock = lag_return.abs() / (shock_scale + 1e-12)
    table = pd.DataFrame(
        {
            "lag_return": lag_return,
            "target_up": (forward_return > 0.0).astype(float),
            "shock": shock,
            "flow": flow,
            "alignment": np.sign(lag_return) * flow,
        },
        index=history.index,
    ).replace([np.inf, -np.inf], np.nan)

    resolved = table.iloc[:-HORIZON_BARS].dropna()
    if len(resolved) < 1200:
        return {**base, "status": "ABSTAIN", "reason": "insufficient_resolved_history"}

    embargo = HORIZON_BARS
    calibration_size = max(240, len(resolved) // 6)
    validation_size = max(240, len(resolved) // 6)
    calibration_start = len(resolved) - calibration_size
    validation_start = calibration_start - embargo - validation_size
    train_end = validation_start - embargo
    if train_end < 600:
        return {**base, "status": "ABSTAIN", "reason": "insufficient_nested_history"}

    train = resolved.iloc[:train_end]
    validation = resolved.iloc[validation_start : validation_start + validation_size]
    calibration = resolved.iloc[calibration_start:]
    baseline_probability = float(train["target_up"].mean())
    best: tuple[float, float, float, bool, float, dict[str, float]] | None = None

    for shock_quantile in (0.50, 0.65, 0.75, 0.85):
        shock_threshold = float(train["shock"].quantile(shock_quantile))
        for flow_quantile in (0.30, 0.50, 0.65, 0.75):
            flow_threshold = float(train["flow"].abs().quantile(flow_quantile))
            for require_alignment in (True, False):
                train_events = _event_rows(train, shock_threshold, flow_threshold, require_alignment)
                if len(train_events) < 180:
                    continue
                reversal = ((train_events["target_up"] > 0.0) != (train_events["lag_return"] > 0.0)).astype(float)
                reversal_probability = float((reversal.sum() + 12.0) / (len(reversal) + 24.0))
                validation_events = _event_rows(validation, shock_threshold, flow_threshold, require_alignment)
                metrics = _metrics(validation_events, reversal_probability, baseline_probability)
                if metrics["n"] < MIN_VALIDATION_SAMPLES:
                    continue
                if metrics["accuracy"] < MIN_VALIDATION_ACCURACY or metrics["brier_skill"] <= MIN_VALIDATION_BRIER_SKILL:
                    continue
                score = metrics["brier_skill"] + 0.2 * (metrics["accuracy"] - 0.52)
                candidate = (score, shock_threshold, flow_threshold, require_alignment, reversal_probability, metrics)
                if best is None or candidate[0] > best[0]:
                    best = candidate

    if best is None:
        return {**base, "status": "ABSTAIN", "reason": "no_past_only_configuration_passed"}

    _, shock_threshold, flow_threshold, require_alignment, prior_reversal, validation_metrics = best
    calibration_events = _event_rows(calibration, shock_threshold, flow_threshold, require_alignment)
    if len(calibration_events) < 15:
        return {**base, "status": "ABSTAIN", "reason": "insufficient_calibration_events"}
    calibration_reversal = ((calibration_events["target_up"] > 0.0) != (calibration_events["lag_return"] > 0.0)).astype(float)
    reversal_probability = float((calibration_reversal.sum() + 24.0 * prior_reversal) / (len(calibration_reversal) + 24.0))
    if abs(reversal_probability - 0.5) < MIN_PROBABILITY_EDGE:
        return {**base, "status": "ABSTAIN", "reason": "calibrated_edge_too_small"}

    current = table.iloc[-1]
    if current.isna().any():
        return {**base, "status": "ABSTAIN", "reason": "current_features_unavailable"}
    event_ok = bool(current["shock"] >= shock_threshold and abs(current["flow"]) >= flow_threshold)
    if require_alignment:
        event_ok = event_ok and bool(current["alignment"] > 0.0)
    if not event_ok:
        return {**base, "status": "ABSTAIN", "reason": "current_event_not_selected"}

    probability_up = reversal_probability if current["lag_return"] < 0.0 else 1.0 - reversal_probability
    return {
        **base,
        "status": "PREDICT",
        "reason": "past_only_flow_configuration_passed",
        "probability_up": float(np.clip(probability_up, 0.02, 0.98)),
        "validation_samples": int(validation_metrics["n"]),
        "validation_accuracy": float(validation_metrics["accuracy"]),
        "validation_brier_skill": float(validation_metrics["brier_skill"]),
        "calibration_events": int(len(calibration_events)),
        "shock_threshold": float(shock_threshold),
        "flow_threshold": float(flow_threshold),
        "require_alignment": bool(require_alignment),
    }
