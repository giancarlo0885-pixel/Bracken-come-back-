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


def test_install_fails_closed_if_live_is_armed(monkeypatch):
    _paper(monkeypatch)
    monkeypatch.setenv("LIVE_TRADING_ARMED", "true")
    oracle = _oracle({})
    assert provenance.install_paper_regime_entry_provenance(oracle) is False
    assert not getattr(oracle._entry_provenance, "_paper_regime_entry_provenance_v1", False)


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
