import numpy as np
import pandas as pd

from v45_flow_shadow import predict_v45_flow_shadow
from v45_shadow_governance import evaluate_shadow_predictions


def test_v45_abstains_for_unvalidated_symbol():
    result = predict_v45_flow_shadow(pd.DataFrame({"Close": [1.0] * 1600}), "SOL-USD")
    assert result["status"] == "ABSTAIN"
    assert result["reason"] == "symbol_not_validated"
    assert result["execution_allowed"] is False


def test_v45_abstains_when_flow_fields_missing():
    result = predict_v45_flow_shadow(pd.DataFrame({"Close": np.linspace(100, 120, 1600)}), "BTC-USD")
    assert result["status"] == "ABSTAIN"
    assert result["reason"] == "missing_flow_fields"
    assert result["execution_allowed"] is False


def test_governance_pass_requires_skill_calibration_coverage_and_no_leakage():
    records = []
    for i in range(600):
        realized_up = i % 2 == 0
        records.append(
            {
                "status": "RESOLVED",
                "probability_up": 0.99 if realized_up else 0.01,
                "realized_up": realized_up,
            }
        )
    result = evaluate_shadow_predictions(
        records,
        total_opportunities=1200,
        temporal_leakage_ok=True,
        beats_baselines=True,
    )
    assert result["eligible_for_promotion"] is True
    assert result["status"] == "PASS"
    assert all(result["checks"].values())


def test_governance_fails_closed_on_leakage_or_bad_skill():
    records = [
        {"status": "RESOLVED", "probability_up": 0.51, "realized_up": i % 2 == 0}
        for i in range(600)
    ]
    result = evaluate_shadow_predictions(
        records,
        total_opportunities=1200,
        temporal_leakage_ok=False,
        beats_baselines=False,
    )
    assert result["eligible_for_promotion"] is False
    assert result["status"] == "SHADOW_ONLY"
    assert result["checks"]["temporal_leakage"] is False
    assert result["checks"]["beats_baselines"] is False
