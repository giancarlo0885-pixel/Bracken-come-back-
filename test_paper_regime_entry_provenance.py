from __future__ import annotations

from types import SimpleNamespace

import paper_regime_entry_provenance as provenance


def _paper(monkeypatch):
    monkeypatch.setenv("EXECUTION_MODE", "paper")
    monkeypatch.setenv("PAPER_AUTONOMOUS_LEARNING", "true")
    monkeypatch.setenv("ENABLE_BROKER_SUBMISSION", "false")
    monkeypatch.setenv("LIVE_TRADING_ARMED", "false")


def _oracle(original_snapshot):
    def signal_value(signal, name, default=None):
        return signal.get(name, default) if isinstance(signal, dict) else default

    def original(**kwargs):
        return {
            "entry_signal_id": "signal-1",
            "feature_snapshot": original_snapshot,
            "risk_snapshot": {},
            "portfolio_snapshot": {},
        }

    return SimpleNamespace(signal_value=signal_value, _entry_provenance=original)


def _worker():
    def original(signal, route, scan_type, **extra):
        return {
            "symbol": getattr(signal, "symbol", "TEST-USD"),
            "scan_type": scan_type,
            "existing": "keep",
            **extra,
        }

    return SimpleNamespace(_signal_payload=original)


def test_install_fails_closed_if_live_is_armed(monkeypatch):
    _paper(monkeypatch)
    monkeypatch.setenv("LIVE_TRADING_ARMED", "true")
    oracle = _oracle({})
    worker = _worker()
    assert provenance.install_paper_regime_entry_provenance(oracle, worker) is False
    assert not getattr(oracle._entry_provenance, "_paper_regime_entry_provenance_v1", False)
    assert not getattr(worker._signal_payload, "_paper_regime_signal_payload_v1", False)


def test_empty_entry_snapshot_keeps_raw_regime_evidence(monkeypatch):
    _paper(monkeypatch)
    oracle = _oracle({})
    assert provenance.install_paper_regime_entry_provenance(oracle) is True

    result = oracle._entry_provenance(
        signal={
            "trend_strength": 0.08,
            "momentum_20d": 0.12,
            "volatility_20d": 0.72,
        },
        quote_metadata={},
        now="2026-09-12T12:00:00+00:00",
    )

    assert result["feature_snapshot"]["trend_strength"] == 0.08
    assert result["feature_snapshot"]["momentum_20d"] == 0.12
    assert result["feature_snapshot"]["volatility_20d"] == 0.72


def test_existing_entry_features_are_never_overwritten(monkeypatch):
    _paper(monkeypatch)
    oracle = _oracle({"volatility_20d": 0.21, "custom": "preserve"})
    provenance.install_paper_regime_entry_provenance(oracle)

    result = oracle._entry_provenance(
        signal={"volatility_20d": 0.91, "trend_strength": -0.07},
        quote_metadata={},
        now="2026-09-12T12:00:00+00:00",
    )

    assert result["feature_snapshot"]["volatility_20d"] == 0.21
    assert result["feature_snapshot"]["trend_strength"] == -0.07
    assert result["feature_snapshot"]["custom"] == "preserve"


def test_persisted_signal_payload_gets_observed_regime_inputs_without_mutating_signal(monkeypatch):
    _paper(monkeypatch)
    oracle = _oracle({})
    worker = _worker()
    signal = SimpleNamespace(
        symbol="NEAR-USD",
        trend_strength=0.06,
        momentum_20d=0.14,
        volatility_20d=0.31,
    )

    assert provenance.install_paper_regime_entry_provenance(oracle, worker) is True
    payload = worker._signal_payload(signal, {"provider": "test"}, "deep", council="v3")

    assert payload["trend_strength"] == 0.06
    assert payload["momentum_20d"] == 0.14
    assert payload["volatility_20d"] == 0.31
    assert payload["existing"] == "keep"
    assert payload["council"] == "v3"
    assert signal.volatility_20d == 0.31


def test_persisted_signal_payload_never_overwrites_existing_exact_values(monkeypatch):
    _paper(monkeypatch)
    oracle = _oracle({})

    def original(signal, route, scan_type, **extra):
        return {
            "volatility_20d": 0.22,
            "trend_strength": 0.03,
            "scan_type": scan_type,
        }

    worker = SimpleNamespace(_signal_payload=original)
    signal = SimpleNamespace(volatility_20d=0.88, trend_strength=-0.20, momentum_20d=0.11)
    provenance.install_paper_regime_entry_provenance(oracle, worker)
    payload = worker._signal_payload(signal, {}, "deep")

    assert payload["volatility_20d"] == 0.22
    assert payload["trend_strength"] == 0.03
    assert payload["momentum_20d"] == 0.11
