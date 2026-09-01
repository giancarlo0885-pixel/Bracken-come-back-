from __future__ import annotations

import math
from typing import Any

import numpy as np
import pandas as pd


_EPS = 1e-12


def _close(history: pd.DataFrame) -> pd.Series:
    if history is None or history.empty or "Close" not in history.columns:
        return pd.Series(dtype=float)
    close = history["Close"]
    if isinstance(close, pd.DataFrame):
        close = close.iloc[:, -1]
    close = pd.to_numeric(close, errors="coerce").dropna()
    return close[close > 0]


def _recency_weights(length: int, half_life: float) -> np.ndarray:
    age = np.arange(length - 1, -1, -1, dtype=float)
    weights = np.exp(-math.log(2.0) * age / max(1.0, float(half_life)))
    return weights / max(float(np.mean(weights)), _EPS)


def _weighted_mean(values: np.ndarray, weights: np.ndarray) -> float:
    denominator = max(float(np.sum(weights)), _EPS)
    return float(np.sum(values * weights) / denominator)


def _posterior_probability(
    outcomes: np.ndarray,
    weights: np.ndarray,
    *,
    prior_mean: float,
    prior_strength: float,
) -> float:
    prior = min(0.99, max(0.01, float(prior_mean)))
    strength = max(0.0, float(prior_strength))
    successes = float(np.sum(weights * outcomes))
    total = float(np.sum(weights))
    probability = (successes + prior * strength) / max(total + strength, _EPS)
    return float(np.clip(probability, 0.02, 0.98))


def _transition_statistics(
    training: pd.DataFrame,
    *,
    half_life: float,
    prior_strength: float,
) -> dict[str, float] | None:
    if training is None or len(training) < 48:
        return None
    weights = _recency_weights(len(training), half_life)
    y = training["target_up"].to_numpy(dtype=float)
    lag_up = training["lag_up"].to_numpy(dtype=float)
    base_rate = _posterior_probability(y, weights, prior_mean=0.5, prior_strength=prior_strength)

    stats: dict[str, float] = {"base_rate": base_rate}
    for state, mask in (("after_up", lag_up > 0.5), ("after_down", lag_up <= 0.5)):
        if int(mask.sum()) < 12:
            stats[state] = base_rate
            continue
        stats[state] = _posterior_probability(
            y[mask],
            weights[mask],
            prior_mean=base_rate,
            prior_strength=prior_strength,
        )

    reversal = (y != lag_up).astype(float)
    stats["reversal_rate"] = _posterior_probability(
        reversal,
        weights,
        prior_mean=0.5,
        prior_strength=prior_strength,
    )
    return stats


def _conditional_probability(stats: dict[str, float], lag_up: np.ndarray) -> np.ndarray:
    return np.where(lag_up > 0.5, stats["after_up"], stats["after_down"]).astype(float)


def _symmetric_probability(stats: dict[str, float], lag_up: np.ndarray) -> np.ndarray:
    reversal_rate = float(stats["reversal_rate"])
    # If the previous horizon was up, an up next horizon is a continuation;
    # if it was down, an up next horizon is a reversal.
    return np.where(lag_up > 0.5, 1.0 - reversal_rate, reversal_rate).astype(float)


def _brier(probability: np.ndarray, outcomes: np.ndarray, weights: np.ndarray) -> float:
    return _weighted_mean((probability - outcomes) ** 2, weights)


def _accuracy(probability: np.ndarray, outcomes: np.ndarray, weights: np.ndarray) -> float:
    correct = ((probability >= 0.5) == (outcomes > 0.5)).astype(float)
    return _weighted_mean(correct, weights)


def _candidate_predictions(
    training: pd.DataFrame,
    validation: pd.DataFrame,
    current_lag_up: float,
) -> tuple[dict[str, np.ndarray], dict[str, float], dict[str, dict[str, float]]]:
    y_validation = validation["target_up"].to_numpy(dtype=float)
    validation_lag = validation["lag_up"].to_numpy(dtype=float)
    validation_weights = _recency_weights(len(validation), max(24.0, len(validation) / 2.0))

    full_stats = _transition_statistics(
        training,
        half_life=max(96.0, min(640.0, len(training) / 2.0)),
        prior_strength=24.0,
    )
    if full_stats is None:
        return {}, {}, {}

    recent = training.tail(min(384, len(training)))
    recent_stats = _transition_statistics(
        recent,
        half_life=max(48.0, min(192.0, len(recent) / 2.0)),
        prior_strength=18.0,
    ) or full_stats

    shorter = training.tail(min(192, len(training)))
    short_stats = _transition_statistics(
        shorter,
        half_life=max(32.0, min(96.0, len(shorter) / 2.0)),
        prior_strength=16.0,
    ) or recent_stats

    candidate_validation: dict[str, np.ndarray] = {
        "climatology": np.full(len(validation), full_stats["base_rate"], dtype=float),
        "conditional_full": _conditional_probability(full_stats, validation_lag),
        "conditional_recent": _conditional_probability(recent_stats, validation_lag),
        "conditional_short": _conditional_probability(short_stats, validation_lag),
        "symmetric_full": _symmetric_probability(full_stats, validation_lag),
        "symmetric_recent": _symmetric_probability(recent_stats, validation_lag),
        "symmetric_short": _symmetric_probability(short_stats, validation_lag),
    }

    current = np.asarray([float(current_lag_up)], dtype=float)
    candidate_current = {
        "climatology": float(full_stats["base_rate"]),
        "conditional_full": float(_conditional_probability(full_stats, current)[0]),
        "conditional_recent": float(_conditional_probability(recent_stats, current)[0]),
        "conditional_short": float(_conditional_probability(short_stats, current)[0]),
        "symmetric_full": float(_symmetric_probability(full_stats, current)[0]),
        "symmetric_recent": float(_symmetric_probability(recent_stats, current)[0]),
        "symmetric_short": float(_symmetric_probability(short_stats, current)[0]),
    }

    diagnostics: dict[str, dict[str, float]] = {}
    for name, probability in candidate_validation.items():
        diagnostics[name] = {
            "brier": _brier(probability, y_validation, validation_weights),
            "accuracy": _accuracy(probability, y_validation, validation_weights),
        }
    return candidate_validation, candidate_current, diagnostics


