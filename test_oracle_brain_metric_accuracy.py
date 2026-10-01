from pathlib import Path


def test_brain_does_not_present_counterfactuals_as_executed_profit():
    source = Path("pages/3_Oracle_Brain.py").read_text(encoding="utf-8")
    assert '"Counterfactual missed opportunities"' in source
    assert "counterfactual_missed_opportunities" in source
    assert "simulated research evidence, not executed trades or verified post-cost profit" in source
    assert 'metric("Missed winners"' not in source
