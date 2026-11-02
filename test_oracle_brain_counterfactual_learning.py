import oracle_brain_feedback as f


def _row(entry, horizon, outcome="missed_winner"):
    return {"outcome_class": outcome, "return_pct": ((horizon / entry) - 1) * 100, "entry_price": entry, "horizon_price": horizon, "payload": {}}


def test_raw_small_upside_does_not_become_positive_learning_after_costs(monkeypatch):
    rows = [_row(100.0, 100.10) for _ in range(30)]
    monkeypatch.setattr("database.rows", lambda sql, params: rows)
    result = f._counterfactual_memory_for_signal({}, market="crypto", symbol="TEST-USD", min_samples=10)
    assert result["simulated_post_cost_profitable_missed"] == 0
    # A raw positive move below round-trip costs is a rejected net loss.
    assert result["avoided_losses"] == 30
    assert result["adjustment"] < 0.0
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


def test_avoided_loss_without_verifiable_prices_is_not_learning_evidence(monkeypatch):
    # The raw outcome label alone cannot certify simulated post-cost economics.
    rows = [{"outcome_class": "avoided_loss", "entry_price": None,
             "horizon_price": 98.0, "payload": {}} for _ in range(30)]
    monkeypatch.setattr("database.rows", lambda sql, params: rows)
    result = f._counterfactual_memory_for_signal({}, market="crypto", symbol="TEST-USD", min_samples=10)
    assert result["avoided_losses"] == 0
    assert result["unevaluable"] == 30
    assert result["status"] == "insufficient"
    assert result["adjustment"] == 0.0


def test_gross_missed_winners_that_lose_after_costs_are_avoided_losses(monkeypatch):
    # Gross-only labels must not suppress a genuine post-cost loss.
    rows = [_row(100.0, 100.10) for _ in range(30)]
    monkeypatch.setattr("database.rows", lambda sql, params: rows)
    result = f._counterfactual_memory_for_signal({}, market="crypto", symbol="TEST-USD", min_samples=10)
    assert result["simulated_post_cost_profitable_missed"] == 0
    assert result["avoided_losses"] == 30
    assert result["status"] == "ok"
    assert result["adjustment"] < 0.0


def test_conflicting_avoided_loss_label_cannot_override_positive_net_economics(monkeypatch):
    rows = [_row(100.0, 103.0, "avoided_loss") for _ in range(30)]
    monkeypatch.setattr("database.rows", lambda sql, params: rows)
    result = f._counterfactual_memory_for_signal({}, market="crypto", symbol="TEST-USD", min_samples=10)
    assert result["avoided_losses"] == 0
    assert result["simulated_post_cost_profitable_missed"] == 0
    assert result["unevaluable"] == 30
    assert result["adjustment"] == 0.0


def test_crypto_counterfactual_uses_conservative_paper_venue_fee_floor(monkeypatch):
    # The Kraken/Coinbase paper models use 60 bps per side, not the
    # generic 10 bps crypto default. +1% gross must not become a winner.
    rows = [_row(100.0, 101.0) for _ in range(30)]
    monkeypatch.setattr("database.rows", lambda sql, params: rows)
    result = f._counterfactual_memory_for_signal(
        {}, market="crypto", symbol="TEST-USD", min_samples=10,
    )
    assert result["status"] == "ok"
    assert result["simulated_post_cost_profitable_missed"] == 0
    assert result["avoided_losses"] == 30
    assert result["adjustment"] < 0.0


def test_cash_counterfactual_is_not_charged_crypto_paper_fee_floor(monkeypatch):
    rows = [_row(100.0, 101.0) for _ in range(30)]
    monkeypatch.setattr("database.rows", lambda sql, params: rows)
    result = f._counterfactual_memory_for_signal(
        {}, market="cash", symbol="TEST", min_samples=10,
    )
    assert result["simulated_post_cost_profitable_missed"] == 30
    assert result["adjustment"] > 0.0


def test_crypto_fee_cannot_be_truncated_by_adverse_price_cap(monkeypatch):
    """A price-friction cap must not erase explicit 60-bps-per-side paper fees."""
    monkeypatch.setenv("PAPER_MAX_ADVERSE_FILL_PCT", "0.001")
    rows = [_row(100.0, 100.5) for _ in range(30)]
    monkeypatch.setattr("database.rows", lambda sql, params: rows)
    result = f._counterfactual_memory_for_signal(
        {}, market="crypto", symbol="TEST-USD", min_samples=10,
    )
    assert result["status"] == "ok"
    assert result["simulated_post_cost_profitable_missed"] == 0
    assert result["avoided_losses"] == 30
    assert result["adjustment"] < 0.0
