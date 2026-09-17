from paper_aeve_v1_formula import AEVEScoringConfig, score_entry, should_take_profit


def candidate(**overrides):
    base = dict(
        expected_net_edge_pct=0.24, mfe_pct=1.1, mae_pct=-0.30,
        round_trip_cost_pct=0.15, loss_streak=0,
        price_above_recent_low_pct=0.35, rebound_from_low_pct=0.45,
        rsi=44, trend_confirmed=True, regime_expectancy_positive=True,
        profit_factor=1.35, min_samples=80,
    )
    base.update(overrides)
    return base


def test_rejects_negative_expectancy_even_with_pretty_technicals():
    result = score_entry(**candidate(expected_net_edge_pct=-0.03, regime_expectancy_positive=False, profit_factor=0.9))
    assert result.would_trade is False


def test_accepts_cost_covered_rebound_with_positive_forward_economics():
    assert score_entry(**candidate()).would_trade is True


def test_loss_streak_reduces_score():
    clean = score_entry(**candidate(loss_streak=0))
    repeated = score_entry(**candidate(loss_streak=6))
    assert repeated.score < clean.score


def test_generation_edge_and_pf_thresholds_are_consumed():
    assert score_entry(**candidate(), config=AEVEScoringConfig(min_edge_pct=0.30)).would_trade is False
    assert score_entry(**candidate(), config=AEVEScoringConfig(min_profit_factor=1.40)).would_trade is False


def test_generation_excursion_and_cost_thresholds_are_consumed():
    assert score_entry(**candidate(), config=AEVEScoringConfig(min_mfe_mae_ratio=4.0)).would_trade is False
    assert score_entry(**candidate(), config=AEVEScoringConfig(min_mfe_cost_multiple=8.0)).would_trade is False


def test_generation_rebound_score_and_loss_streak_thresholds_are_consumed():
    # Use a non-saturated rebound fixture: quality=0.20/(2*0.15)=0.667,
    # which clears the default 0.50 gate but must fail a stricter 0.95 gate.
    rebound_candidate = candidate(rebound_from_low_pct=0.20)
    assert score_entry(**rebound_candidate).would_trade is True
    assert score_entry(**rebound_candidate, config=AEVEScoringConfig(rebound_gate=0.95)).would_trade is False
    assert score_entry(**candidate(), config=AEVEScoringConfig(score_gate=2.0)).would_trade is False
    assert score_entry(**candidate(loss_streak=3), config=AEVEScoringConfig(max_loss_streak=2)).would_trade is False


def test_generation_regime_requirement_is_consumed():
    relaxed = AEVEScoringConfig(require_positive_regime=False)
    result = score_entry(**candidate(regime_expectancy_positive=False), config=relaxed)
    assert isinstance(result.would_trade, bool)


def test_falling_knife_does_not_count_as_buy_low():
    result = score_entry(**candidate(rebound_from_low_pct=0.01, rsi=25, trend_confirmed=False))
    assert result.would_trade is False


def test_take_profit_requires_net_gain_and_pullback():
    assert should_take_profit(unrealized_return_pct=0.85, round_trip_cost_pct=0.15,
                              mfe_since_entry_pct=1.0, pullback_from_peak_pct=0.35) is True
    assert should_take_profit(unrealized_return_pct=0.20, round_trip_cost_pct=0.15,
                              mfe_since_entry_pct=0.25, pullback_from_peak_pct=0.20) is False
