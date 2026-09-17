from paper_aeve_v1_formula import score_entry, should_take_profit


def test_rejects_negative_expectancy_even_with_pretty_technicals():
    result = score_entry(
        expected_net_edge_pct=-0.03, mfe_pct=1.2, mae_pct=-0.2,
        round_trip_cost_pct=0.15, loss_streak=0,
        price_above_recent_low_pct=0.2, rebound_from_low_pct=0.5,
        rsi=42, trend_confirmed=True, regime_expectancy_positive=False,
        profit_factor=0.9, min_samples=100,
    )
    assert result.would_trade is False


def test_accepts_cost_covered_rebound_with_positive_forward_economics():
    result = score_entry(
        expected_net_edge_pct=0.24, mfe_pct=1.1, mae_pct=-0.30,
        round_trip_cost_pct=0.15, loss_streak=0,
        price_above_recent_low_pct=0.35, rebound_from_low_pct=0.45,
        rsi=44, trend_confirmed=True, regime_expectancy_positive=True,
        profit_factor=1.35, min_samples=80,
    )
    assert result.would_trade is True


def test_loss_streak_reduces_score():
    base = dict(
        expected_net_edge_pct=0.20, mfe_pct=0.9, mae_pct=-0.25,
        round_trip_cost_pct=0.12, price_above_recent_low_pct=0.4,
        rebound_from_low_pct=0.4, rsi=45, trend_confirmed=True,
        regime_expectancy_positive=True, profit_factor=1.25, min_samples=80,
    )
    clean = score_entry(loss_streak=0, **base)
    repeated = score_entry(loss_streak=6, **base)
    assert repeated.score < clean.score


def test_falling_knife_does_not_count_as_buy_low():
    result = score_entry(
        expected_net_edge_pct=0.25, mfe_pct=1.0, mae_pct=-0.25,
        round_trip_cost_pct=0.12, loss_streak=0,
        price_above_recent_low_pct=0.1, rebound_from_low_pct=0.01,
        rsi=25, trend_confirmed=False, regime_expectancy_positive=True,
        profit_factor=1.3, min_samples=100,
    )
    assert result.would_trade is False


def test_take_profit_requires_net_gain_and_pullback():
    assert should_take_profit(
        unrealized_return_pct=0.85, round_trip_cost_pct=0.15,
        mfe_since_entry_pct=1.0, pullback_from_peak_pct=0.35,
    ) is True
    assert should_take_profit(
        unrealized_return_pct=0.20, round_trip_cost_pct=0.15,
        mfe_since_entry_pct=0.25, pullback_from_peak_pct=0.20,
    ) is False
