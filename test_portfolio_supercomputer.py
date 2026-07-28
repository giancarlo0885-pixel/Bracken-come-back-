from portfolio_supercomputer import assess_portfolio_supercomputer


def base_decision(value=180.0):
    return {
        "recommendation": "BUY", "opportunity_score": 88, "probability_of_profit": 70,
        "risk_reward_ratio": 2.5,
        "quant": {"risk_score": 82, "execution_score": 88},
        "scenario": {"value_at_risk_95_pct": -7},
        "capital": {"recommended_position_value": value, "recommended_position_pct": value / 10},
    }


def test_healthy_portfolio_approves_controlled_allocation():
    result = assess_portfolio_supercomputer(
        {"symbol": "NVDA", "sector": "TECH", "portfolio_correlation": 0.35},
        decision=base_decision(150), portfolio={"equity": 1000, "cash": 500, "peak_equity": 1050},
        positions=[{"symbol": "SPY", "sector": "INDEX", "market_value": 250, "score": 75}],
    )
    assert result.approved
    assert 0 < result.recommended_trade_value <= 150
    assert result.projected_cash_pct >= 10


def test_concentration_blocks_additional_position():
    result = assess_portfolio_supercomputer(
        {"symbol": "NVDA", "sector": "TECH"}, decision=base_decision(200),
        portfolio={"equity": 1000, "cash": 500},
        positions=[{"symbol": "NVDA", "sector": "TECH", "market_value": 230, "score": 82}],
    )
    assert result.veto
    assert result.recommended_trade_value == 0


def test_rotation_candidate_identified():
    result = assess_portfolio_supercomputer(
        {"symbol": "NVDA", "sector": "TECH"}, decision=base_decision(100),
        portfolio={"equity": 1000, "cash": 300},
        positions=[{"symbol": "OLD", "sector": "UTILITIES", "market_value": 200, "score": 55}],
    )
    assert result.rotation_candidate == "OLD"
