"""Bounded prospective Kraken book observations; no orders or strategy authority.

Freeze entry/costs before observing 5/15-minute exits. Original source records are
never rewritten. Outcomes are context-only Brain sources, never canonical trades.
"""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from decimal import Decimal, localcontext
import hashlib
import json
import logging
import os

from coinbase_paper_pricing import depth_price, FEE
from kraken_paper_pricing import KrakenPaperPricing, SOURCE, validate_kraken_book
from kraken_reference_book import exact_decimal, timestamp
from kraken_research_data import ResearchUnavailable

log = logging.getLogger('prospective-paper-research')
VERSION = 'kraken-prospective-costed-v1'
POLICY = {'version':VERSION, 'entry_notional':'25', 'holding_seconds':[300,900],
          'exit_window_seconds':60, 'source_id_slots':1000, 'max_batch':32,
          'fee_pct':'0.006', 'minimum_slippage_pct':'0.00135', 'minimum_latency_pct':'0.0003',
          'cost_rule':'FREEZE_EXISTING_PAPER_ASSUMPTIONS_AT_ENTRY', 'execution_authority':'NONE'}


def encoded(value):return json.dumps(value,sort_keys=True,allow_nan=False,separators=(',',':'))
def digest(value):return hashlib.sha256(encoded(value).encode()).hexdigest()
POLICY_HASH = digest(POLICY)
CONFIG_KEY = 'prospective-paper-config:'+VERSION


def enabled():
    from kraken_paper_pricing import enabled as pricing_enabled
    return (os.getenv('KRAKEN_FORWARD_PAPER_PILOT_ENABLED','false').lower()=='true' and pricing_enabled())


def checked_quote(quote, at):
    if not isinstance(quote,dict) or quote.get('provider')!=SOURCE:
        raise ResearchUnavailable('ORIGINAL_KRAKEN_PAPER_RECEIPT_REQUIRED')
    rebuilt=validate_kraken_book(quote['symbol'],quote['original_reference'],quote['product_rules'],
                                 quote['product_metadata_received_at'],at)
    if encoded(quote)!=encoded(rebuilt):
        raise ResearchUnavailable('ALTERED_ORIGINAL_PAPER_RECEIPT')
    return quote


def costs_at_entry():
    from paper_execution_reality import _market_defaults
    defaults=_market_defaults('crypto')
    slip=exact_decimal(str(defaults['slippage_pct']))
    latency=exact_decimal(str(defaults['latency_pct']))
    values={'fee_pct':str(FEE), 'slippage_pct':str(max(Decimal('.00135'),slip)),
            'latency_pct':str(max(Decimal('.0003'),latency))}
    checked_costs(values)
    return values


def checked_costs(costs):
    fee,slip,latency=(exact_decimal(costs[k]) for k in ('fee_pct','slippage_pct','latency_pct'))
    if fee!=Decimal('.006') or slip<Decimal('.00135') or latency<Decimal('.0003') or slip+latency>Decimal('.05'):
        raise ResearchUnavailable('UNSUPPORTED_OR_NONFINITE_COST_ASSUMPTIONS')
    return fee,slip,latency


def priced_fill(quote,side,notional,costs):
    fee,slip,latency=checked_costs(costs)
    vwap=depth_price(quote,side,str(notional))
    price=vwap*(1+slip+latency if side=='BUY' else 1-slip-latency)
    return price,vwap,fee


def check_decision(state, forecast, reason, captured, symbol):
    if state == 'ABSTAINED':
        if forecast is not None or not reason:
            raise ResearchUnavailable('ABSTENTION_IDENTITY_REQUIRED')
    elif state == 'SHADOW_FORECAST':
        if (not isinstance(forecast,dict) or not forecast.get('forecast_id')
                or forecast.get('validation_status')!='shadow' or forecast.get('symbol')!=symbol
                or not forecast.get('model_version')
                or not 0 <= (captured-timestamp(forecast['generated_at'])).total_seconds() <= 30
                or timestamp(forecast['source_quote_timestamp']) > timestamp(forecast['generated_at'])):
            raise ResearchUnavailable('FORECAST_NOT_AVAILABLE_BEFORE_ENTRY')
    else:
        raise ResearchUnavailable('UNKNOWN_PROSPECTIVE_DECISION')


