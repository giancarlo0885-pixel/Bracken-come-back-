from __future__ import annotations

import math
from typing import Any

import numpy as np
import pandas as pd


_EPS = 1e-9


def _series(history: pd.DataFrame, column: str, fallback: pd.Series | None = None) -> pd.Series:
    if column not in history.columns:
        if fallback is None:
            return pd.Series(index=history.index, dtype=float)
        return fallback.copy()
    value = history[column]
    if isinstance(value, pd.DataFrame):
        value = value.iloc[:, -1]
    return pd.to_numeric(value, errors="coerce")


def _sigmoid(value: Any) -> np.ndarray:
    array = np.asarray(value, dtype=float)
    return 1.0 / (1.0 + np.exp(-np.clip(array, -25.0, 25.0)))


def _logit(probability: float) -> float:
    p = min(1.0 - 1e-6, max(1e-6, float(probability)))
    return math.log(p / (1.0 - p))


def _weighted_mean(values: np.ndarray, weights: np.ndarray) -> float:
    denominator = max(float(np.sum(weights)), 1e-12)
    return float(np.sum(values * weights) / denominator)


def _recency_weights(length: int, half_life: float) -> np.ndarray:
    age = np.arange(length - 1, -1, -1, dtype=float)
    weights = np.exp(-math.log(2.0) * age / max(1.0, float(half_life)))
    return weights / max(float(np.mean(weights)), 1e-12)


def _feature_frame(history: pd.DataFrame, horizon: int) -> pd.DataFrame:
    close = _series(history, "Close")
    open_ = _series(history, "Open", close)
    high = _series(history, "High", close)
    low = _series(history, "Low", close)
    volume = _series(history, "Volume")

    positive_close = close.where(close > 0)
    positive_open = open_.where(open_ > 0)
    positive_high = high.where(high > 0)
    positive_low = low.where(low > 0)
    log_close = np.log(positive_close)
    r1 = log_close.diff(1)
    vol12 = r1.rolling(12, min_periods=8).std(ddof=0)
    vol48 = r1.rolling(48, min_periods=24).std(ddof=0)
    horizon_return = log_close.diff(horizon)

    prior_mean20 = log_close.shift(1).rolling(20, min_periods=16).mean()
    prior_std20 = log_close.shift(1).rolling(20, min_periods=16).std(ddof=0)
    range_log = np.log(positive_high / positive_low)
    range_scale = range_log.shift(1).rolling(48, min_periods=24).median()
    body = np.log(positive_close / positive_open)
    candle_range = (high - low).replace(0.0, np.nan)
    close_location = ((close - low) / candle_range) - 0.5

    features = pd.DataFrame(
        {
            "r1_z": r1 / (vol12 + _EPS),
            "horizon_return_z": horizon_return / (vol48 * math.sqrt(horizon) + _EPS),
            "price_z20": (log_close - prior_mean20) / (prior_std20 + _EPS),
            "momentum16": log_close.diff(16) / (vol48 * 4.0 + _EPS),
            "momentum48": log_close.diff(48) / (vol48 * math.sqrt(48.0) + _EPS),
            "vol_ratio": (vol12 / (vol48 + _EPS)) - 1.0,
            "body_z": body / (vol12 + _EPS),
            "range_ratio": (range_log / (range_scale + _EPS)) - 1.0,
            "close_location": close_location,
        },
        index=history.index,
    )

    if not volume.dropna().empty:
        log_volume = np.log(volume.where(volume > 0))
        volume_mean = log_volume.shift(1).rolling(48, min_periods=24).mean()
        volume_std = log_volume.shift(1).rolling(48, min_periods=24).std(ddof=0)
        features["volume_z"] = (log_volume - volume_mean) / (volume_std + _EPS)
    else:
        features["volume_z"] = 0.0

    return features.replace([np.inf, -np.inf], np.nan)


