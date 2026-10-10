"""Preserve selective forecast abstentions and attribute unavailable exit evidence."""
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch
import ast
import math

import numpy as np
import pandas as pd

import forecasting
from paper_shadow_exit_challenger import _trigger_telemetry


def _history(n: int) -> pd.DataFrame:
    frame = pd.DataFrame({"Close": 100.0 + np.linspace(0.0, 2.0, n)})
    frame.attrs["provider_route"] = {"requested_symbol": "BTC-USD", "provider_symbol": "BTC-USD"}
    return frame


def test_missing_history_reports_reason_without_forecast():
    diagnostics = {"unavailable_reason": "stale"}
    assert forecasting.forecast_price(_history(20), market="crypto", diagnostics=diagnostics) is None
    assert diagnostics["unavailable_reason"] == "insufficient_history_or_close"


def test_causal_abstention_never_falls_back_to_diffusion():
    diagnostics = {}
    with patch.object(forecasting, "predict_crypto_direction", return_value=None) as predictor:
        result = forecasting.forecast_price(
            _history(90), days=None, market="crypto", source_interval="1m",
            horizon_minutes=15, diagnostics=diagnostics
        )
    assert result is None
    assert predictor.call_count == 1
    assert diagnostics["unavailable_reason"] == "causal_model_abstention"


def test_unsupported_causal_horizon_retains_fail_closed_result():
    diagnostics = {}
    result = forecasting.forecast_price(
        _history(90), market="crypto", source_interval="1m",
        model=forecasting.CRYPTO_CAUSAL_MODEL, days=1, diagnostics=diagnostics
    )
    assert result is None
    assert diagnostics["unavailable_reason"] == "unsupported_causal_horizon"


def test_valid_diffusion_forecast_clears_old_diagnostic():
    diagnostics = {"unavailable_reason": "stale"}
    result = forecasting.forecast_price(_history(90), market="crypto", source_interval="1d",
                                         days=1, diagnostics=diagnostics)
    assert result is not None
    assert "unavailable_reason" not in diagnostics
    assert math.isfinite(result.expected_move_pct)
    assert 0 <= result.probability_up <= 1


def test_entry_forecast_never_substitutes_for_missing_current_forecast():
    exclusions = {}
    now = datetime.now(timezone.utc).isoformat()
    signal = {
        "id": 101,
        "action": "HOLD",
        "expected_edge_pct": None,
        "payload": {
            "quote_verified": True,
            "quote_timestamp": now,
            "price": 101.0,
            "forecast_unavailable_reason": "causal_model_abstention",
        },
    }
    position = {"entry_price": 100, "quantity": 1, "entry_forecast_id": "entry-valid"}
    assert _trigger_telemetry("crypto", position, signal, initial_risk_usd=6,
                              risk_basis_source="test", exclusions=exclusions) is None
    assert exclusions["missing_expected_edge"] == 1


def test_fast_and_deep_scan_persist_reason_without_synthetic_edge():
    src = (Path(__file__).resolve().parent / "market_worker.py").read_text(encoding="utf-8")
    ast.parse(src)
    assert src.count('diagnostics=forecast_diagnostics') >= 2
    assert src.count('forecast_unavailable_reason=forecast_diagnostics.get("unavailable_reason")') >= 2
    assert "FAST_FORECAST_MISSING" in src
    assert "DEEP_FORECAST_MISSING" in src


def test_shadow_logs_entry_and_current_forecasts_separately():
    src = (Path(__file__).resolve().parent / "paper_shadow_exit_challenger.py").read_text(encoding="utf-8")
    ast.parse(src)
    assert "entry_forecast_ids" in src
    assert "current_forecast_id=" in src
    assert "current_forecast_unavailable_reason=" in src
    assert 'if expected_edge_pct is None:\n        return reject("missing_expected_edge")' in src
