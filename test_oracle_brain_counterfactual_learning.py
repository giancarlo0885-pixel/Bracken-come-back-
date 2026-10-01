import oracle_brain_feedback as f


def test_counterfactual_memory_rewards_repeated_missed_upside(monkeypatch):
    rows = [{"outcome_class": "missed_winner", "return_pct": 1.0, "payload": {}} for _ in range(24)]
    rows += [{"outcome_class": "avoided_loss", "return_pct": -1.0, "payload": {}} for _ in range(6)]
    monkeypatch.setattr("database.rows", lambda sql, params: rows)
    result = f._counterfactual_memory_for_signal({}, market="crypto", symbol="TEST-USD", min_samples=10)
    assert result["status"] == "ok"
    assert result["adjustment"] > 0
    assert result["execution_impact"] == "BOUNDED_RANKING_ONLY"


def test_counterfactual_memory_penalizes_repeated_avoided_losses(monkeypatch):
    rows = [{"outcome_class": "avoided_loss", "return_pct": -1.0, "payload": {}} for _ in range(24)]
    rows += [{"outcome_class": "missed_winner", "return_pct": 1.0, "payload": {}} for _ in range(6)]
    monkeypatch.setattr("database.rows", lambda sql, params: rows)
    result = f._counterfactual_memory_for_signal({}, market="crypto", symbol="TEST-USD", min_samples=10)
    assert result["adjustment"] < 0
    assert abs(result["adjustment"]) <= 0.75


def test_counterfactual_memory_needs_evidence(monkeypatch):
    monkeypatch.setattr("database.rows", lambda sql, params: [{"outcome_class": "missed_winner"}])
    result = f._counterfactual_memory_for_signal({}, market="cash", symbol="TEST", min_samples=10)
    assert result["status"] == "insufficient"
    assert result["adjustment"] == 0.0