def _standardize(
    train: pd.DataFrame,
    validation: pd.DataFrame,
    current: pd.Series,
    feature_columns: list[str],
    weights: np.ndarray,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    train_raw = train[feature_columns].to_numpy(dtype=float)
    validation_raw = validation[feature_columns].to_numpy(dtype=float)
    current_raw = current[feature_columns].to_numpy(dtype=float)
    weight_sum = max(float(np.sum(weights)), 1e-12)
    means = np.sum(train_raw * weights[:, None], axis=0) / weight_sum
    variances = np.sum(((train_raw - means) ** 2) * weights[:, None], axis=0) / weight_sum
    scales = np.sqrt(np.maximum(variances, 1e-8))
    return (
        np.clip((train_raw - means) / scales, -8.0, 8.0),
        np.clip((validation_raw - means) / scales, -8.0, 8.0),
        np.clip((current_raw - means) / scales, -8.0, 8.0),
    )


def _fit_logistic(
    train_z: np.ndarray,
    outcomes: np.ndarray,
    weights: np.ndarray,
    base_rate: float,
) -> np.ndarray | None:
    x = np.column_stack([np.ones(len(train_z)), train_z])
    beta = np.zeros(x.shape[1], dtype=float)
    beta[0] = _logit(base_rate)
    regularization = np.diag([0.05] + [4.5] * (x.shape[1] - 1))
    for _ in range(30):
        eta = np.clip(x @ beta, -25.0, 25.0)
        probability = 1.0 / (1.0 + np.exp(-eta))
        variance = np.maximum(probability * (1.0 - probability), 1e-6)
        gradient = x.T @ (weights * (outcomes - probability)) - regularization @ beta
        hessian = x.T @ ((weights * variance)[:, None] * x) + regularization
        try:
            step = np.linalg.solve(hessian, gradient)
        except np.linalg.LinAlgError:
            return None
        beta += step
        if float(np.linalg.norm(step)) < 1e-6:
            break
    return beta


def _heuristic_probability(frame: pd.DataFrame, base_rate: float, kind: str) -> np.ndarray:
    hret = frame["horizon_return_z"].to_numpy(dtype=float)
    r1 = frame["r1_z"].to_numpy(dtype=float)
    price_z = frame["price_z20"].to_numpy(dtype=float)
    momentum16 = frame["momentum16"].to_numpy(dtype=float)
    body = frame["body_z"].to_numpy(dtype=float)
    close_location = frame["close_location"].to_numpy(dtype=float)
    range_ratio = np.clip(frame["range_ratio"].to_numpy(dtype=float), -2.0, 4.0)
    volume_z = np.clip(frame["volume_z"].to_numpy(dtype=float), -3.0, 3.0)

    if kind == "reversal":
        score = -0.95 * hret - 0.20 * r1 - 0.15 * price_z
    elif kind == "flow_reversal":
        intensity = 1.0 + 0.08 * np.maximum(0.0, range_ratio) + 0.04 * np.maximum(0.0, volume_z)
        score = intensity * (-0.85 * hret - 0.20 * body - 0.18 * close_location)
    elif kind == "momentum":
        score = 0.75 * hret + 0.25 * momentum16 + 0.10 * r1
    else:
        raise ValueError(f"unknown heuristic candidate: {kind}")

    raw = _sigmoid(_logit(base_rate) + 0.75 * score)
    # Keep heuristics calibrated toward the in-window base rate. The outer
    # governance layer is intentionally stricter and remains authoritative.
    return np.clip(base_rate + 0.72 * (raw - base_rate), 0.05, 0.95)


def _brier(probability: np.ndarray, outcomes: np.ndarray, weights: np.ndarray) -> float:
    return _weighted_mean((probability - outcomes) ** 2, weights)


def _accuracy(probability: np.ndarray, outcomes: np.ndarray, weights: np.ndarray) -> float:
    correct = ((probability >= 0.5) == (outcomes > 0.5)).astype(float)
    return _weighted_mean(correct, weights)


def predict_crypto_direction(history: pd.DataFrame, horizon_bars: int) -> dict[str, Any] | None:
    """Nested, past-only selector for short-horizon crypto direction.

    The outer Oracle walk-forward evaluation remains untouched. Inside each
    decision window this function reserves a recent historical validation slice,
    purges an embargo equal to the forecast horizon, compares heterogeneous
    predictors on recency-weighted Brier loss, and applies only the winner that
    demonstrated positive skill versus in-window climatology. Future bars are
    never used for fitting, candidate selection, calibration, or regime choice.
    """
    horizon = max(1, int(horizon_bars))
    if history is None or history.empty or "Close" not in history.columns or len(history) < max(180, horizon + 120):
        return None

    close = _series(history, "Close").dropna()
    close = close[close > 0]
    if len(close) != len(history):
        history = history.loc[close.index].copy()
    if len(history) < max(180, horizon + 120):
        return None

    features = _feature_frame(history, horizon)
    log_close = np.log(_series(history, "Close").where(_series(history, "Close") > 0))
    forward_log_return = log_close.shift(-horizon) - log_close
    target_up = (forward_log_return > 0).astype(float)

    training = features.copy()
    training["target_up"] = target_up
    training["forward_log_return"] = forward_log_return
    # Last horizon rows have unresolved labels at the decision time and are
    # excluded before any nested selection or fitting occurs.
    training = training.iloc[:-horizon].dropna()
    current = features.iloc[-1]
    if training.empty or current.isna().any() or len(training) < 140:
        return None

    training = training.tail(2000)
    validation_size = min(192, max(48, len(training) // 4))
    split = len(training) - validation_size
    train_end = split - horizon
    if train_end < 96:
        return None
    selector_train = training.iloc[:train_end].copy()
    selector_validation = training.iloc[split:].copy()
    if len(selector_validation) < 36:
        return None

    feature_columns = list(features.columns)
    train_weights = _recency_weights(len(selector_train), half_life=max(96.0, min(512.0, len(selector_train) / 2.0)))
    validation_weights = _recency_weights(len(selector_validation), half_life=max(32.0, len(selector_validation) / 2.0))
    y_train = selector_train["target_up"].to_numpy(dtype=float)
    y_validation = selector_validation["target_up"].to_numpy(dtype=float)
    if len(np.unique(y_train)) < 2 or len(np.unique(y_validation)) < 2:
        return None

    base_rate = min(0.90, max(0.10, _weighted_mean(y_train, train_weights)))
    train_z, validation_z, current_z = _standardize(
        selector_train,
        selector_validation,
        current,
        feature_columns,
        train_weights,
    )
    beta = _fit_logistic(train_z, y_train, train_weights, base_rate)

    candidates_validation: dict[str, np.ndarray] = {
        "climatology": np.full(len(selector_validation), base_rate, dtype=float),
        "reversal": _heuristic_probability(selector_validation, base_rate, "reversal"),
        "flow_reversal": _heuristic_probability(selector_validation, base_rate, "flow_reversal"),
        "momentum": _heuristic_probability(selector_validation, base_rate, "momentum"),
    }
    candidates_current: dict[str, float] = {
        "climatology": base_rate,
        "reversal": float(_heuristic_probability(current.to_frame().T, base_rate, "reversal")[0]),
        "flow_reversal": float(_heuristic_probability(current.to_frame().T, base_rate, "flow_reversal")[0]),
        "momentum": float(_heuristic_probability(current.to_frame().T, base_rate, "momentum")[0]),
    }

    if beta is not None:
        logistic_validation = _sigmoid(np.column_stack([np.ones(len(validation_z)), validation_z]) @ beta)
        logistic_current = float(_sigmoid(np.concatenate([[1.0], current_z]) @ beta))
        logistic_validation = np.clip(base_rate + 0.78 * (logistic_validation - base_rate), 0.05, 0.95)
        logistic_current = float(np.clip(base_rate + 0.78 * (logistic_current - base_rate), 0.05, 0.95))
        candidates_validation["logistic"] = logistic_validation
        candidates_current["logistic"] = logistic_current
        # If a learned relationship has changed polarity, the inverted expert
        # can win only by proving that fact on the nested, past-only validation
        # slice. This is not an after-the-fact flip of the outer result.
        inverted_validation = np.clip(base_rate - (logistic_validation - base_rate), 0.05, 0.95)
        inverted_current = float(np.clip(base_rate - (logistic_current - base_rate), 0.05, 0.95))
        candidates_validation["inverted_logistic"] = inverted_validation
        candidates_current["inverted_logistic"] = inverted_current

    diagnostics: dict[str, dict[str, float]] = {}
    for name, probability in candidates_validation.items():
        diagnostics[name] = {
            "brier": _brier(probability, y_validation, validation_weights),
            "accuracy": _accuracy(probability, y_validation, validation_weights),
        }

    climatology_brier = diagnostics["climatology"]["brier"]
    best_name = min(diagnostics, key=lambda name: (diagnostics[name]["brier"], -diagnostics[name]["accuracy"], name))
    best_brier = diagnostics[best_name]["brier"]
    selection_skill = 0.0 if climatology_brier <= 1e-12 else 1.0 - (best_brier / climatology_brier)

    if best_name == "climatology" or selection_skill <= 0.0:
        probability_up = base_rate
        best_name = "climatology"
        selection_skill = max(0.0, selection_skill)
    else:
        # Increase conviction only when the nested validation slice demonstrates
        # material skill. Small apparent improvements stay close to climatology.
        conviction = min(0.90, max(0.35, selection_skill / 0.06))
        probability_up = base_rate + conviction * (candidates_current[best_name] - base_rate)
    probability_up = float(np.clip(probability_up, 0.05, 0.95))

    recent_returns = np.log(_series(history, "Close") / _series(history, "Close").shift(1)).dropna().tail(144)
    vol_per_bar = float(recent_returns.std(ddof=0)) if len(recent_returns) else 0.0
    if not math.isfinite(vol_per_bar) or vol_per_bar <= 0:
        return None
    predicted_log_return = (probability_up - 0.5) * 2.0 * vol_per_bar * math.sqrt(horizon) * 0.70
    max_move = 2.5 * vol_per_bar * math.sqrt(horizon)
    predicted_log_return = float(np.clip(predicted_log_return, -max_move, max_move))

    return {
        "probability_up": probability_up,
        "predicted_log_return": predicted_log_return,
        "vol_per_bar": vol_per_bar,
        "training_samples": int(len(training)),
        "selection_train_samples": int(len(selector_train)),
        "selection_validation_samples": int(len(selector_validation)),
        "selected_expert": best_name,
        "selection_brier_skill": float(selection_skill),
        "candidate_diagnostics": diagnostics,
    }