def capture_trial(result,pricing,at):
    """Capture a passive hypothesis, including abstentions, never approve a BUY."""
    if (not isinstance(pricing,KrakenPaperPricing) or not pricing.available()
            or result.get('status') not in {'SHADOW_FORECAST','ABSTAINED'}
            or result.get('data_role')!='KRAKEN_PRIMARY' or result.get('execution_authority')!='NONE'
            or not result.get('history_fingerprint')):
        raise ResearchUnavailable('UNSUPPORTED_PROSPECTIVE_RESEARCH_CONTEXT')
    forecast=deepcopy(result.get('forecast'))
    check_decision(result['status'],forecast,result.get('reason'),at,result['symbol'])
    quote=pricing.quote(result['symbol'])
    at=pricing.clock()  # Actual capture time, after any metadata/read latency.
    if not quote or not pricing.authentic(quote,result['symbol']):
        raise ResearchUnavailable('NO_AUTHENTIC_CURRENT_ENTRY_BOOK')
    checked_quote(quote,at)
    check_decision(result['status'],forecast,result.get('reason'),at,result['symbol'])
    with localcontext() as context:
        context.prec=80
        costs=costs_at_entry();notional=Decimal(POLICY['entry_notional'])
        price,vwap,fee=priced_fill(quote,'BUY',notional,costs)
        quantity=notional/price
        trial={'version':VERSION,'policy_hash':POLICY_HASH,'status':'CAPTURED',
            'captured_at':at.isoformat(),'symbol':result['symbol'],'decision_status':result['status'],
            'abstention_reason':result.get('reason'),'original_forecast':forecast,
            'model_identity':deepcopy(result.get('model_identity')),'history_fingerprint':result['history_fingerprint'],
            'entry_quote':deepcopy(quote),'costs':costs,'entry_notional':str(notional),
            'entry_fill_price':str(price),'entry_depth_vwap':str(vwap),'quantity':str(quantity),
            'entry_fee':str(notional*fee),'market_episode_proxy':at.astimezone(timezone.utc).date().isoformat(),
            'role':'PASSIVE_UNCONDITIONAL_ENTRY_HYPOTHESIS','canonical_trade':False,
            'execution_authority':'NONE','broker_submission':'NONE','promotion_action':'NONE'}
    trial={**trial,'trial_hash':digest(trial)}
    if len(encoded(trial).encode())>16384:
        raise ResearchUnavailable('PROSPECTIVE_ENTRY_TOO_LARGE')
    return trial


def validate_trial(trial):
    if (trial.get('version')!=VERSION or trial.get('policy_hash')!=POLICY_HASH or trial.get('status')!='CAPTURED'
            or trial.get('execution_authority')!='NONE' or trial.get('canonical_trade') is not False
            or trial.get('trial_hash')!=digest({k:v for k,v in trial.items() if k!='trial_hash'})):
        raise ResearchUnavailable('INVALID_ORIGINAL_PROSPECTIVE_TRIAL')
    captured=timestamp(trial['captured_at']);quote=checked_quote(trial['entry_quote'],captured)
    check_decision(trial['decision_status'],trial['original_forecast'],trial['abstention_reason'],captured,trial['symbol'])
    if trial.get('symbol')!=quote['symbol'] or trial.get('entry_notional')!=POLICY['entry_notional']:
        raise ResearchUnavailable('PROSPECTIVE_SCOPE_MISMATCH')
    with localcontext() as context:
        context.prec=80
        notional=exact_decimal(trial['entry_notional'])
        price,vwap,fee=priced_fill(quote,'BUY',notional,trial['costs'])
        if (exact_decimal(trial['entry_fill_price'])!=price or exact_decimal(trial['entry_depth_vwap'])!=vwap
                or exact_decimal(trial['entry_fee'])!=notional*fee or exact_decimal(trial['quantity'])!=notional/price):
            raise ResearchUnavailable('PROSPECTIVE_ENTRY_ECONOMICS_MISMATCH')
    return captured


def evaluate_trial(trial,exit_quote,horizon,at):
    """First actual book in a predeclared window; gaps remain unavailable."""
    if horizon not in POLICY['holding_seconds']:raise ValueError('unregistered holding period')
    captured=validate_trial(trial);due=captured+timedelta(seconds=horizon)
    if at<due:return None
    result={'version':VERSION,'policy_hash':POLICY_HASH,'trial_hash':trial['trial_hash'],
        'symbol':trial['symbol'],'horizon_seconds':horizon,'due_at':due.isoformat(),'evaluated_at':at.isoformat(),
        'decision_status':trial['decision_status'],'market_episode_proxy':trial['market_episode_proxy'],
        'status':'UNAVAILABLE','canonical_trade':False,'execution_authority':'NONE','promotion_action':'NONE'}
    if at>due+timedelta(seconds=POLICY['exit_window_seconds']):
        return {**result,'reason':'MISSED_PREDECLARED_EXIT_WINDOW'}
    if not exit_quote:return None
    checked_quote(exit_quote,at)
    if exit_quote['symbol']!=trial['symbol']:raise ResearchUnavailable('EXIT_SYMBOL_MISMATCH')
    observed=timestamp(exit_quote['quote_timestamp'])
    if observed<due:return None
    if observed>due+timedelta(seconds=POLICY['exit_window_seconds']):
        return {**result,'reason':'EXIT_BOOK_OUTSIDE_PREDECLARED_WINDOW'}
    with localcontext() as context:
        context.prec=80
        quantity=exact_decimal(trial['quantity'])
        price,vwap,fee=priced_fill(exit_quote,'SELL',quantity*exact_decimal(exit_quote['mid_price']),trial['costs'])
        proceeds=quantity*price;entry_notional=exact_decimal(trial['entry_notional'])
        entry_fee=exact_decimal(trial['entry_fee']);exit_fee=proceeds*fee
        gross=proceeds-entry_notional;net=gross-entry_fee-exit_fee
        result.update(status='COMPLETED_SIMULATION',entry_quote_id=trial['entry_quote']['quote_id'],
            exit_quote=deepcopy(exit_quote),quantity=str(quantity),exit_fill_price=str(price),exit_depth_vwap=str(vwap),
            gross_pnl=str(gross),entry_fee=str(entry_fee),exit_fee=str(exit_fee),net_pnl=str(net),
            net_return=str(net/(entry_notional+entry_fee)),costs=deepcopy(trial['costs']),
            fee_provenance='MODELED_NOT_ACCOUNT_VERIFIED',not_realized_trading_profit=True)
    return result


