"""Repeated aliases of one scoring formula must not outvote other factors."""
from copy import deepcopy

import pytest

from strategy_engine import STRATEGY_NAMES, StrategySignal, ensemble_score, evaluate_strategies


def data():
    from strategy_engine import REQUIRED_EVIDENCE
    payload = {key: 1.0 for keys in REQUIRED_EVIDENCE.values() for key in keys}
    payload.update(symbol='BTC-USD', momentum_5d=0.0, momentum_20d=0.0,
                   trend_strength=0.0, volume_ratio=0.0, valuation_score=50.0,
                   catalyst_score=0.0, data_quality_score=80.0,
                   mean_reversion_available=True, mean_reversion_zscore=-2.0,
                   short_horizon_return=-0.04, mean_reversion_score=-1.0,
                   mean_reversion_confidence=0.8)
    return payload


def test_all_generic_labels_cannot_dilute_a_distinct_reversal_disagreement():
    payload = data(); before = deepcopy(payload)
    minimal = evaluate_strategies(payload, ['momentum', 'mean_reversion'])
    expanded = evaluate_strategies(payload)
    assert len(expanded) == len(STRATEGY_NAMES) == 21
    assert all(item.available for item in expanded)
    # Different provider-field labels are still the same generic calculation.
    minimal_score = ensemble_score(minimal)
    expanded_score = ensemble_score(expanded)
    for key in ('expected_return', 'overall_confidence', 'model_agreement'):
        assert expanded_score[key] == pytest.approx(minimal_score[key])
    assert expanded_score['scoring_family_count'] == 2
    assert expanded_score['available_strategy_count'] == 21
    assert expanded_score['expected_return'] == pytest.approx(32.0)
    assert expanded_score['overall_confidence'] == pytest.approx(58.4)
    assert payload == before


def test_repeating_a_card_cannot_change_consensus_or_count_as_a_new_family():
    signals = evaluate_strategies(data(), ['momentum', 'mean_reversion'])
    before = deepcopy(signals)
    baseline = ensemble_score(signals)
    repeated = ensemble_score(signals + [signals[0]] * 100)
    for key in ('expected_return', 'overall_confidence', 'model_agreement', 'scoring_family_count'):
        assert repeated[key] == baseline[key]
    assert signals == before


def test_all_named_cards_retain_provider_availability_without_inventing_evidence():
    signals = evaluate_strategies({'symbol':'BTC-USD'})
    assert len(signals) == 21 and not any(item.available for item in signals)
    score = ensemble_score(signals)
    assert score['overall_confidence'] == 0
    assert score['scoring_family_count'] == 0


def test_distinct_custom_scoring_families_are_not_collapsed_by_equal_scores():
    signals = [StrategySignal('alpha', 60.0, .8, True, '', {}),
               StrategySignal('beta', 60.0, .8, True, '', {})]
    score = ensemble_score(signals)
    assert score['scoring_family_count'] == 2
    assert score['expected_return'] == 60.0


def test_noncrypto_mean_reversion_is_still_the_shared_generic_formula():
    payload = data(); payload['symbol'] = 'AAPL'
    signals = evaluate_strategies(payload, ['momentum', 'mean_reversion'])
    assert ensemble_score(signals)['scoring_family_count'] == 1


def test_advisor_cannot_turn_one_shared_factor_into_a_buy_by_adding_labels():
    from datetime import datetime, timezone
    from advisor_engine import AdvisorProfile, generate_recommendation
    payload = data()
    payload.update(opportunity_score=82.0, expected_return=6.0, expected_downside=3.0,
                   data_quality_score=70.0, liquidity_value=1_000_000.0,
                   validation_status='approved',
                   verified_quote={'symbol':'BTC-USD', 'requested_symbol':'BTC-USD',
                       'provider_symbol':'BTC-USD', 'provider':'unit', 'price':100.0,
                       'quote_timestamp':datetime.now(timezone.utc).isoformat(),
                       'interval':'5m', 'quote_verified':True})
    profile = AdvisorProfile(available_capital=10_000.0)
    minimal = generate_recommendation(payload, profile,
        strategy_signals=evaluate_strategies(payload, ['momentum', 'mean_reversion']))
    expanded = generate_recommendation(payload, profile)
    assert expanded.action == minimal.action == 'HOLD'
    assert expanded.confidence == pytest.approx(minimal.confidence)
    assert expanded.probability_evidence['calibrated'] is False


def test_legacy_signal_shape_without_family_keeps_named_strategy_compatibility():
    from types import SimpleNamespace
    signal = SimpleNamespace(strategy='legacy', score=60.0, confidence=.8, available=True)
    result = ensemble_score([signal])
    assert result['expected_return'] == 60.0
    assert result['scoring_family_count'] == 1
