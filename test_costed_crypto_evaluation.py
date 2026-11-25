"""Matched opportunity reports distinguish actual hypotheses from abstentions."""
from copy import deepcopy
from decimal import Decimal

import pytest

from costed_crypto_evaluation import matched_panel
from costed_crypto_policy import plan
from test_costed_crypto_policy import limits, observation


def pair(identity, *, selected=True, selected_net='-0.25'):
    trial = observation()
    if not selected:
        trial['decision_status'] = 'ABSTAINED'
        trial['original_forecast'] = None
    decision = plan(trial, limits())
    baseline = plan(observation(), limits())
    outcomes = []
    for horizon in (300, 900):
        outcome = {'status': 'COMPLETED_SIMULATION', 'net_pnl': selected_net,
                   'net_return': str(Decimal(selected_net) / 25)} if selected else {
                   'status': 'NO_TRADE', 'net_pnl': '0', 'net_return': None}
        outcomes.append({'source_evidence_id': identity, 'trial_hash': f'trial-{identity}',
            'status': 'COMPLETED_SIMULATION', 'horizon_seconds': horizon,
            'challenger_policy': deepcopy(decision), 'baseline_exit_policy': deepcopy(baseline),
            'net_pnl': '-0.50', 'net_return': '-0.02',
            'market_episode_proxy': '2026-11-24', 'selected_outcome': outcome})
    return tuple(outcomes)


@pytest.mark.parametrize('horizon_index', (0, 1))
@pytest.mark.parametrize('selected', (False, True))
@pytest.mark.parametrize('bad_first', (False, True))
def test_contradictory_selection_endpoint_is_excluded_in_either_pair_order(
        horizon_index, selected, bad_first):
    bad = pair(1, selected=selected)
    bad[horizon_index]['selected_outcome'] = (
        {'status': 'NO_TRADE', 'net_pnl': '0', 'net_return': None} if selected else
        {'status': 'COMPLETED_SIMULATION', 'net_pnl': '1', 'net_return': '0.04'})
    valid = pair(2)
    report = matched_panel([bad, valid] if bad_first else [valid, bad])
    assert report['matched_original_entries'] == 1
    assert report['pair_exclusions'] == 1
    assert report['policies']['entry_only_900']['modeled_net_pnl'] == '-0.25'


def test_abstention_is_not_a_completed_trade_but_remains_in_expectancy_denominator():
    originals = [pair(1, selected=False), pair(2), pair(3, selected_net='0')]
    original_copy = deepcopy(originals)
    report = matched_panel(originals + [originals[0]])
    assert report['matched_original_entries'] == 3
    assert report['matched_selected_entry_opportunities'] == 2
    assert report['matched_abstained_entry_opportunities'] == 1
    assert report['pair_exclusions'] == 1
    for name in ('entry_only_900', 'combined_checkpoints'):
        policy = report['policies'][name]
        assert policy['matched_observations'] == 3
        assert policy['modeled_trade_count'] == 2
        assert policy['no_trade_opportunity_count'] == 1
        assert policy['modeled_net_pnl'] == '-0.25'
        assert Decimal(policy['expectancy_per_opportunity_usd']) == Decimal('-.25') / 3
        assert policy['expectancy_denominator'] == 'MATCHED_ORIGINAL_ENTRY_OPPORTUNITIES_INCLUDING_ABSTENTIONS'
    assert report['policies']['passive_900']['modeled_trade_count'] == 3
    assert report['policies']['no_trade']['modeled_trade_count'] == 0
    assert report['policies']['no_trade']['no_trade_opportunity_count'] == 3
    assert report['policies']['no_trade']['modeled_net_pnl'] == '0'
    assert report['realized_trading_profit'] is None
    assert originals == original_copy


def test_nonzero_pnl_for_no_trade_cannot_enter_the_matched_panel():
    first, last = pair(1, selected=False)
    last['selected_outcome']['net_pnl'] = '1'
    report = matched_panel([(first, last)])
    assert report['matched_original_entries'] == 0
    assert report['pair_exclusions'] == 1
