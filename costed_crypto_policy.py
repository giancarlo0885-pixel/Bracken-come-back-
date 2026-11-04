"""A frozen, research-only economic policy over original paper observations.

Percent inputs are percentage points; cash, risk and notional inputs are dollars.
The caller validates original trials/books and reprices the selected quantity
through the existing depth/fee model. Nothing here authorizes canonical fills.
"""
from dataclasses import dataclass, asdict
from datetime import timedelta
from decimal import Decimal, InvalidOperation, ROUND_DOWN
import hashlib
import json

from kraken_reference_book import timestamp

VERSION = 'costed-crypto-challenger-v1'


@dataclass(frozen=True)
class Policy:
    min_edge_cost_multiple: str = '1.25'
    max_notional: str = '25'
    cash_budget: str = '25.15'
    risk_budget: str = '0.50'
    stop_distance_pct: str = '1'
    profit_target_pct: str = '3'
    holding_seconds: int = 900
    profit_protect_arm_pct: str = '1'
    profit_giveback_pct: str = '0.5'


def _decimal(value):
    if isinstance(value, bool) or not isinstance(value, (str, int, float, Decimal)):
        raise ValueError('numeric evidence required')
    try:
        result = Decimal(str(value))
    except InvalidOperation as exc:
        raise ValueError('numeric evidence required') from exc
    if not result.is_finite():
        raise ValueError('finite evidence required')
    return result


def _hash(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, allow_nan=False,
                                    separators=(',', ':')).encode()).hexdigest()


def freeze(policy=Policy()):
    """Validate once and persist this exact manifest before any outcomes arrive."""
    config = asdict(policy)
    values = {key:_decimal(value) for key,value in config.items() if key!='holding_seconds'}
    if (values['min_edge_cost_multiple'] < 1 or values['max_notional'] <= 0
            or values['cash_budget'] <= 0 or values['risk_budget'] <= 0
            or not 0 < values['stop_distance_pct'] < 100
            or values['profit_target_pct'] <= 0
            or values['profit_protect_arm_pct'] <= 0 or values['profit_giveback_pct'] <= 0
            or type(policy.holding_seconds) is not int or policy.holding_seconds not in {300,900}):
        raise ValueError('invalid frozen research policy')
    return {'version':VERSION, **config, 'execution_authority':'NONE',
            'canonical_trade':False, 'policy_hash':_hash({'version':VERSION,**config})}


def _plan(trial, *, cost_floor_pct, cost_quote_id, research_eligible, depth_notional,
         policy=Policy()):
    """One economic selection and bounded sizing rule; not an execution gate.

    The signed forecast return is an unconditional model estimate. Quantile bands
    are never treated as conditional win/loss payoffs or calibrated probabilities.
    A selected hypothesis still requires independent forward evaluation; model
    approval and all execution guards remain separate and cannot be granted here.
    """
    manifest=freeze(policy)
    result={'version':VERSION, 'policy':manifest, 'action':'NO_TRADE',
            'reason':'UNAVAILABLE_ORIGINAL_EVIDENCE', 'selected_notional':'0',
            'execution_authority':'NONE', 'canonical_trade':False,
            'promotion_action':'NONE', 'execution_eligible':False,
            'budget_semantics':'RESEARCH_VIRTUAL_BUDGET'}
    try:
        if (research_eligible is not True or not isinstance(trial,dict)
                or trial.get('execution_authority')!='NONE'
                or trial.get('canonical_trade') is not False):
            return result
        if trial.get('decision_status')=='ABSTAINED':
            return {**result,'reason':'MODEL_ABSTAINED'}
        forecast=trial.get('original_forecast')
        quote=trial.get('entry_quote')
        if (trial.get('decision_status')!='SHADOW_FORECAST'
                or not isinstance(forecast,dict) or not isinstance(quote,dict)
                or forecast.get('symbol')!=trial.get('symbol')
                or quote.get('symbol')!=trial.get('symbol')
                or not forecast.get('forecast_id') or forecast.get('validation_status')!='shadow'
                or not cost_quote_id or cost_quote_id!=quote.get('quote_id')):
            return result
        captured=timestamp(trial['captured_at'])
        if timestamp(forecast['generated_at'])>captured:
            return result
        model_edge=_decimal(forecast['expected_move_pct']);cost=_decimal(cost_floor_pct)
        depth=_decimal(depth_notional);fee=_decimal(trial['costs']['fee_pct'])
        minimum=_decimal(quote['min_order_notional'])
        mid=_decimal(quote['mid_price'])
        target=_decimal(forecast['target_price'])
        if cost<=0 or depth<=0 or not 0<=fee<1 or minimum<=0 or mid<=0 or target<=0:
            return result
        fee_floor=((1+fee)/(1-fee)-1)*100
        if cost<fee_floor:
            return result
        # Do not rebase a completed forecast to the later passive entry book.
        # Preserve its original model return and compare its original target to
        # the actual frozen entry reference as a separately named opportunity.
        edge=(target/mid-1)*100
        margin=_decimal(policy.min_edge_cost_multiple)
        required=cost*margin
        result.update(symbol=trial['symbol'], captured_at=trial['captured_at'],
            forecast_id=forecast['forecast_id'], entry_quote_id=quote['quote_id'],
            model_expected_move_pct=str(model_edge),
            entry_reference_gross_edge_pct=str(edge),
            edge_basis='ORIGINAL_FORECAST_TARGET_OVER_FROZEN_ENTRY_REFERENCE',
            cost_estimate_semantics='CURRENT_BOOK_FRICTION_NOT_FUTURE_RETURN_GUARANTEE',
            expected_gross_return_pct=str(edge), round_trip_cost_floor_pct=str(cost),
            modeled_net_edge_pct=str(edge-cost), required_gross_edge_pct=str(required))
        if edge<=0 or edge<required:
            return {**result,'reason':'EDGE_BELOW_SELECTED_COST'}
        if _decimal(policy.profit_target_pct)<=cost:
            return {**result,'reason':'FROZEN_PROFIT_TARGET_DOES_NOT_COVER_COST'}
        # Cost is already a full round-trip floor. Add it once to the stop
        # distance for sizing; never subtract another fee from expected net edge.
        downside_fraction=(_decimal(policy.stop_distance_pct)+cost)/100
        notional=min(_decimal(policy.max_notional),_decimal(policy.cash_budget)/(1+fee),
                     _decimal(policy.risk_budget)/downside_fraction,depth)
        notional=notional.quantize(Decimal('.01'),rounding=ROUND_DOWN)
        if notional<minimum:
            return {**result,'reason':'BELOW_VERIFIED_MINIMUM_NOTIONAL'}
        result.update(action='RESEARCH_LONG',reason='EDGE_CLEARS_SELECTED_COST',
            selected_notional=str(notional), estimated_risk_usd=str(notional*downside_fraction),
            entry_reference_price=str(mid),
            stop_reference_price=str(mid*(1-_decimal(policy.stop_distance_pct)/100)),
            profit_reference_price=str(mid*(1+_decimal(policy.profit_target_pct)/100)),
            time_exit_at=(captured+timedelta(seconds=policy.holding_seconds)).isoformat(),
            protective_net_return_pct=str(-(_decimal(policy.stop_distance_pct)+cost)),
            profit_target_net_return_pct=str(_decimal(policy.profit_target_pct)-cost),
            risk_semantics='FROZEN_STOP_PLUS_CURRENT_FRICTION_ESTIMATE_GAPS_CAN_EXCEED_BUDGET')
        return result
    except (ValueError,KeyError,TypeError,ArithmeticError):
        return {**result,'action':'NO_TRADE','reason':'UNAVAILABLE_ORIGINAL_EVIDENCE',
                'selected_notional':'0'}



