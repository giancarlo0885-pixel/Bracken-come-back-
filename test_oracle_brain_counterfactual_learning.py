import oracle_brain_feedback as f


def _row(entry, horizon, outcome="missed_winner"):
    return {"outcome_class": outcome, "return_pct": ((horizon / entry) - 1) * 100, "entry_price": entry, "horizon_price": horizon, "payload": {}}


def test_raw_small_upside_does_not_become_positive_learning_after_costs(monkeypatch):
    rows = [_row(100.0, 100.10) for _ in range(30)]
    monkeypatch.setattr("database.rows", lambda sql, params: rows)
    result = f._counterfactual_memory_for_signal({}, market="crypto", symbol="TEST-USD", min_samples=10)
    assert result["simulated_post_cost_profitable_missed"] == 0
    assert result["adjustment"] == 0.0
    assert result["economics"] == "SIMULATED_POST_COST"


def test_large_upside_can_be_positive_after_simulated_costs(monkeypatch):
    rows = [_row(100.0, 103.0) for _ in range(30)]
    monkeypatch.setattr("database.rows", lambda sql, params: rows)
    result = f._counterfactual_memory_for_signal({}, market="crypto", symbol="TEST-USD", min_samples=10)
    assert result["status"] == "ok"
    assert result["simulated_post_cost_profitable_missed"] == 30
    assert result["adjustment"] > 0


def test_avoided_losses_remain_negative_evidence(monkeypatch):
    rows = [_row(100.0, 98.0, "avoided_loss") for _ in range(30)]
    monkeypatch.setattr("database.rows", lambda sql, params: rows)
    result = f._counterfactual_memory_for_signal({}, market="crypto", symbol="TEST-USD", min_samples=10)
    assert result["status"] == "ok"
    assert result["adjustment"] < 0


def test_invalid_prices_are_unknown_not_profitable(monkeypatch):
    rows = [{"outcome_class": "missed_winner", "entry_price": None, "horizon_price": 101.0, "payload": {}} for _ in range(30)]
    monkeypatch.setattr("database.rows", lambda sql, params: rows)
    result = f._counterfactual_memory_for_signal({}, market="cash", symbol="TEST", min_samples=10)
    assert result["simulated_post_cost_profitable_missed"] == 0
    assert result["unevaluable"] == 30
    assert result["adjustment"] == 0.0


def test_stock_and_crypto_use_different_friction(monkeypatch):
    rows = [_row(100.0, 100.7) for _ in range(30)]
    monkeypatch.setattr("database.rows", lambda sql, params: rows)
    stock = f._counterfactual_memory_for_signal({}, market="cash", symbol="TEST", min_samples=10)
    crypto = f._counterfactual_memory_for_signal({}, market="crypto", symbol="TEST-USD", min_samples=10)
    assert stock["simulated_post_cost_profitable_missed"] >= crypto["simulated_post_cost_profitable_missed"]
