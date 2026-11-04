"""Frozen challenger selection is cost-aware and never execution authority."""
from copy import deepcopy
from datetime import datetime, timezone
from decimal import Decimal
import pytest
from costed_crypto_policy import plan, exit_reason, freeze, Policy

NOW=datetime(2026,11,4,12,tzinfo=timezone.utc)


def observation(**updates):
    result={'execution_authority':'NONE','canonical_trade':False,'decision_status':'SHADOW_FORECAST',
            'symbol':'BTC-USD','captured_at':NOW.isoformat(),
            'original_forecast':{'symbol':'BTC-USD','forecast_id':'original-f',
                'generated_at':NOW.isoformat(),'validation_status':'shadow',
                'expected_move_pct':4.0,'target_price':104.0},
            'entry_quote':{'symbol':'BTC-USD','quote_id':'original-q','mid_price':'100',
                           'min_order_notional':'5'},
            'costs':{'fee_pct':'0.006'}}
    result.update(updates)
    return result


def limits(**updates):
    result={'round_trip_cost_floor_pct':'1.6','cost_quote_id':'original-q','depth_notional':'25',
            'cash_budget':'25.15','risk_budget':'0.50','max_notional':'25','research_eligible':True}
    result.update(updates)
    return result


def test_selected_opportunity_has_single_cost_deduction_and_virtual_risk_cap():
    trial=observation();caps=limits();before=deepcopy((trial,caps))
    result=plan(trial,caps)
    assert result['selection_status']=='SELECTED'
    assert Decimal(result['entry_reference_gross_edge_pct'])==4
    assert Decimal(result['modeled_net_edge_pct'])==Decimal('2.4')
    assert Decimal(result['entry_notional'])==Decimal('19.23')
    assert Decimal(result['risk_usd'])<=Decimal('.50')
    assert result['budget_semantics']=='RESEARCH_VIRTUAL_BUDGET'
    assert result['execution_authority']=='NONE' and not result['execution_eligible']
    assert result['canonical_trade'] is False and result['promotion_action']=='NONE'
    assert (trial,caps)==before


def test_later_entry_book_can_erase_model_edge_without_rewriting_prediction():
    trial=observation();trial['entry_quote']['mid_price']='110'
    original=deepcopy(trial)
    result=plan(trial,limits())
    assert result['selection_status']=='ABSTAIN'
    assert result['reason']=='EDGE_BELOW_SELECTED_COST'
    assert Decimal(result['model_expected_move_pct'])==4
    assert Decimal(result['entry_reference_gross_edge_pct'])<0
    assert trial==original


def test_gross_positive_below_cost_is_not_progress():
    trial=observation();trial['original_forecast']['target_price']=101.0
    result=plan(trial,limits())
    assert result['reason']=='EDGE_BELOW_SELECTED_COST'
    assert Decimal(result['modeled_net_edge_pct'])==Decimal('-.6')


@pytest.mark.parametrize('key,value',[
    ('risk_budget','0'),('cash_budget',float('nan')),('depth_notional',True),
    ('round_trip_cost_floor_pct','0.2'),('round_trip_cost_floor_pct',float('inf')),
    ('cost_quote_id','other-q'),('research_eligible',None),
])
def test_unknown_or_conflicting_limits_abstain(key,value):
    result=plan(observation(),limits(**{key:value}))
    assert result['selection_status']=='ABSTAIN' and result['entry_notional']=='0'
    assert not result['execution_eligible']


def test_model_abstention_and_absent_budget_are_explicit():
    result=plan(observation(decision_status='ABSTAINED',original_forecast=None),limits())
    assert result['reason']=='MODEL_ABSTAINED'
    assert plan(observation(),{})['reason']=='INVALID_OR_MISSING_VIRTUAL_LIMITS'


def test_cash_depth_and_verified_minimum_bound_selected_size():
    assert plan(observation(),limits(cash_budget='10.06'))['entry_notional']=='10.00'
    assert plan(observation(),limits(depth_notional='6'))['entry_notional']=='6.00'
    result=plan(observation(),limits(depth_notional='4.99'))
    assert result['reason']=='BELOW_VERIFIED_MINIMUM_NOTIONAL'


def test_exit_policy_cannot_call_a_negative_net_trigger_profit():
    trial=observation();trial['original_forecast']['target_price']=110.0
    result=plan(trial,limits(round_trip_cost_floor_pct='4'))
    assert result['reason']=='FROZEN_PROFIT_TARGET_DOES_NOT_COVER_COST'


def test_first_observed_stop_profit_and_time_triggers_are_predeclared():
    entry=plan(observation(),limits())
    assert exit_reason(entry,'-3.1',12)=='PROTECTIVE_STOP'
    assert exit_reason(entry,'1.5',12)=='PROFIT_TARGET'
    assert exit_reason(entry,'.6',12,peak_net_return_pct='1.2')=='PROFIT_PROTECTION'
    assert exit_reason(entry,'-.5',900)=='TIME_EXIT'
    assert exit_reason(entry,'-.5',899) is None
    assert exit_reason(entry,'-3.1',-1) is None
    # This is a trigger only: no stop-price fill or fabricated exit book exists.
    assert 'exit_fill_price' not in entry and 'net_pnl' not in entry


def test_all_costs_are_in_percentage_points_and_policy_manifest_is_frozen():
    entry=plan(observation(),limits())
    assert Decimal(entry['protective_net_return_pct'])==Decimal('-2.6')
    assert Decimal(entry['profit_target_net_return_pct'])==Decimal('1.4')
    assert freeze()==freeze() and freeze()['policy_hash']!=freeze(Policy(holding_seconds=300))['policy_hash']
    assert exit_reason(plan(observation(decision_status='ABSTAINED'),limits()),10,1000) is None
