"""Fail-first research-only cutoff tests for persisted shadow-exit quote evidence.

The quote must have existed when the persisted signal decision was made,
not merely before the current wall clock.
"""
from datetime import datetime, timedelta, timezone

import paper_shadow_exit_challenger as shadow


def _telemetry(monkeypatch, *, quote_offset_seconds: int):
    decision_at = datetime.now(timezone.utc) - timedelta(seconds=30)
    quote_at = decision_at + timedelta(seconds=quote_offset_seconds)
    signal = {
        "id": 7001,
        "created_at": decision_at,
        "price": 101.0,
        "expected_edge_pct": -0.3,
        "action": "SELL",
        "payload": {
            "quote_verified": True,
            "quote_timestamp": quote_at.isoformat(),
            "price": 101.0,
        },
    }
    # Isolate the timestamp gate from the already-tested fill-cost model.
    monkeypatch.setattr(
        shadow.CounterfactualSettlement,
        "calculate_immutable_challenger_r",
        staticmethod(lambda *args, **kwargs: (-0.2, {"fee_contract": shadow.COST_MODEL_VERSION})),
    )
    monkeypatch.setattr(shadow, "_projected_hold_r", lambda *args, **kwargs: (-0.4, {}))
    exclusions = {}
    result = shadow._trigger_telemetry(
        "crypto",
        {"entry_price": 100.0, "quantity": 1.0},
        signal,
        initial_risk_usd=6.0,
        risk_basis_source="exact_entry_stop_lots",
        exclusions=exclusions,
    )
    return result, exclusions


def test_shadow_rejects_quote_arriving_after_persisted_signal_decision(monkeypatch):
    # Ten seconds AFTER the signal, but twenty seconds BEFORE wall clock:
    # previously accepted as a valid forward-evidence observation.
    result, exclusions = _telemetry(monkeypatch, quote_offset_seconds=10)
    assert result is None
    assert exclusions["quote_after_signal_decision"] == 1


def test_shadow_accepts_predecision_quote_with_other_evidence_unchanged(monkeypatch):
    result, exclusions = _telemetry(monkeypatch, quote_offset_seconds=-10)
    assert result is not None
    assert result["expected_edge_pct"] == -0.3
    assert result["thesis_decay_confirmed"] is True
    assert exclusions == {}
