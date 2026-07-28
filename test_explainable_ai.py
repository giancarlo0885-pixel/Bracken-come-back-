from explainable_ai import build_explainability


def _parts():
    return dict(
        quant={"trade_quality": 86, "approved": True, "position_multiplier": 1.05, "reason": "positive edge after costs"},
        memory={"score_adjustment": 3, "veto": False, "effective_sample_size": 22, "summary": "similar setups were profitable"},
        global_intelligence={"global_score": 72, "approved": True, "veto": False, "score_adjustment": 2, "position_multiplier": 1.03, "summary": "risk-on confirmation"},
        radar={"setup_score": 82, "approved": True, "veto": False, "radar_adjustment": 2, "position_multiplier": 1.04, "summary": "durable breakout"},
        scenario={"probability_of_profit": 69, "expected_return_pct": 4.2, "uncertainty_pct": 10, "approved": True, "veto": False, "position_multiplier": 1.02, "summary": "favorable distribution"},
        capital={"capital_priority_score": 80, "approved": True, "veto": False, "final_multiplier": .95, "verdict": "APPROVE", "summary": "portfolio fit is strong"},
    )


def test_explanation_has_full_decision_ledger():
    report = build_explainability({"symbol":"XYZ", "atr_pct":.02}, final_score=88, recommendation="BUY", **_parts())
    assert report.consensus_label == "HIGH CONSENSUS"
    assert len(report.engine_votes) == 6
    assert len(report.invalidation_conditions) >= 5
    assert any("Combined" in x for x in report.sizing_explanation)


def test_veto_is_exposed_not_hidden():
    parts = _parts()
    parts["radar"] = {**parts["radar"], "veto": True, "approved": False, "summary": "entry is overcrowded"}
    report = build_explainability({"symbol":"XYZ"}, final_score=70, recommendation="AVOID", **parts)
    assert report.consensus_label == "VETO CONFLICT"
    assert any(v["verdict"] == "VETO" for v in report.engine_votes)
    assert report.conflicts
