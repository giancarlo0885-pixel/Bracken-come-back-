from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import math
from typing import Any

import numpy as np
import pandas as pd

from config import FORECAST_MODEL_VERSION
from asset_routing import infer_asset_class, normalize_symbol


INTERVAL_MINUTES = {
    "1m": 1,
    "2m": 2,
    "5m": 5,
    "15m": 15,
    "30m": 30,
    "60m": 60,
    "1h": 60,
    "90m": 90,
    "1d": 1440,
    "1wk": 10080,
    "1mo": 43200,
}

CRYPTO_CAUSAL_MODEL = "crypto causal adaptive"
CRYPTO_CAUSAL_MODEL_VERSION = "v40-causal-adaptive"


@dataclass
class Forecast:
    symbol: str = ""
    market: str = ""
    asset_class: str = "stock"
    horizon_days: float = 0.0
    target_price: float = 0.0
    low_price: float = 0.0
    high_price: float = 0.0
    probability_up: float = 0.0
    model: str = "log-return diffusion"
    requested_symbol: str = ""
    provider_symbol: str = ""
    source_interval: str = "1d"
    source_quote_timestamp: str = ""
    generated_at: str = ""
    horizon_bars: int = 0
    horizon_minutes: float = 0.0
    expected_move_pct: float = 0.0
    model_version: str = FORECAST_MODEL_VERSION
    data_quality_score: float = 0.0
    forecast_id: str = ""
    spot_price: float = 0.0
    volatility: float = 0.0
    regime: str = "unknown"
    validation_status: str = "unvalidated"
    bars_per_year: float = 252.0


def _series(frame: pd.DataFrame, column: str) -> pd.Series:
    value = frame[column]
    if isinstance(value, pd.DataFrame):
        value = value.iloc[:, -1]
    return pd.to_numeric(value, errors="coerce").dropna()


def interval_minutes(interval: Any) -> float:
    text = str(interval or "1d").lower().strip()
    if text in INTERVAL_MINUTES:
        return float(INTERVAL_MINUTES[text])
    try:
        if text.endswith("m"):
            return float(text[:-1])
        if text.endswith("h"):
            return float(text[:-1]) * 60.0
        if text.endswith("d"):
            return float(text[:-1]) * 1440.0
    except ValueError:
        pass
    return 1440.0


def bars_per_year(source_interval: Any, asset_class: str = "stock") -> float:
    minutes = max(1.0, interval_minutes(source_interval))
    if asset_class == "crypto":
        return 365.0 * 24.0 * 60.0 / minutes
    if minutes < 1440:
        regular_minutes = 390.0
        return 252.0 * regular_minutes / minutes
    if minutes >= 10080:
        return 52.0 if minutes < 43200 else 12.0
    return 252.0


def active_crypto_model_identity() -> tuple[str, str]:
    """Return the model identity that currently governs crypto capital evidence."""
    return CRYPTO_CAUSAL_MODEL, CRYPTO_CAUSAL_MODEL_VERSION


def _horizon(
    *,
    source_interval: str,
    asset_class: str,
    days: float | None,
    horizon_bars: int | None,
    horizon_minutes: float | None,
    horizon_hours: float | None,
    horizon_days: float | None,
) -> tuple[int, float, float]:
    minutes_per_bar = interval_minutes(source_interval)
    is_crypto = str(asset_class or "").lower() == "crypto"
    is_intraday = minutes_per_bar < 1440.0
    stock_session_minutes = 390.0
    requested_minutes = None
    if horizon_minutes is not None:
        requested_minutes = float(horizon_minutes)
    elif horizon_hours is not None:
        requested_minutes = float(horizon_hours) * 60.0
    elif horizon_days is not None:
        requested_minutes = float(horizon_days) * (1440.0 if is_crypto or not is_intraday else stock_session_minutes)
    elif days is not None:
        requested_minutes = float(days) * (1440.0 if is_crypto or not is_intraday else stock_session_minutes)

    if horizon_bars is None:
        if requested_minutes is None:
            requested_minutes = minutes_per_bar * 5.0
        horizon_bars = max(1, int(round(requested_minutes / minutes_per_bar)))
    else:
        horizon_bars = max(1, int(horizon_bars))

    total_minutes = float(horizon_bars) * minutes_per_bar
    if requested_minutes is not None and horizon_minutes is not None:
        total_minutes = max(minutes_per_bar, float(horizon_minutes))
        horizon_bars = max(1, int(round(total_minutes / minutes_per_bar)))
    minutes_per_horizon_day = 1440.0 if is_crypto or not is_intraday else stock_session_minutes
    return horizon_bars, total_minutes, total_minutes / minutes_per_horizon_day