def predict_crypto_direction(history: pd.DataFrame, horizon_bars: int) -> dict[str, Any] | None:
    """Estimate next-horizon crypto direction from causal sign transitions.

    The model intentionally targets the phenomenon measured in recent 15-minute
    crypto research: whether the sign of the next horizon tends to reverse or
    continue the sign of the previous horizon. It avoids hand-weighted move
    magnitudes. At every decision timestamp, unresolved labels are purged, an
    inner historical validation slice is separated by a horizon-length embargo,
    and the transition estimator is selected on past-only Brier loss.
    """
    horizon = max(1, int(horizon_bars))
    close = _close(history)
    if len(close) < max(180, horizon * 12):
        return None

    log_close = np.log(close)
    lag_return = log_close.diff(horizon)
    forward_return = log_close.shift(-horizon) - log_close
    table = pd.DataFrame(
        {
            "lag_up": (lag_return > 0.0).astype(float),
            "target_up": (forward_return > 0.0).astype(float),
            "forward_return": forward_return,
        },
        index=close.index,
    )
    # A zero lag move has no meaningful sign. Exclude it rather than choosing a
    # direction by convention. The final horizon rows have unresolved outcomes.
    table.loc[lag_return == 0.0, "lag_up"] = np.nan
    resolved = table.iloc[:-horizon].dropna()
    if len(resolved) < 132:
        return None

    current_lag_return = float(lag_return.iloc[-1]) if pd.notna(lag_return.iloc[-1]) else 0.0
    if not math.isfinite(current_lag_return) or current_lag_return == 0.0:
        return None
    current_lag_up = 1.0 if current_lag_return > 0.0 else 0.0

    resolved = resolved.tail(2400)
    validation_size = min(240, max(48, len(resolved) // 4))
    split = len(resolved) - validation_size
    train_end = split - horizon
    if train_end < 96:
        return None
    selector_train = resolved.iloc[:train_end].copy()
    selector_validation = resolved.iloc[split:].copy()
    if len(selector_validation) < 36:
        return None

    candidate_validation, candidate_current, diagnostics = _candidate_predictions(
        selector_train,
        selector_validation,
        current_lag_up,
    )
    if not diagnostics:
        return None

    climatology_brier = diagnostics["climatology"]["brier"]
    best_name = min(
        diagnostics,
        key=lambda name: (diagnostics[name]["brier"], -diagnostics[name]["accuracy"], name),
    )
    best_brier = diagnostics[best_name]["brier"]
    selection_skill = 0.0 if climatology_brier <= _EPS else 1.0 - (best_brier / climatology_brier)

    base_rate = float(candidate_current["climatology"])
    if best_name == "climatology" or selection_skill <= 0.0:
        probability_up = base_rate
        best_name = "climatology"
        selection_skill = max(0.0, selection_skill)
    else:
        # The selector already uses Bayesian-shrunk transition probabilities.
        # Further shrink toward climatology unless the inner validation slice
        # demonstrates meaningful Brier skill, reducing regime-chasing noise.
        reliability = min(1.0, max(0.35, selection_skill / 0.04))
        probability_up = base_rate + reliability * (candidate_current[best_name] - base_rate)
    probability_up = float(np.clip(probability_up, 0.02, 0.98))

    one_bar_returns = np.log(close / close.shift(1)).dropna().tail(192)
    vol_per_bar = float(one_bar_returns.std(ddof=0)) if len(one_bar_returns) else 0.0
    if not math.isfinite(vol_per_bar) or vol_per_bar <= 0.0:
        return None
    predicted_log_return = (probability_up - 0.5) * 2.0 * vol_per_bar * math.sqrt(horizon) * 0.65
    maximum_move = 2.0 * vol_per_bar * math.sqrt(horizon)
    predicted_log_return = float(np.clip(predicted_log_return, -maximum_move, maximum_move))

    return {
        "probability_up": probability_up,
        "predicted_log_return": predicted_log_return,
        "vol_per_bar": vol_per_bar,
        "training_samples": int(len(resolved)),
        "selection_train_samples": int(len(selector_train)),
        "selection_validation_samples": int(len(selector_validation)),
        "selected_expert": best_name,
        "selection_brier_skill": float(selection_skill),
        "current_lag_up": bool(current_lag_up),
        "candidate_diagnostics": diagnostics,
    }
