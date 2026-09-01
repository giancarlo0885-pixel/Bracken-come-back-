from __future__ import annotations

from types import SimpleNamespace

import numpy as np
import pandas as pd

import capital_model_v42 as v42
import oracle_readiness
from crypto_predictor_v42 import predict_crypto_direction
from forecasting import CRYPTO_CAUSAL_MODEL, CRYPTO_CAUSAL_MODEL_VERSION, active_crypto_model_identity, forecast_price
from walk_forward_validation import temporal_leakage_probe


def _block_reversal_history(periods: int = 960, *, with_volume: bool = True) -> pd.DataFrame:
    block = 3
    returns: list[float] = []
    for index in range(periods):
        phase = (index // block) % 2
        magnitude = 0.0030 + 0.00025 * np.sin(index / 13.0)
        returns.append(magnitude if phase == 0 else -magnitude)
    prices = 100.0 * np.exp(np.cumsum(returns))
    prior = np.concatenate([[prices[0]], prices[:-1]])
    index = pd.date_range("2026-01-01", periods=periods, freq="5min", tz="UTC")
    payload = {
        "Open": prior,
        "High": np.maximum(prior, prices) * 1.001,
        "Low": np.minimum(prior, prices) * 0.999,
        "Close": prices,
    }
    if with_volume:
        payload["Volume"] = np.full(periods, 1_000_000.0)
    frame = pd.DataFrame(payload, index=index)
    frame.attrs["provider_route"] = {
        "requested_symbol": "BTC-USD",
        "provider_symbol": "BTC-USD",
        "provider": "unit",
        "interval": "5m",
        "quote_timestamp": index[-1].isoformat(),
        "quote_verified": True,
    }
    return frame


def test_short_horizon_forecast_uses_current_causal_model() -> None:
    history = _block_reversal_history()
    forecast = forecast_price(history, None, market="crypto", source_interval="5m", horizon_minutes=15)
    assert forecast is not None
    assert forecast.model == CRYPTO_CAUSAL_MODEL
    assert forecast.model_version == CRYPTO_CAUSAL_MODEL_VERSION
    assert forecast.horizon_bars == 3
    assert forecast.horizon_minutes == 15
    assert active_crypto_model_identity() == (CRYPTO_CAUSAL_MODEL, CRYPTO_CAUSAL_MODEL_VERSION)
    assert oracle_readiness._execution_models() == [active_crypto_model_identity()]


def test_v42_transition_estimator_recovers_known_reversal() -> None:
    history = _block_reversal_history(963)
    prediction = predict_crypto_direction(history, 3)
    assert prediction is not None
    assert prediction["selection_validation_samples"] >= 36
    assert prediction["selected_expert"] != "climatology"
    assert prediction["selection_brier_skill"] > 0.0
    assert prediction["current_lag_up"] is True
    assert prediction["probability_up"] < 0.5


def test_v42_prediction_does_not_require_optional_ohlcv_fields() -> None:
    history = _block_reversal_history(963, with_volume=False)[["Close"]].copy()
    prediction = predict_crypto_direction(history, 3)
    assert prediction is not None
    assert prediction["training_samples"] > 300
    assert 0.0 < prediction["probability_up"] < 1.0


def test_v42_future_mutation_probe_remains_causal() -> None:
    history = _block_reversal_history(980)
    result = temporal_leakage_probe(
        history,
        decision_position=820,
        horizon_bars=3,
        source_interval="5m",
        asset_class="crypto",
        market="crypto",
    )
    assert result["ok"] is True


def test_v42_evidence_runner_keeps_5m_to_15m_scope(monkeypatch) -> None:
    calls: list[dict] = []
    monkeypatch.setattr(v42, "_evidence_current", lambda *args: False)
    monkeypatch.setattr(v42, "register_model", lambda *args, **kwargs: None)
    monkeypatch.setattr(v42, "active_crypto_model_identity", lambda: ("crypto sign transition selector", "v42-sign-transition"))
    monkeypatch.setattr(
        v42,
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
    monkeypatch.setattr(v42, "apply_model_governance", lambda *args: SimpleNamespace(recommended_status="approved", eligible_for_approval=True))
    monkeypatch.setattr(v42, "_recent_evidence", lambda *args: {"run_count": 3, "distinct_symbols": 3, "calibration_samples": 360})

    result = v42.refresh_v42_crypto_evidence(force=True)
    assert result[0]["eligible_for_approval"] is True
    assert len(calls) == 3
    assert all(item["model"] == "crypto sign transition selector" for item in calls)
    assert all(item["version"] == "v42-sign-transition" for item in calls)
    assert all(item["period"] == "30d" for item in calls)
    assert all(item["interval"] == "5m" for item in calls)
    assert all(item["horizon_bars"] == 3 for item in calls)
    assert all(item["minimum_history_bars"] == 240 for item in calls)
    assert all(item["stride"] == 30 for item in calls)