def _quote_timestamp(history: pd.DataFrame) -> str:
    route = dict(getattr(history, "attrs", {}).get("provider_route") or {})
    timestamp = route.get("quote_timestamp")
    if timestamp:
        return str(timestamp)
    if len(history.index):
        stamp = pd.Timestamp(history.index[-1])
        if stamp.tzinfo is None:
            return stamp.isoformat()
        return stamp.to_pydatetime().astimezone(timezone.utc).isoformat()
    return ""


def _quality_score(close: pd.Series, returns: pd.Series) -> float:
    completeness = min(1.0, len(close) / 120.0)
    finite = close.map(lambda value: math.isfinite(float(value))).mean() if len(close) else 0.0
    volatility_ok = 1.0 if len(returns) and math.isfinite(float(returns.std() or 0.0)) else 0.0
    return round(max(0.0, min(100.0, 45.0 * completeness + 35.0 * finite + 20.0 * volatility_ok)), 2)


def _safe_sigmoid(value: float) -> float:
    value = max(-30.0, min(30.0, float(value)))
    return 1.0 / (1.0 + math.exp(-value))


def _weighted_mean(values: np.ndarray, weights: np.ndarray) -> float:
    denom = float(np.sum(weights))
    if denom <= 0:
        return float(np.mean(values))
    return float(np.sum(values * weights) / denom)


def _causal_crypto_features(close: pd.Series) -> pd.DataFrame:
    """Build features that are available at the decision bar and never from its future."""
    values = pd.to_numeric(close, errors="coerce")
    log_close = np.log(values.where(values > 0))
    r1 = log_close.diff(1)
    vol12 = r1.rolling(12, min_periods=8).std(ddof=0)
    vol48 = r1.rolling(48, min_periods=24).std(ddof=0)
    eps = 1e-9
    prior_mean20 = log_close.shift(1).rolling(20, min_periods=16).mean()
    prior_std20 = log_close.shift(1).rolling(20, min_periods=16).std(ddof=0)

    r1_z = r1 / (vol12 + eps)
    r3_z = log_close.diff(3) / (vol12 * math.sqrt(3.0) + eps)
    price_z20 = (log_close - prior_mean20) / (prior_std20 + eps)
    momentum16 = log_close.diff(16) / (vol48 * math.sqrt(16.0) + eps)
    momentum48 = log_close.diff(48) / (vol48 * math.sqrt(48.0) + eps)
    vol_ratio = (vol12 / (vol48 + eps)) - 1.0
    shock_curve = r1_z * r1_z.abs()

    return pd.DataFrame(
        {
            "r1_z": r1_z,
            "r3_z": r3_z,
            "price_z20": price_z20,
            "momentum16": momentum16,
            "momentum48": momentum48,
            "vol_ratio": vol_ratio,
            "shock_curve": shock_curve,
        },
        index=values.index,
    ).replace([np.inf, -np.inf], np.nan)


