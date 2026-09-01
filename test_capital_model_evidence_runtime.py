from __future__ import annotations

from types import SimpleNamespace

import pandas as pd

import capital_model_evidence_runtime as evidence


def test_validation_key_is_deterministic_and_horizon_scoped() -> None:
    base = dict(
        model="m",
        model_version="v1",
        symbol="BTC-USD",
        interval="1d",
        decision_timestamp="2026-01-01",
        outcome_timestamp="2026-01-06",
    )
    first = evidence._validation_key(horizon_bars=5, **base)
    assert first == evidence._validation_key(horizon_bars=5, **base)
    assert first != evidence._validation_key(horizon_bars=6, **base)


def test_evidence_current_requires_runs_symbols_and_calibration(monkeypatch) -> None:
    monkeypatch.setattr(
        evidence,
        "_recent_evidence",
        lambda *args: {"run_count": 3, "distinct_symbols": 3, "calibration_samples": 100},
    )
    assert evidence._evidence_current("m", "v") is True
    monkeypatch.setattr(
        evidence,
        "_recent_evidence",
        lambda *args: {"run_count": 3, "distinct_symbols": 2, "calibration_samples": 100},
    )
    assert evidence._evidence_current("m", "v") is False


def test_refresh_does_not_regenerate_current_evidence(monkeypatch) -> None:
    monkeypatch.setattr(evidence, "_execution_models", lambda: [("m", "v")])
    monkeypatch.setattr(evidence, "_evidence_current", lambda *args: True)
    monkeypatch.setattr(
        evidence,
        "_recent_evidence",
        lambda *args: {"run_count": 3, "distinct_symbols": 3, "calibration_samples": 250},
    )
    monkeypatch.setattr(
        evidence,
        "apply_model_governance",
        lambda *args: SimpleNamespace(recommended_status="approved", eligible_for_approval=True),
    )
    monkeypatch.setattr(
        evidence,
        "_build_symbol_evidence",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("must not rebuild current evidence")),
    )
    result = evidence.refresh_capital_model_evidence()
    assert result[0]["status"] == "CURRENT"
    assert result[0]["eligible_for_approval"] is True


def test_symbol_evidence_persists_run_and_deduplicatable_samples(monkeypatch) -> None:
    closes = [100.0 + index * 0.5 + (1.0 if index % 3 == 0 else 0.0) for index in range(90)]
    history = pd.DataFrame(
        {
            "Open": closes,
            "High": [value * 1.01 for value in closes],
            "Low": [value * 0.99 for value in closes],
            "Close": closes,
            "Volume": [1000.0] * len(closes),
        },
        index=pd.date_range("2025-01-01", periods=len(closes), freq="D", tz="UTC"),
    )
    monkeypatch.setattr(evidence, "get_history", lambda *args, **kwargs: history)
    monkeypatch.setattr(evidence, "history_matches_symbol", lambda *args, **kwargs: True)
    monkeypatch.setattr(
        evidence,
        "forecast_price",
        lambda past, **kwargs: SimpleNamespace(
            probability_up=0.55,
            expected_move_pct=0.5,
            target_price=float(past["Close"].iloc[-1]) * 1.005,
        ),
    )
    monkeypatch.setattr(evidence, "temporal_leakage_probe", lambda *args, **kwargs: {"ok": True})

    statements: list[str] = []

    class FakeConnection:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def execute(self, statement, params=None):
            statements.append(" ".join(str(statement).split()))
            return None

    monkeypatch.setattr(evidence, "connect", lambda: FakeConnection())
    result = evidence._build_symbol_evidence(
        "BTC-USD",
        "log-return diffusion",
        "v-test",
        period="1y",
        interval="1d",
        horizon_bars=2,
        minimum_history_bars=40,
        stride=2,
    )
    assert result["sample_count"] > 0
    assert any("INSERT INTO walk_forward_validation_runs" in statement for statement in statements)
    assert any("INSERT INTO forecast_validation" in statement for statement in statements)
    assert any("ON CONFLICT (evidence_key) DO NOTHING" in statement for statement in statements)
