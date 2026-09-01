from __future__ import annotations

import math
from typing import Any

import numpy as np
import pandas as pd

from crypto_predictor_v42 import (
    _EPS,
    _accuracy,
    _brier,
    _candidate_predictions,
    _close,
    _recency_weights,
)


COVERAGE_LEVELS = (1.0, 0.80, 0.65, 0.50, 0.40)
MIN_INNER_DIRECTIONAL_ACCURACY = 0.52
MIN_INNER_BRIER_SKILL = 0.0


def _resolved_transition_table(close: pd.Series, horizon: int) -> tuple[pd.DataFrame, pd.Series]:
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
    table.loc[lag_return == 0.0, "lag_up"] = np.nan
    return table, lag_return


def _selective_candidate(
    selector_train: pd.DataFrame,
    selector_validation: pd.DataFrame,
    current_lag_up: float,
) -> dict[str, Any] | None:
    candidate_validation, candidate_current, diagnostics = _candidate_predictions(
        selector_train,
        selector_validation,
        current_lag_up,
    )
    if not diagnostics or "climatology" not in candidate_validation:
        return None

    outcomes = selector_validation["target_up"].to_numpy(dtype=float)
    weights = _recency_weights(len(selector_validation), max(24.0, len(selector_validation) / 2.0))
    baseline = candidate_validation["climatology"]
    minimum_accepted = max(24, int(math.ceil(0.35 * len(selector_validation))))
    choices: list[dict[str, Any]] = []

    for name, probability in candidate_validation.items():
        if name == "climatology":
            continue
        confidence = np.abs(probability - 0.5)
        for requested_coverage in COVERAGE_LEVELS:
            threshold = 0.0 if requested_coverage >= 1.0 else float(
                np.quantile(confidence, max(0.0, 1.0 - requested_coverage), method="lower")
            )
            accepted = confidence >= threshold - 1e-15
            accepted_count = int(accepted.sum())
            if accepted_count < minimum_accepted:
                continue
            selected_probability = probability[accepted]
            selected_outcomes = outcomes[accepted]
            selected_weights = weights[accepted]
            selected_baseline = baseline[accepted]
            brier = _brier(selected_probability, selected_outcomes, selected_weights)
            baseline_brier = _brier(selected_baseline, selected_outcomes, selected_weights)
            if baseline_brier <= _EPS:
                continue
            brier_skill = 1.0 - (brier / baseline_brier)
            accuracy = _accuracy(selected_probability, selected_outcomes, selected_weights)
            actual_coverage = accepted_count / len(selector_validation)
            if brier_skill <= MIN_INNER_BRIER_SKILL or accuracy < MIN_INNER_DIRECTIONAL_ACCURACY:
                continue
            # Prefer genuine probability skill first. Accuracy and coverage are
            # weak tie-breakers so the selector does not collapse to a tiny set.
            score = brier_skill + 0.02 * max(0.0, accuracy - 0.5) + 0.002 * actual_coverage
            choices.append(
                {
                    "name": name,
                    "threshold": threshold,
                    "requested_coverage": requested_coverage,
                    "coverage": actual_coverage,
                    "accepted_count": accepted_count,
                    "brier": brier,
                    "baseline_brier": baseline_brier,
                    "brier_skill": brier_skill,
                    "accuracy": accuracy,
                    "score": score,
                    "current_probability": float(candidate_current[name]),
                }
            )

    if not choices:
        return None
    return max(
        choices,
        key=lambda item: (
            item["score"],
            item["brier_skill"],
            item["accuracy"],
            item["coverage"],
        ),
    )


def predict_crypto_direction(history: pd.DataFrame, horizon_bars: int) -> dict[str, Any] | None:
    """Emit a crypto direction only when past-only selective evidence supports it.

    V43 is a coverage-constrained selective classifier. It first fits the causal
    v42 sign-transition candidates. An inner historical validation slice then
    selects both the estimator and a confidence threshold. Predictions are
    emitted only when the chosen model had positive Brier skill and >=52%
    directional accuracy on that past-only accepted subset and the current
    forecast clears the selected confidence threshold. Otherwise Oracle abstains.
    The outer capital-model walk-forward remains untouched and scores only the
    forecasts that would actually have been emitted at each historical timestamp.
    """
    horizon = max(1, int(horizon_bars))
    close = _close(history)
    if len(close) < max(180, horizon * 12):
        return None

    table, lag_return = _resolved_transition_table(close, horizon)
    resolved = table.iloc[:-horizon].dropna()
    if len(resolved) < 132:
        return None

    current_lag_return = float(lag_return.iloc[-1]) if pd.notna(lag_return.iloc[-1]) else 0.0
    if not math.isfinite(current_lag_return) or current_lag_return == 0.0:
        return None
    current_lag_up = 1.0 if current_lag_return > 0.0 else 0.0

    resolved = resolved.tail(2400)
    validation_size = min(240, max(60, len(resolved) // 4))
    split = len(resolved) - validation_size
    train_end = split - horizon
    if train_end < 96:
        return None
    selector_train = resolved.iloc[:train_end].copy()
    selector_validation = resolved.iloc[split:].copy()
    if len(selector_validation) < 48:
        return None

    selected = _selective_candidate(selector_train, selector_validation, current_lag_up)
    if selected is None:
        return None

    probability_up = float(np.clip(selected["current_probability"], 0.02, 0.98))
    current_confidence = abs(probability_up - 0.5)
    if current_confidence + 1e-15 < float(selected["threshold"]):
        return None

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
        "selected_expert": str(selected["name"]),
        "selection_brier_skill": float(selected["brier_skill"]),
        "selection_accuracy": float(selected["accuracy"]),
        "selected_coverage": float(selected["coverage"]),
        "confidence_threshold": float(selected["threshold"]),
        "current_confidence": float(current_confidence),
        "current_lag_up": bool(current_lag_up),
    }