def source_key(source_id,horizon):return 'prospective-paper:'+VERSION+':'+str(source_id)+':'+str(horizon)


def _insert_source(conn,key,symbol,title,metadata,at):
    metadata={**metadata,'context_only':True,'ranking_eligible':False,'trade_outcome':False,
              'execution_authority':'NONE','broker_submission':'NONE'}
    payload=encoded(metadata)
    if len(payload.encode())>18000:raise ResearchUnavailable('PROSPECTIVE_OUTCOME_TOO_LARGE')
    return conn.execute('''INSERT INTO oracle_brain_sources
        (source_key,source_type,provider,category,symbol,title,body,source_ref,observed_at,
         source_quality,freshness_score,confidence,status,metadata,execution_impact)
        VALUES(%s,'crypto_forward_paper_simulation',%s,'market_research',%s,%s,
        'Prospective depth-priced hypothesis; not a canonical trade or promotion.',%s,%s,0.5,1,0,'active',%s::jsonb,'NONE')
        ON CONFLICT(source_key) DO NOTHING RETURNING source_key''',
        (key,SOURCE,symbol,title,key,at,payload)).fetchone() is not None


def pilot_config(conn,capacity,at):
    row=conn.execute('SELECT metadata FROM oracle_brain_sources WHERE source_key=%s',(CONFIG_KEY,)).fetchone()
    if not row:
        first=capacity.get('highest_id');maximum=capacity.get('max_id')
        if type(first) is not int or type(maximum) is not int or first>=maximum:return None
        config={'policy':POLICY,'policy_hash':POLICY_HASH,'first_source_id':first+1,
            'last_source_id':min(first+POLICY['source_id_slots'],maximum),'started_at':at.isoformat()}
        _insert_source(conn,CONFIG_KEY,None,'Frozen prospective paper pilot configuration',config,at)
        row=conn.execute('SELECT metadata FROM oracle_brain_sources WHERE source_key=%s',(CONFIG_KEY,)).fetchone()
    config=row['metadata']
    first,last=config.get('first_source_id'),config.get('last_source_id')
    if (config.get('policy_hash')!=POLICY_HASH or config.get('policy')!=POLICY
            or type(first) is not int or type(last) is not int
            or not 1 <= first <= last <= 10000 or last-first >= POLICY['source_id_slots']):
        raise ResearchUnavailable('FROZEN_PILOT_CONFIGURATION_MISMATCH')
    timestamp(config['started_at'])
    return config


