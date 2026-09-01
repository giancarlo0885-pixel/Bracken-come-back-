from __future__ import annotations

from types import SimpleNamespace

import numpy as np
import pandas as pd

import capital_model_v41 as v41
from crypto_predictor_v41 import predict_crypto_direction
from forecasting import CRYPTO_CAUSAL_MODEL, CRYPTO_CAUSAL_MODEL_VERSION, active_crypto_model_identity, forecast_price
from walk_forward_validation import temporal_leakage_probe


def _block_reversal_history(periods: int = 900) -> pd.DataFrame:
    returns: list[float] = []
    block = 3
    for index in range(periods):
        phase = (index // block) % 2
        magnitude = 0.0030 + 0.0004 * np.sin(index / 17.0)
        returns.append(magnitude if phase == 0 else -magnitude)
    prices = 100.0 * np.exp(np.cumsum(returns))
    previous = np.concatenate([[prices[0]], prices[:-1]])
    high = np.maximum(previous, prices) * 1.0008
    low = np.minimum(previous, prices) * 0.9992
    index = pd.date_range("2026-01-01", periods=periods, freq="5min", tz="UTC")
    frame = pd.DataFrame(
        {
            "Open": previous,
            "High": high,
            "Low": low,
            "Close": prices,
            "Volume": 1_000_000.0 * (1.0 + 0.10 * np.abs(np.sin(np.arange(periods) / 11.0))),
        },
        index=index,
    )
    frame.attrs["provider_route"] = {
        "requested_symbol": "BTC-USD",
        "provider_symbol": "BTC-USD",
        "provider": "unit",
        "interval": "5m",
        "quote_timestamp": index[-1].isoformat(),
        "quote_verified": True,
    }
    return frame


def test_v41_selector_uses_active_identity_and_short_horizon() -> None:
    history = _block_reversal_history()
    forecast = forecast_price(history, None, market="crypto", source_interval="5m", horizon_minutes=15)
    assert forecast is not None
    assert forecast.model == CRYPTO_CAUSAL_MODEL == "crypto nested adaptive selector"
    assert forecast.model_version == CRYPTO_CAUSAL_MODEL_VERSION == "v41-nested-selector"
    assert forecast.horizon_bars == 3
    assert forecast.horizon_minutes == 15
    assert active_crypto_model_identity() == (CRYPTO_CAUSAL_MODEL, CRYPTO_CAUSAL_MODEL_VERSION)


def test_v41_nested_selector_learns_reversal_without_outer_future() -> None:
    # End on a positive three-bar block. The constructed history repeatedly
    # reverses after each three-bar block, so a learned short-horizon predictor
    # should assign the next move more probability to down than up.
    history = _block_reversal_history(903)
    prediction = predict_crypto_direction(history, 3)
    assert prediction is not None
    assert prediction["selection_validation_samples"] >= 36
    assert prediction["selected_expert"] != "climatology"
    assert prediction["selection_brier_skill"] > 0.0
    assert prediction["probability_up"] < 0.5


def test_v41_future_mutation_probe_remains_causal() -> None:
    history = _block_reversal_history(940)
    result = temporal_leakage_probe(
        history,
        decision_position=780,
        horizon_bars=3,
        source_interval="5m",
        asset_class="crypto",
        market="crypto",
    )
    assert result["ok"] is True


def test_v41_evidence_runner_keeps_5m_to_15m_outer_scope(monkeypatch) -> None:
    calls: list[dict] = []
    monkeypatch.setattr(v41, "_evidence_current", lambda *args: False)
    monkeypatch.setattr(v41, "register_model", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        v41,
        "_build_symbol_evidence",
        lambda symbol, model, version, **kwargs: calls.append(
            {"symbol": symbol, "model": model, "version": version, **kwargs}
        )
        or {
            "symbol": symbol,
            "status": "PASS",
            "sample_count": 120,
            "directional_accuracy": 0.55,
            "expected_calibration_error": 0.05,
            "brier_skill_score": 0.03,
            "beats_all_baselines": True,
            "temporal_leakage_ok": True,
        },
    )
    monkeypatch.setattr(
        v41,
        "apply_model_governance",
        lambda *args: SimpleNamespace(recommended_status="approved", eligible_for_approval=True),
    )
    monkeypatch.setattr(
        v41,
        "_recent_evidence",
        lambda *args: {"run_count": 3, "distinct_symbols": 3, "calibration_samples": 360},
    )

    result = v41.refresh_v41_crypto_evidence(force=True)
    assert result[0]["eligible_for_approval"] is True
    assert len(calls) == 3
    assert all(item["period"] == "30d" for item in calls)
    assert all(item["interval"] == "5m" for item in calls)
    assert all(item["horizon_bars"] == 3 for item in calls)
    assert all(item["minimum_history_bars"] == 240 for item in calls)
    assert all(item["stride"] == 30 for item in calls)
