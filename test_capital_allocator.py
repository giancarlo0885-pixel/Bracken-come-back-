import pytest

from capital_allocator import (
    adaptive_capital_allocation,
    assess_capital_allocation,
    drawdown_risk_multiplier,
    max_positions_for_equity,
    risk_based_position_notional,
)


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


def allocation(**overrides):
    values = {
        "symbol": "AAPL",
        "market": "cash",
        "equity": 10_000,
        "cash": 5_000,
        "current_exposure": 2_000,
        "price": 100,
        "stop_price": 95,
        "tier": "A",
        "confidence": 88,
        "reward_risk": 2.0,
        "market_regime": "risk_on",
        "dollar_volume": 1_000_000_000,
        "spread_pct": 0.002,
    }
    values.update(overrides)
    return adaptive_capital_allocation(**values)


def test_small_portfolio_creates_smaller_position_than_large_portfolio():
    small = allocation(equity=100, cash=90, current_exposure=0, price=10, stop_price=9.5)
    large = allocation(equity=10_000, cash=9_000, current_exposure=0)

    assert small.calculated_notional < large.calculated_notional


def test_position_risk_scales_proportionally_with_equity():
    small = risk_based_position_notional(equity=2_000, price=100, stop_price=95, max_risk_pct=0.01, max_position_pct=0.12, tier_multiplier=1)
    large = risk_based_position_notional(equity=20_000, price=100, stop_price=95, max_risk_pct=0.01, max_position_pct=0.12, tier_multiplier=1)

    assert large == pytest.approx(small * 10)


def test_position_size_decreases_when_stop_distance_widens():
    assert allocation(stop_price=90).calculated_notional < allocation(stop_price=98).calculated_notional


def test_b_and_c_tier_are_smaller_than_a_tier():
    a = allocation(tier="A", confidence=90, reward_risk=2.2)
    b = allocation(tier="B", confidence=75, reward_risk=1.6)
    c = allocation(tier="C", confidence=64, reward_risk=1.3)

    assert b.calculated_notional < a.calculated_notional
    assert c.calculated_notional < b.calculated_notional


def test_risk_off_size_is_smaller_than_risk_on():
    assert allocation(market_regime="risk_off").calculated_notional < allocation(market_regime="risk_on").calculated_notional


def test_drawdown_reduces_and_severe_drawdown_blocks():
    normal = allocation(drawdown_pct=0.0)
    reduced = allocation(drawdown_pct=0.12)
    blocked = allocation(drawdown_pct=0.22)

    assert drawdown_risk_multiplier(0.12) == 0.5
    assert reduced.calculated_notional < normal.calculated_notional
    assert blocked.approved is False
    assert blocked.reason == "SEVERE_DRAWDOWN_BLOCKS_NEW_TRADE"


def test_cash_reserve_cannot_be_violated():
    result = allocation(equity=10_000, cash=1_500, current_exposure=8_500)

    assert result.approved is False
    assert result.reason == "BELOW_MINIMUM_NOTIONAL"


def test_single_position_limit_cannot_be_violated():
    result = allocation(existing_position_value=1_190)

    assert result.calculated_notional <= 10


def test_fractional_equities_work_when_enabled_and_round_down_when_disabled():
    fractional = allocation(equity=500, cash=400, current_exposure=0, price=300, stop_price=285, fractional_equities=True)
    whole = allocation(equity=500, cash=400, current_exposure=0, price=300, stop_price=285, fractional_equities=False)

    assert fractional.calculated_quantity > 0
    assert fractional.calculated_quantity < 1
    assert whole.approved is False


def test_crypto_fractional_quantity_works():
    result = allocation(symbol="ETH-USD", market="crypto", equity=1_000, cash=800, current_exposure=0, price=3_000, stop_price=2_700, tier="B", confidence=75, reward_risk=1.6, fractional_crypto=True)

    assert result.approved is True
    assert 0 < result.calculated_quantity < 1


def test_large_account_liquidity_participation_cap_works():
    result = allocation(equity=1_000_000, cash=500_000, current_exposure=100_000, dollar_volume=30_000_000)

    assert result.calculated_notional <= 30_000


def test_malformed_buying_power_does_not_affect_sizing_unless_validated():
    ignored = allocation(buying_power=1, buying_power_validated=False)
    enforced = allocation(buying_power=1, buying_power_validated=True)

    assert ignored.calculated_notional > enforced.calculated_notional
    assert enforced.approved is False


def test_equity_growth_and_loss_adjust_risk_budget_automatically():
    base = allocation(equity=2_000, cash=1_800, current_exposure=0)
    grown = allocation(equity=3_000, cash=2_700, current_exposure=0)
    down = allocation(equity=1_000, cash=900, current_exposure=0)

    assert grown.risk_budget_dollars > base.risk_budget_dollars
    assert down.risk_budget_dollars < base.risk_budget_dollars


def test_max_positions_scale_with_equity():
    assert max_positions_for_equity(100) == 3
    assert max_positions_for_equity(2_000) == 8
    assert max_positions_for_equity(100_000) == 20