def _causal_crypto_prediction(close: pd.Series, horizon_bars: int) -> dict[str, float] | None:
    """Fit a regularized online classifier only on labels resolved by decision time."""
    horizon = max(1, int(horizon_bars))
    if close is None or len(close) < max(96, horizon + 64):
        return None

    numeric_close = pd.to_numeric(close, errors="coerce").dropna()
    numeric_close = numeric_close[numeric_close > 0]
    if len(numeric_close) < max(96, horizon + 64):
        return None

    features = _causal_crypto_features(numeric_close)
    log_close = np.log(numeric_close)
    forward_log_return = log_close.shift(-horizon) - log_close
    target_up = (forward_log_return > 0).astype(float)

    feature_columns = list(features.columns)
    training = features.copy()
    training["target_up"] = target_up
    training["forward_log_return"] = forward_log_return
    training = training.iloc[:-horizon].dropna()
    current = features.iloc[-1]
    if training.empty or current.isna().any() or len(training) < 80:
        return None

    training = training.tail(1600)
    x_raw = training[feature_columns].to_numpy(dtype=float)
    y = training["target_up"].to_numpy(dtype=float)
    y_return = training["forward_log_return"].to_numpy(dtype=float)
    if len(np.unique(y)) < 2:
        return None

    age = np.arange(len(training) - 1, -1, -1, dtype=float)
    half_life = max(96.0, min(512.0, len(training) / 2.0))
    weights = np.exp(-math.log(2.0) * age / half_life)
    weights = weights / max(float(np.mean(weights)), 1e-12)

    weight_sum = max(float(np.sum(weights)), 1e-12)
    means = np.sum(x_raw * weights[:, None], axis=0) / weight_sum
    variances = np.sum(((x_raw - means) ** 2) * weights[:, None], axis=0) / weight_sum
    scales = np.sqrt(np.maximum(variances, 1e-8))
    z = np.clip((x_raw - means) / scales, -8.0, 8.0)
    current_z = np.clip((current.to_numpy(dtype=float) - means) / scales, -8.0, 8.0)
    x = np.column_stack([np.ones(len(z)), z])
    x_current = np.concatenate([[1.0], current_z])

    base_rate = min(0.95, max(0.05, _weighted_mean(y, weights)))
    beta = np.zeros(x.shape[1], dtype=float)
    beta[0] = math.log(base_rate / (1.0 - base_rate))
    regularization = np.diag([0.05] + [3.5] * (x.shape[1] - 1))

    for _ in range(25):
        eta = np.clip(x @ beta, -25.0, 25.0)
        probability = 1.0 / (1.0 + np.exp(-eta))
        variance = np.maximum(probability * (1.0 - probability), 1e-6)
        gradient = x.T @ (weights * (y - probability)) - regularization @ beta
        hessian = x.T @ ((weights * variance)[:, None] * x) + regularization
        try:
            step = np.linalg.solve(hessian, gradient)
        except np.linalg.LinAlgError:
            return None
        beta += step
        if float(np.linalg.norm(step)) < 1e-6:
            break

    raw_probability = _safe_sigmoid(float(x_current @ beta))
    probability_up = base_rate + 0.75 * (raw_probability - base_rate)
    probability_up = min(0.95, max(0.05, probability_up))

    ridge = np.diag([0.05] + [8.0] * (x.shape[1] - 1))
    weighted_x = x * weights[:, None]
    try:
        return_beta = np.linalg.solve(x.T @ weighted_x + ridge, x.T @ (weights * y_return))
        predicted_log_return = float(x_current @ return_beta)
    except np.linalg.LinAlgError:
        predicted_log_return = 0.0

    recent_returns = np.log(numeric_close / numeric_close.shift(1)).dropna().tail(96)
    vol_per_bar = float(recent_returns.std(ddof=0)) if len(recent_returns) else 0.0
    if not math.isfinite(vol_per_bar) or vol_per_bar <= 0:
        return None
    statistical_move = (probability_up - 0.5) * 2.0 * vol_per_bar * math.sqrt(horizon) * 0.75
    predicted_log_return = 0.55 * predicted_log_return + 0.45 * statistical_move
    max_move = 3.0 * vol_per_bar * math.sqrt(horizon)
    predicted_log_return = max(-max_move, min(max_move, predicted_log_return))

    return {
        "probability_up": float(probability_up),
        "predicted_log_return": float(predicted_log_return),
        "vol_per_bar": float(vol_per_bar),
        "training_samples": float(len(training)),
    }