def plan(trial, limits):
    """Public pilot seam; all budgets are explicitly virtual research dollars.

    Required limits: cash_budget, risk_budget, max_notional, depth_notional,
    round_trip_cost_floor_pct and cost_quote_id. Optional policy settings are
    frozen into the manifest; no live or canonical trading control is consulted
    or changed. The parent caller must validate the trial/book and compute the
    supplied cost floor with its existing exact depth/fee implementation.
    """
    try:
        kwargs={name:limits[name] for name in ('cash_budget','risk_budget','max_notional')}
        for name in ('min_edge_cost_multiple','stop_distance_pct','profit_target_pct',
                     'holding_seconds','profit_protect_arm_pct','profit_giveback_pct'):
            if name in limits:kwargs[name]=limits[name]
        policy=Policy(**kwargs)
        result=_plan(trial,cost_floor_pct=limits['round_trip_cost_floor_pct'],
            cost_quote_id=limits['cost_quote_id'],research_eligible=limits.get('research_eligible') is True,
            depth_notional=limits['depth_notional'],policy=policy)
    except (ValueError,KeyError,TypeError,ArithmeticError):
        result={'version':VERSION,'action':'NO_TRADE','reason':'INVALID_OR_MISSING_VIRTUAL_LIMITS',
                'selected_notional':'0','execution_authority':'NONE','canonical_trade':False,
                'promotion_action':'NONE','execution_eligible':False,
                'budget_semantics':'RESEARCH_VIRTUAL_BUDGET'}
    return {**result,'selection_status':'SELECTED' if result['action']=='RESEARCH_LONG' else 'ABSTAIN',
            'entry_notional':result['selected_notional'],
            'risk_usd':result.get('estimated_risk_usd'),
            'costs_floor_pct':result.get('round_trip_cost_floor_pct'),
            'policy_hash':result.get('policy',{}).get('policy_hash')}


def exit_reason(entry, observed_net_return_pct, age_seconds, peak_net_return_pct=None):
    """First observed modeled liquidation trigger, never a stop-price fill.

    Returns None to hold. Returns are percentage points AFTER the existing
    depth/fees model. A caller must use the actual observed book when settling;
    gaps can lose more than the frozen sizing budget. MFE/MAE cannot be assumed
    continuous or attainable between sparse observations.
    """
    try:
        if entry.get('action')!='RESEARCH_LONG':return None
        value=_decimal(observed_net_return_pct);age=_decimal(age_seconds)
        if age<0:return None
        policy=entry['policy']
        if value<=_decimal(entry['protective_net_return_pct']):return 'PROTECTIVE_STOP'
        if value>=_decimal(entry['profit_target_net_return_pct']):return 'PROFIT_TARGET'
        if peak_net_return_pct is not None:
            peak=_decimal(peak_net_return_pct)
            if (peak>=_decimal(policy['profit_protect_arm_pct']) and peak>=value
                    and value<=peak-_decimal(policy['profit_giveback_pct'])):
                return 'PROFIT_PROTECTION'
        if age>=policy['holding_seconds']:return 'TIME_EXIT'
        return None
    except (ValueError,KeyError,TypeError,ArithmeticError):
        return None
