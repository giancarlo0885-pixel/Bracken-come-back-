from capital_allocator import assess_capital_allocation


def decision(score=90, probability=68, rr=2.8, recommendation="BUY"):
    return {
        "opportunity_score": score,
        "probability_of_profit": probability,
        "risk_reward_ratio": rr,
        "recommendation": recommendation,
        "quant": {"net_expected_value_pct": 0.025, "execution_score": 90, "risk_score": 86},
        "scenario": {"position_multiplier": 0.95},
    }


def test_strong_setup_receives_allocation():
    result = assess_capital_allocation(
        {"symbol": "AAA", "sector": "TECH", "regime": "bull", "portfolio_correlation": 0.30},
        decision=decision(),
        portfolio={"equity": 2000, "cash": 1000},
        positions=[],
    )
    assert result.approved
    assert result.recommended_position_value > 0
    assert result.capital_priority_score >= 55


def test_concentrated_setup_is_vetoed():
    result = assess_capital_allocation(
        {"symbol": "AAA", "sector": "TECH", "regime": "bull", "portfolio_correlation": 0.9},
        decision=decision(),
        portfolio={"equity": 2000, "cash": 400},
        positions=[{"symbol": "AAA", "sector": "TECH", "market_value": 500, "opportunity_score": 75}],
    )
    assert result.veto
    assert not result.approved


def test_rotation_candidate_detected():
    result = assess_capital_allocation(
        {"symbol": "NEW", "sector": "ENERGY", "regime": "neutral", "portfolio_correlation": 0.2},
        decision=decision(score=92),
        portfolio={"equity": 2000, "cash": 600},
        positions=[{"symbol": "WEAK", "sector": "RETAIL", "market_value": 300, "opportunity_score": 55}],
    )
    assert result.rotation_candidate == "WEAK"
    assert result.rotation_edge >= 8


def test_non_buy_never_allocates():
    result = assess_capital_allocation(
        {"symbol": "AAA", "sector": "TECH", "regime": "bull"},
        decision=decision(recommendation="WATCH"),
        portfolio={"equity": 2000, "cash": 1800},
        positions=[],
    )
    assert result.veto
