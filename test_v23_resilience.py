import importlib


def test_aggressive_valid_buy_gets_starter_allocation(monkeypatch):
    monkeypatch.setenv("AGGRESSIVE_TRADING", "true")
    import capital_allocator
    importlib.reload(capital_allocator)
    result = capital_allocator.assess_capital_allocation(
        {"symbol":"AAA","sector":"TECH","regime":"neutral","portfolio_correlation":0.4},
        decision={
            "opportunity_score":72,"probability_of_profit":56,"risk_reward_ratio":1.8,"recommendation":"BUY",
            "quant":{"net_expected_value_pct":0.01,"execution_score":70,"risk_score":65},
            "scenario":{"position_multiplier":0.35},
        },
        portfolio={"equity":2000,"cash":1000}, positions=[]
    )
    assert result.recommended_position_value > 0


def test_provider_guard_cooldown():
    from provider_guard import available, disable, state
    disable("test-provider", 60, "permission denied")
    assert not available("test-provider")
    assert state("test-provider")["cooldown_remaining_seconds"] > 0
