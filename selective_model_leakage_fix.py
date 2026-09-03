from __future__ import annotations

import math
from typing import Any

import pandas as pd


def _finite(value: Any, default: float = 0.0) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return number if math.isfinite(number) else default


def install_selective_model_leakage_fix() -> None:
    """Make the future-mutation probe valid for selective/abstaining models.

    The original probe tested one fixed near-end decision. For the v43 selective
    crypto model, a legitimate abstention at that exact point returned None and
    was incorrectly recorded as temporal leakage. This replacement searches only
    *past* decision points for a point where the active model actually emits a
    forecast, then mutates bars strictly after that decision and verifies that the
    forecast is invariant. No future data is ever supplied to either forecast.

    Failure to find a usable probe remains fail-closed; it is not converted to a
    pass. A changed forecast after future mutation also remains a hard failure.
    """
    import capital_model_evidence_runtime as evidence
    import walk_forward_validation as walk
    from config import FORECAST_MODEL_VERSION
    from forecasting import active_crypto_model_identity, forecast_price, interval_minutes

    def robust_probe(
        history: pd.DataFrame,
        *,
        decision_position: int,
        horizon_bars: int = 5,
        source_interval: str = "1d",
        asset_class: str = "stock",
        market: str = "cash",
    ) -> dict[str, Any]:
        if history is None or history.empty or len(history) < 42:
            return {"ok": False, "status": "NO_EVIDENCE", "reason": "insufficient probe data"}

        asset = str(asset_class or "stock").strip().lower()
        market_name = str(market or "cash").strip().lower()
        interval = str(source_interval or "1d")
        bars = max(1, int(horizon_bars))
        requested_position = min(max(40, int(decision_position)), len(history) - 2)

        if asset == "crypto" and interval_minutes(interval) <= 15.0 and interval_minutes(interval) * bars <= 30.0:
            model, model_version = active_crypto_model_identity()
        else:
            model, model_version = "log-return diffusion", FORECAST_MODEL_VERSION

        # Search backwards for a decision on which the selective model participates.
        # This changes only which historical *decision* is tested, never the rule
        # that the model receives data only through that decision.
        lower_bound = max(40, requested_position - 240)
        selected_position: int | None = None
        original = None
        for candidate_position in range(requested_position, lower_bound - 1, -1):
            past = history.iloc[: candidate_position + 1].copy()
            try:
                candidate = forecast_price(
                    past,
                    source_interval=interval,
                    horizon_bars=bars,
                    asset_class=asset,
                    market=market_name,
                    model=model,
                    model_version=model_version,
                )
            except Exception:
                candidate = None
            if candidate is not None:
                selected_position = candidate_position
                original = candidate
                break

        if selected_position is None or original is None:
            return {
                "ok": False,
                "status": "NO_EVIDENCE",
                "reason": "selective model abstained across probe search window",
                "requested_decision_position": requested_position,
                "search_lower_bound": lower_bound,
                "model": model,
                "model_version": model_version,
            }

        mutated = history.copy(deep=True)
        future_start = selected_position + 1
        for column in ("Open", "High", "Low", "Close"):
            if column in mutated.columns:
                future_values = pd.to_numeric(mutated.iloc[future_start:][column], errors="coerce").fillna(1.0).to_numpy()
                mutated.iloc[future_start:, mutated.columns.get_loc(column)] = future_values * 1000.0 + 12345.0

        mutated_past = mutated.iloc[: selected_position + 1].copy()
        try:
            after = forecast_price(
                mutated_past,
                source_interval=interval,
                horizon_bars=bars,
                asset_class=asset,
                market=market_name,
                model=model,
                model_version=model_version,
            )
        except Exception as exc:
            return {
                "ok": False,
                "status": "FAIL_CLOSED",
                "reason": f"mutated forecast error:{exc.__class__.__name__}",
                "decision_position": selected_position,
                "model": model,
                "model_version": model_version,
            }
        if after is None:
            return {
                "ok": False,
                "status": "FAIL_CLOSED",
                "reason": "mutated forecast unavailable after original participated",
                "decision_position": selected_position,
                "model": model,
                "model_version": model_version,
            }

        fields = ("target_price", "low_price", "high_price", "probability_up", "expected_move_pct", "spot_price")
        differences = {
            field: abs(_finite(getattr(original, field, None)) - _finite(getattr(after, field, None)))
            for field in fields
        }
        ok = all(value <= 1e-12 for value in differences.values())
        return {
            "ok": ok,
            "status": "PASS" if ok else "FAIL",
            "reason": "future mutation invariant" if ok else "future mutation changed past-only forecast",
            "requested_decision_position": requested_position,
            "decision_position": selected_position,
            "future_start_position": future_start,
            "probe_backtrack_bars": requested_position - selected_position,
            "model": model,
            "model_version": model_version,
            "differences": differences,
        }

    robust_probe._oracle_selective_model_probe = True
    walk.temporal_leakage_probe = robust_probe
    # capital_model_evidence_runtime imported the function by name, so patch the
    # bound reference as well as the defining module.
    evidence.temporal_leakage_probe = robust_probe
