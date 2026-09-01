from __future__ import annotations

from typing import Any

from forecasting import interval_minutes


CRYPTO_SHORT_HORIZON_MINUTES = 15.0


def install_crypto_short_horizon_forecast(worker: Any) -> bool:
    """Use a 15-minute causal forecast for crypto intraday fast-scan history.

    The market worker historically passed ``days=1`` even when the source was a
    five-minute crypto series. That made the forecast model answer a one-day
    question while the short-horizon strategy was acting on minute-scale
    dislocations. This wrapper only changes crypto histories whose bar size is
    15 minutes or less. Daily/deep and stock forecasts are untouched.
    """
    current = getattr(worker, "forecast_price", None)
    if current is None or getattr(current, "_oracle_crypto_short_horizon", False):
        return False

    def wrapped(history: Any, days: Any = 5, *args: Any, **kwargs: Any):
        market = str(kwargs.get("market") or "").strip().lower()
        source_interval = str(
            kwargs.get("source_interval")
            or (getattr(history, "attrs", {}).get("provider_route") or {}).get("interval")
            or getattr(history, "attrs", {}).get("interval")
            or "1d"
        )
        explicit_horizon = any(
            kwargs.get(name) is not None
            for name in ("horizon_bars", "horizon_minutes", "horizon_hours", "horizon_days")
        )
        if market == "crypto" and interval_minutes(source_interval) <= 15.0 and not explicit_horizon:
            return current(
                history,
                None,
                *args,
                **{**kwargs, "horizon_minutes": CRYPTO_SHORT_HORIZON_MINUTES},
            )
        return current(history, days, *args, **kwargs)

    wrapped._oracle_crypto_short_horizon = True
    wrapped._oracle_original = current
    worker.forecast_price = wrapped
    return True
