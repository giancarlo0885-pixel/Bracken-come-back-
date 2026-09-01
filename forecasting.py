from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import math
from typing import Any

import numpy as np
import pandas as pd

from asset_routing import infer_asset_class, normalize_symbol
from config import FORECAST_MODEL_VERSION
from crypto_predictor_v41 import predict_crypto_direction


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

CRYPTO_CAUSAL_MODEL = "crypto nested adaptive selector"
CRYPTO_CAUSAL_MODEL_VERSION = "v41-nested-selector"


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
        causal_prediction = predict_crypto_direction(history, bars)
        if causal_prediction is not None:
            selected_model = CRYPTO_CAUSAL_MODEL
            selected_version = CRYPTO_CAUSAL_MODEL_VERSION
        elif causal_requested:
            # Do not label a diffusion fallback as the causal model when the
            # nested selector lacks enough resolved evidence to make a forecast.
            return None

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