def evaluate_pending(conn,pricing,config,at):
    from kraken_research_store import evidence_layout,evidence_sql
    source=evidence_sql(evidence_layout(conn),'id,payload,observed_at')
    prefix='prospective-paper:'+VERSION+':'
    records=conn.execute(f'''SELECT e.id,e.payload,e.observed_at,e.source_table FROM ({source}) e
        WHERE e.id BETWEEN %s AND %s AND e.payload->'forward_trial'->>'status'='CAPTURED'
          AND e.payload->'forward_trial'->>'version'=%s
          AND (NOT EXISTS(SELECT 1 FROM oracle_brain_sources s WHERE s.source_key=%s||e.id::text||':300')
            OR NOT EXISTS(SELECT 1 FROM oracle_brain_sources s WHERE s.source_key=%s||e.id::text||':900'))
        ORDER BY e.id LIMIT %s''',(config['first_source_id'],config['last_source_id'],VERSION,prefix,prefix,POLICY['max_batch'])).fetchall()
    keys=[source_key(r['id'],h) for r in records for h in POLICY['holding_seconds']]
    existing={r['source_key'] for r in conn.execute('SELECT source_key FROM oracle_brain_sources WHERE source_key=ANY(%s)',(keys,)).fetchall()} if keys else set()
    quotes={};inserted=0
    for row in records:
        trial=row['payload']['forward_trial']
        for horizon in POLICY['holding_seconds']:
            key=source_key(row['id'],horizon)
            if key in existing:continue
            try:
                captured=validate_trial(trial)
                observed=row['observed_at']
                if isinstance(observed,str):observed=timestamp(observed)
                parent=row['payload']
                if (timestamp(config['started_at'])>captured or observed<captured
                        or parent.get('symbol')!=trial['symbol']
                        or parent.get('status')!=trial['decision_status']
                        or parent.get('forecast')!=trial['original_forecast']
                        or parent.get('model_identity')!=trial['model_identity']
                        or parent.get('history_fingerprint')!=trial['history_fingerprint']):
                    raise ResearchUnavailable('SOURCE_TRIAL_CHRONOLOGY_OR_SCOPE_MISMATCH')
                due=captured+timedelta(seconds=horizon)
                if at<due:continue
                if at<=due+timedelta(seconds=POLICY['exit_window_seconds']) and trial['symbol'] not in quotes:
                    q=pricing.quote(trial['symbol'])
                    quotes[trial['symbol']]=q if q and pricing.authentic(q,trial['symbol']) else None
                try:
                    outcome=evaluate_trial(trial,quotes.get(trial['symbol']),horizon,pricing.clock())
                except ResearchUnavailable:
                    # A transient bad exit book is not a finalized result. Retry
                    # inside the fixed window; expire explicitly after its end.
                    continue
                if outcome is None:continue
            except (ValueError,KeyError,TypeError,ArithmeticError) as exc:
                # Preserve explicit missing/unusable evidence, never manufacture P&L.
                outcome={'version':VERSION,'status':'UNAVAILABLE','reason':type(exc).__name__,
                         'horizon_seconds':horizon,'execution_authority':'NONE','canonical_trade':False}
            metadata={**outcome,'source_evidence_id':row['id'],'source_table':row['source_table']}
            inserted+=_insert_source(conn,key,trial.get('symbol'),
                str(trial.get('symbol'))+' prospective '+str(horizon)+'s '+outcome['status'],metadata,pricing.clock())
    return inserted


def summarize(conn,config):
    """Bounded diagnostics; horizons share entries and are never independent trades."""
    keys=[source_key(i,h) for i in range(config['first_source_id'],config['last_source_id']+1)
          for h in POLICY['holding_seconds']]
    rows=conn.execute('''SELECT metadata FROM oracle_brain_sources
        WHERE source_key=ANY(%s) ORDER BY source_key''',(keys,)).fetchall()
    if len(rows)>2000:raise ResearchUnavailable('PROSPECTIVE_STORAGE_BOUND_EXCEEDED')
    grouped={};unavailable=0
    for row in rows:
        value=row['metadata'];identity=value.get('source_evidence_id')
        if type(identity) is not int or not config['first_source_id']<=identity<=config['last_source_id']:
            raise ResearchUnavailable('OUTCOME_OUTSIDE_FROZEN_PILOT')
        if value.get('status')!='COMPLETED_SIMULATION':unavailable+=1;continue
        grouped.setdefault(identity,{})[value['horizon_seconds']]=value
    horizons={}
    for horizon in POLICY['holding_seconds']:
        samples=[g[horizon] for g in grouped.values() if horizon in g]
        values=[Decimal(v['net_pnl']) for v in samples]
        horizons[str(horizon)]={'completed_simulations':len(values),
            'modeled_net_pnl':str(sum(values,Decimal(0))) if values else None,
            'utc_day_dependence_proxies':len({v['market_episode_proxy'] for v in samples})}
    pairs=[g for g in grouped.values() if all(h in g for h in POLICY['holding_seconds'])]
    deltas=[Decimal(g[900]['net_pnl'])-Decimal(g[300]['net_pnl']) for g in pairs
            if g[900]['trial_hash']==g[300]['trial_hash']]
    return {'version':VERSION,'status':'RESEARCH_ONLY','horizons':horizons,
        'distinct_entry_observations':len(grouped),'unavailable_outcomes':unavailable,
        'matched_simulated_entries':len(deltas),
        'modeled_900_minus_300_pnl':str(sum(deltas,Decimal(0))) if deltas else None,
        'independent_market_episodes':None,'canonical_shadow_exit_pairs':0,
        'realized_trading_profit':None,'promotion_action':'NONE','execution_authority':'NONE',
        'first_source_id':config['first_source_id'],'last_source_id':config['last_source_id']}
