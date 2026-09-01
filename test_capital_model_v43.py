from __future__ import annotations

from types import SimpleNamespace

import numpy as np
import pandas as pd

import capital_model_v43 as v43
import forecasting
from crypto_predictor_v43 import predict_crypto_direction
from forecasting import CRYPTO_CAUSAL_MODEL, CRYPTO_CAUSAL_MODEL_VERSION, active_crypto_model_identity, forecast_price
from walk_forward_validation import temporal_leakage_probe


def _block_reversal_history(periods: int = 1200) -> pd.DataFrame:
    returns: list[float] = []
    for index in range(periods):
        phase = (index // 3) % 2
        magnitude = 0.0030 + 0.00025 * np.sin(index / 13.0)
        returns.append(magnitude if phase == 0 else -magnitude)
    prices = 100.0 * np.exp(np.cumsum(returns))
    index = pd.date_range("2026-01-01", periods=periods, freq="5min", tz="UTC")
    frame = pd.DataFrame({"Close": prices}, index=index)
    frame.attrs["provider_route"] = {
        "requested_symbol": "BTC-USD",
        "provider_symbol": "BTC-USD",
        "provider": "unit",
        "interval": "5m",
        "quote_timestamp": index[-1].isoformat(),
        "quote_verified": True,
    }
    return frame


def test_v43_active_identity_and_known_reversal() -> None:
    history = _block_reversal_history(1203)
    prediction = predict_crypto_direction(history, 3)
    assert prediction is not None
    assert prediction["selected_expert"]
    assert prediction["selection_brier_skill"] > 0.0
    assert prediction["selection_accuracy"] >= 0.52
    assert 0.40 <= prediction["selected_coverage"] <= 1.0
    assert prediction["current_lag_up"] is True
    assert prediction["probability_up"] < 0.5

    forecast = forecast_price(history, None, market="crypto", source_interval="5m", horizon_minutes=15)
    assert forecast is not None
    assert forecast.model == CRYPTO_CAUSAL_MODEL == "crypto selective sign transition"
    assert forecast.model_version == CRYPTO_CAUSAL_MODEL_VERSION == "v43-selective-transition"
    assert active_crypto_model_identity() == (CRYPTO_CAUSAL_MODEL, CRYPTO_CAUSAL_MODEL_VERSION)


def test_v43_abstention_never_falls_back_to_diffusion(monkeypatch) -> None:
    history = _block_reversal_history(500)
    monkeypatch.setattr(forecasting, "predict_crypto_direction", lambda *args, **kwargs: None)
    result = forecasting.forecast_price(history, None, market="crypto", source_interval="5m", horizon_minutes=15)
    assert result is None


def test_v43_future_mutation_probe_remains_causal() -> None:
    history = _block_reversal_history(1250)
    result = temporal_leakage_probe(
        history,
        decision_position=1050,
        horizon_bars=3,
        source_interval="5m",
        asset_class="crypto",
        market="crypto",
    )
    assert result["ok"] is True


def test_v43_evidence_runner_preserves_selective_sample_floor(monkeypatch) -> None:
    calls: list[dict] = []
    monkeypatch.setattr(v43, "_evidence_current", lambda *args: False)
    monkeypatch.setattr(v43, "register_model", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        v43,
        "_build_symbol_evidence",
        lambda symbol, model, version, **kwargs: calls.append({"symbol": symbol, "model": model, "version": version, **kwargs})
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
    monkeypatch.setattr(v43, "apply_model_governance", lambda *args: SimpleNamespace(recommended_status="approved", eligible_for_approval=True))
    monkeypatch.setattr(v43, "_recent_evidence", lambda *args: {"run_count": 3, "distinct_symbols": 3, "calibration_samples": 360})

    result = v43.refresh_v43_crypto_evidence(force=True)
    assert result[0]["eligible_for_approval"] is True
    assert len(calls) == 3
    assert all(item["period"] == "30d" for item in calls)
    assert all(item["interval"] == "5m" for item in calls)
    assert all(item["horizon_bars"] == 3 for item in calls)
    assert all(item["minimum_history_bars"] == 240 for item in calls)
    assert all(item["stride"] == 24 for item in calls)