def forecast_price(
    history: pd.DataFrame,
    days: float | None = 5,
    *,
    source_interval: str | None = None,
    horizon_bars: int | None = None,
    horizon_minutes: float | None = None,
    horizon_hours: float | None = None,
    horizon_days: float | None = None,
    asset_class: str | None = None,
    market: str = "",
    model: str = "log-return diffusion",
    model_version: str = FORECAST_MODEL_VERSION,
) -> Forecast | None:
    if history is None or history.empty or len(history) < 40 or "Close" not in history.columns:
        return None
    route = dict(getattr(history, "attrs", {}).get("provider_route") or {})
    interval = str(source_interval or route.get("interval") or history.attrs.get("interval") or "1d")
    requested_symbol = normalize_symbol(route.get("requested_symbol") or history.attrs.get("requested_symbol") or "")
    provider_symbol = normalize_symbol(route.get("provider_symbol") or history.attrs.get("provider_symbol") or requested_symbol)
    asset = asset_class or infer_asset_class(requested_symbol, market)
    close = _series(history, "Close")
    if close.empty:
        return None
    spot = float(close.iloc[-1])
    if not math.isfinite(spot) or spot <= 0:
        return None
    returns = np.log(close / close.shift(1)).dropna()
    if len(returns) < 10:
        return None
    recent = returns.tail(min(180, max(30, len(returns))))
    drift_per_bar = float(recent.mean())
    vol_per_bar = float(recent.std())
    if not math.isfinite(drift_per_bar) or not math.isfinite(vol_per_bar):
        return None
    bars, minutes, calendar_days = _horizon(
        source_interval=interval,
        asset_class=asset,
        days=days,
        horizon_bars=horizon_bars,
        horizon_minutes=horizon_minutes,
        horizon_hours=horizon_hours,
        horizon_days=horizon_days,
    )

    selected_model = str(model or "log-return diffusion")
    selected_version = str(model_version or FORECAST_MODEL_VERSION)
    causal_prediction = None
    short_horizon_crypto = asset == "crypto" and interval_minutes(interval) <= 15.0 and minutes <= 30.0
    causal_requested = selected_model == CRYPTO_CAUSAL_MODEL
    if asset == "crypto" and (causal_requested or (selected_model == "log-return diffusion" and short_horizon_crypto)):
        causal_prediction = _causal_crypto_prediction(close, bars)
        if causal_prediction is not None:
            selected_model = CRYPTO_CAUSAL_MODEL
            selected_version = CRYPTO_CAUSAL_MODEL_VERSION

    if causal_prediction is not None:
        probability_up = float(causal_prediction["probability_up"])
        predicted_log_return = float(causal_prediction["predicted_log_return"])
        vol_per_bar = float(causal_prediction["vol_per_bar"])
        target = spot * math.exp(predicted_log_return)
    else:
        target = spot * math.exp((drift_per_bar - 0.5 * vol_per_bar**2) * bars)
        z = drift_per_bar * math.sqrt(bars) / (vol_per_bar + 1e-12)
        probability_up = float(1 / (1 + math.exp(-1.7 * z)))

    radius = vol_per_bar * math.sqrt(bars)
    low = target * math.exp(-1.645 * radius)
    high = target * math.exp(1.645 * radius)
    expected_move_pct = ((target / spot) - 1.0) * 100.0
    generated_at = datetime.now(timezone.utc).isoformat()
    source_quote_timestamp = _quote_timestamp(history)
    forecast_key = "|".join(
        [requested_symbol, provider_symbol, interval, source_quote_timestamp, str(bars), selected_model, selected_version, generated_at]
    )
    return Forecast(
        symbol=requested_symbol,
        market=str(market or "cash"),
        asset_class=asset,
        horizon_days=float(calendar_days),
        target_price=float(target),
        low_price=float(low),
        high_price=float(high),
        probability_up=probability_up,
        model=selected_model,
        requested_symbol=requested_symbol,
        provider_symbol=provider_symbol,
        source_interval=interval,
        source_quote_timestamp=source_quote_timestamp,
        generated_at=generated_at,
        horizon_bars=bars,
        horizon_minutes=float(minutes),
        expected_move_pct=float(expected_move_pct),
        model_version=selected_version,
        data_quality_score=_quality_score(close, returns),
        forecast_id=hashlib.sha256(forecast_key.encode("utf-8")).hexdigest()[:24],
        spot_price=spot,
        volatility=vol_per_bar,
        regime=str(route.get("regime") or "unknown"),
        validation_status="shadow",
        bars_per_year=bars_per_year(interval, asset),
    )
