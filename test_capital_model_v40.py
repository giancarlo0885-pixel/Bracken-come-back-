from __future__ import annotations

from types import SimpleNamespace

import numpy as np
import pandas as pd

import capital_model_v40 as v40
import oracle_readiness
from crypto_forecast_runtime import install_crypto_short_horizon_forecast
from forecasting import (
    CRYPTO_CAUSAL_MODEL,
    CRYPTO_CAUSAL_MODEL_VERSION,
    active_crypto_model_identity,
    forecast_price,
)
from walk_forward_validation import temporal_leakage_probe


def _mean_reverting_crypto(periods: int = 700, interval: str = "5m") -> pd.DataFrame:
    phi = -0.72
    previous = 0.0
    returns: list[float] = []
    for index in range(periods):
        innovation = 0.0025 * np.sin(index / 7.0) + 0.0015 * np.cos(index / 19.0)
        current = phi * previous + innovation
        returns.append(float(current))
        previous = current
    prices = 100.0 * np.exp(np.cumsum(returns))
    freq = "5min" if interval == "5m" else "1D"
    index = pd.date_range("2026-01-01", periods=periods, freq=freq, tz="UTC")
    frame = pd.DataFrame(
        {
            "Open": prices,
            "High": prices * 1.001,
            "Low": prices * 0.999,
            "Close": prices,
            "Volume": np.full(periods, 1_000_000.0),
        },
        index=index,
    )
    frame.attrs["provider_route"] = {
        "requested_symbol": "BTC-USD",
        "provider_symbol": "BTC-USD",
        "provider": "unit",
        "interval": interval,
        "quote_timestamp": index[-1].isoformat(),
        "quote_verified": True,
    }
    return frame


def test_short_horizon_crypto_uses_causal_v40_but_daily_stays_diffusion() -> None:
    intraday = _mean_reverting_crypto(interval="5m")
    short = forecast_price(
        intraday,
        None,
        market="crypto",
        source_interval="5m",
        horizon_minutes=15,
    )
    assert short is not None
    assert short.model == CRYPTO_CAUSAL_MODEL
    assert short.model_version == CRYPTO_CAUSAL_MODEL_VERSION
    assert short.horizon_bars == 3
    assert short.horizon_minutes == 15
    assert 0.0 < short.probability_up < 1.0

    daily = _mean_reverting_crypto(interval="1d")
    long_horizon = forecast_price(daily, 5, market="crypto", source_interval="1d")
    assert long_horizon is not None
    assert long_horizon.model == "log-return diffusion"
    assert long_horizon.model_version != CRYPTO_CAUSAL_MODEL_VERSION


def test_v40_future_mutation_probe_is_causal() -> None:
    history = _mean_reverting_crypto(760, "5m")
    result = temporal_leakage_probe(
        history,
        decision_position=620,
        horizon_bars=3,
        source_interval="5m",
        asset_class="crypto",
        market="crypto",
    )
    assert result["ok"] is True


def test_crypto_runtime_maps_fast_intraday_forecast_to_15_minutes() -> None:
    calls: list[tuple[object, dict]] = []

    def original(history, days=5, *args, **kwargs):
        calls.append((days, dict(kwargs)))
        return "ok"

    worker = SimpleNamespace(forecast_price=original)
    assert install_crypto_short_horizon_forecast(worker) is True
    intraday = _mean_reverting_crypto(120, "5m")
    assert worker.forecast_price(intraday, 1, market="crypto", source_interval="5m") == "ok"
    assert calls[-1][0] is None
    assert calls[-1][1]["horizon_minutes"] == 15.0

    daily = _mean_reverting_crypto(120, "1d")
    assert worker.forecast_price(daily, 5, market="crypto", source_interval="1d") == "ok"
    assert calls[-1][0] == 5
    assert "horizon_minutes" not in calls[-1][1]


def test_readiness_only_requires_active_crypto_capital_model() -> None:
    assert oracle_readiness._execution_models() == [active_crypto_model_identity()]


def test_leakage_health_is_independent_from_performance(monkeypatch) -> None:
    monkeypatch.setattr(
        oracle_readiness,
        "rows",
        lambda *args, **kwargs: [
            {
                "run_id": "wf:pass",
                "leakage_checks": {
                    "strict_ordering": True,
                    "future_mutation_probe": {"ok": True},
                },
            }
        ],
    )
    result = oracle_readiness._leakage_for_model(*active_crypto_model_identity())
    assert result["ok"] is True
    assert result["status"] == "PASS"


def test_v40_evidence_runner_uses_exact_short_horizon_scope(monkeypatch) -> None:
    calls: list[dict] = []
    monkeypatch.setattr(v40, "_evidence_current", lambda *args: False)
    monkeypatch.setattr(v40, "register_model", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        v40,
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
        v40,
        "apply_model_governance",
        lambda *args: SimpleNamespace(recommended_status="approved", eligible_for_approval=True),
    )
    monkeypatch.setattr(
        v40,
        "_recent_evidence",
        lambda *args: {"run_count": 3, "distinct_symbols": 3, "calibration_samples": 360},
    )

    result = v40.refresh_v40_crypto_evidence(force=True)
    assert result[0]["eligible_for_approval"] is True
    assert len(calls) == 3
    assert {item["symbol"] for item in calls} == {"BTC-USD", "ETH-USD", "SOL-USD"}
    assert all(item["period"] == "30d" for item in calls)
    assert all(item["interval"] == "5m" for item in calls)
    assert all(item["horizon_bars"] == 3 for item in calls)
    assert all(item["minimum_history_bars"] == 240 for item in calls)
    assert all(item["stride"] == 32 for item in calls)
