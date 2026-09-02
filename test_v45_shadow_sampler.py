import pandas as pd

import v45_shadow_sampler as sampler


def test_sample_once_collects_evidence_without_execution(monkeypatch):
    history = pd.DataFrame(
        {
            "Close": [100.0, 101.0],
            "QuoteVolume": [1000.0, 1000.0],
            "TakerBuyQuoteVolume": [500.0, 500.0],
        },
        index=pd.to_datetime(["2026-09-02T00:00:00Z", "2026-09-02T00:05:00Z"]),
    )
    persisted = []
    monkeypatch.setattr(sampler, "ensure_schema", lambda: None)
    monkeypatch.setattr(sampler, "_fetch_history", lambda symbol: history)
    monkeypatch.setattr(sampler, "_resolve_pending", lambda symbol, frame: {"predictions": 0, "abstentions": 2})
    monkeypatch.setattr(
        sampler,
        "predict_v45_flow_shadow",
        lambda frame, symbol: {
            "model": "crypto selective flow reversal",
            "model_version": "v45-flow-shadow",
            "status": "ABSTAIN",
            "reason": "test",
            "execution_allowed": False,
        },
    )
    monkeypatch.setattr(sampler, "_persist_observation", lambda *args: persisted.append(args))
    monkeypatch.setattr(
        sampler,
        "evidence_summary",
        lambda: {
            "total_observations": 4,
            "abstentions_resolved": 2,
            "abstentions_below_actionable_move": 2,
            "abstention_quiet_rate": 1.0,
            "actionable_move_pct": 1.25,
        },
    )
    monkeypatch.setattr(sampler, "governance_summary", lambda: {"eligible_for_promotion": False, "status": "SHADOW_ONLY"})

    result = sampler.sample_once()
    assert set(result["symbols"]) == {"BTC-USD", "ETH-USD"}
    assert len(persisted) == 2
    assert result["governance"]["eligible_for_promotion"] is False
    assert result["evidence"]["abstention_quiet_rate"] == 1.0
    assert all(item[3]["execution_allowed"] is False for item in persisted)


def test_actionable_move_threshold_is_observational_only():
    assert sampler.ACTIONABLE_MOVE_PCT == 1.25
