"""Forward-only receipts for the Brain inputs actually read by evaluate_opportunity.

Does not certify an exchange quote, an executed fill, a complete round trip, or
profitability. Original snapshots commit before consumption; decisions/receipts
commit together. Legacy decisions are never reconstructed.
"""
from copy import deepcopy
from dataclasses import asdict, is_dataclass, replace
from datetime import datetime, timezone
from decimal import Decimal
from functools import wraps
import hashlib
import inspect
import json
import logging
import os
from pathlib import Path
import uuid

from paper_fill_provenance import _time

_INSTALLED = False
log=logging.getLogger('paper-brain-consumption')


class DecisionEvidenceUnavailable(ValueError):
    """This candidate cannot receive original decision-consumption proof."""


def paper_only():
    return (os.getenv('EXECUTION_MODE','paper').lower()=='paper'
        and os.getenv('ENABLE_BROKER_SUBMISSION','false').lower()=='false'
        and os.getenv('LIVE_TRADING_ARMED','false').lower()=='false')


def _encode(value):
    if isinstance(value, datetime):
        if value.tzinfo is None:raise ValueError('naive evidence timestamp')
        return value.astimezone(timezone.utc).isoformat()
    if isinstance(value, Decimal):
        if not value.is_finite():raise ValueError('nonfinite evidence')
        return str(value)
    if is_dataclass(value):return asdict(value)
    raise TypeError('unsupported evidence value')


def canonical(value):
    raw=json.dumps(value,sort_keys=True,separators=(',',':'),ensure_ascii=False,allow_nan=False,default=_encode)
    if len(raw.encode())>1048576:raise ValueError('decision evidence exceeds bound')
    return raw


def digest(raw):return hashlib.sha256(raw.encode('utf-8')).hexdigest()


def storage_diagnostics(conn):
    """Read-only sizes and bounded payload samples, never a capacity approval.

    Samples are unordered, at most 1000 rows per table, and need not represent
    production traffic. Relation sizes include indexes/TOAST, but exclude WAL,
    replicas and retained source rows. No exact full-table count is requested.
    """
    if conn.execute("SELECT current_setting('transaction_read_only') AS ro").fetchone()['ro'] != 'on':
        raise RuntimeError('read-only storage diagnostics required')
    result = {'sample_limit': 1000, 'total_relation_bytes': 0, 'tables': {}}
    for table, columns in (
        ('paper_brain_snapshots', 'octet_length(canonical_snapshot)'),
        ('paper_brain_decisions', 'octet_length(canonical_input)+octet_length(canonical_output)'),
        ('paper_brain_consumption_receipts', '0'),
        ('paper_brain_fill_links', '0'),
    ):
        row = conn.execute('SELECT to_regclass(%s) AS relation', (table,)).fetchone()
        if not row['relation']:
            result['tables'][table] = {'status': 'UNAVAILABLE'}
            continue
        size = conn.execute('SELECT pg_total_relation_size(%s::regclass) AS bytes', (table,)).fetchone()['bytes']
        # Identifiers/expressions come only from the static allowlist above.
        sample = conn.execute(f'''SELECT count(*) AS sampled_rows,
            avg(payload_bytes)::float8 AS mean_canonical_payload_bytes,
            max(payload_bytes) AS max_canonical_payload_bytes
            FROM (SELECT {columns} AS payload_bytes FROM {table} LIMIT 1000) sample''').fetchone()
        result['tables'][table] = {'status': 'AVAILABLE', 'relation_bytes': size, **sample}
        result['total_relation_bytes'] += size
    return result


def _value(signal,key):
    return signal.get(key) if isinstance(signal,dict) else getattr(signal,key,None)


def persist_snapshot(conn, *, market, symbol, signal, brain):
    """Caller must commit this transaction before reading the snapshot for use."""
    if not paper_only():raise ValueError('paper provenance disarmed boundary')
    if market not in {'cash','crypto'} or not symbol:raise ValueError('invalid decision scope')
    signal_id,forecast_id=_value(signal,'signal_id'),_value(signal,'forecast_id')
    if signal_id is None or not forecast_id:raise ValueError('original signal and forecast required')
    # Share the existing entry/retention lock while copying original evidence.
    from database import ENTRY_EVIDENCE_RETENTION_LOCK
    conn.execute('SELECT pg_advisory_xact_lock_shared(%s)',(ENTRY_EVIDENCE_RETENTION_LOCK,))
    rows=conn.execute('''SELECT to_jsonb(f) AS forecast,to_jsonb(s) AS signal FROM forecasts f JOIN signals s
        ON s.id=f.signal_id AND s.market=f.market AND s.symbol=f.symbol
        WHERE f.forecast_id=%s AND f.signal_id::text=%s AND f.market=%s AND f.symbol=%s
        LIMIT 2''',(forecast_id,str(signal_id),market,symbol)).fetchall()
    if len(rows)!=1:raise ValueError('missing or conflicting original forecast')
    forecast=dict(rows[0]['forecast'])
    now=conn.execute('SELECT clock_timestamp() AS now').fetchone()['now']
    created,quoted=_time(forecast.get('created_at')),_time(forecast.get('source_quote_timestamp'))
    signal_created=_time(rows[0]['signal'].get('created_at'))
    if (created is None or quoted is None or quoted>created or created>now
            or signal_created is None or signal_created>now
            or not forecast.get('model') or not forecast.get('model_version')):
        raise ValueError('invalid or future forecast evidence')
    payload={'contract':'paper-brain-input-v1','market':market,'symbol':symbol,
        'signal_id':str(signal_id),'forecast_id':forecast_id,'forecast':forecast,
        'original_signal':rows[0]['signal'],'brain':brain}
    raw=canonical(payload);identity='brain-snapshot:'+digest(raw)
    conn.execute('''INSERT INTO paper_brain_snapshots
        (brain_snapshot_id,brain_snapshot_hash,canonical_snapshot,source_forecast_row_id,source_signal_id)
        VALUES(%s,%s,%s,%s,%s) ON CONFLICT(brain_snapshot_id) DO NOTHING''',
        (identity,digest(raw),raw,forecast['id'],rows[0]['signal']['id']))
    return identity


def consume_snapshot(conn, snapshot_id, *, decision_input, feature_version, model_version):
    """Read a committed original snapshot at the decision boundary, before use."""
    row=conn.execute('SELECT * FROM paper_brain_snapshots WHERE brain_snapshot_id=%s',(snapshot_id,)).fetchone()
    if not row or digest(row['canonical_snapshot'])!=row['brain_snapshot_hash']:
        raise ValueError('missing or corrupt Brain snapshot')
    clock=conn.execute('SELECT clock_timestamp() AS now,pg_current_xact_id()::text AS xid').fetchone()
    consumed=clock['now']
    if str(row['created_xid'])==clock['xid']:raise ValueError('Brain snapshot not committed before consumption')
    if row['created_at']>consumed:raise ValueError('future Brain snapshot')
    if not feature_version or not model_version:raise ValueError('missing input/model version')
    snapshot=json.loads(row['canonical_snapshot'])
    bound={'brain_snapshot_id':snapshot_id,'brain_snapshot_hash':row['brain_snapshot_hash'],
        'arguments':decision_input,'feature_version':feature_version,'model_version':model_version}
    raw=canonical(bound)
    return {'snapshot':snapshot,'brain_snapshot_id':snapshot_id,'brain_snapshot_hash':row['brain_snapshot_hash'],
        'canonical_input':raw,'decision_input_hash':digest(raw),'consumed_at':consumed,
        'feature_version':feature_version,'model_version':model_version}


def persist_decision(conn, consumption, output, *, decision_id):
    snapshot=consumption['snapshot']
    decision=conn.execute('''INSERT INTO paper_brain_decisions
        (decision_id,market,symbol,signal_id,forecast_id,canonical_input,decision_input_hash,canonical_output)
        VALUES(%s,%s,%s,%s,%s,%s,%s,%s) RETURNING decided_at''',
        (decision_id,snapshot['market'],snapshot['symbol'],snapshot['signal_id'],snapshot['forecast_id'],
         consumption['canonical_input'],consumption['decision_input_hash'],canonical(output))).fetchone()
    conn.execute('''INSERT INTO paper_brain_consumption_receipts
        (decision_id,forecast_id,brain_snapshot_id,brain_snapshot_hash,feature_version,model_version,consumed_at,decision_input_hash)
        VALUES(%s,%s,%s,%s,%s,%s,%s,%s)''',
        (decision_id,snapshot['forecast_id'],consumption['brain_snapshot_id'],consumption['brain_snapshot_hash'],
         consumption['feature_version'],consumption['model_version'],consumption['consumed_at'],consumption['decision_input_hash']))
    return decision['decided_at']


def verify_decision(conn, decision_id, *, market, symbol, signal_id, forecast_id):
    records=conn.execute('''SELECT to_jsonb(d) AS decision,to_jsonb(r) AS receipt,to_jsonb(s) AS snapshot
        FROM paper_brain_decisions d JOIN paper_brain_consumption_receipts r USING(decision_id)
        JOIN paper_brain_snapshots s USING(brain_snapshot_id) WHERE d.decision_id=%s''',(decision_id,)).fetchall()
    if len(records)!=1:return False
    try:
        d,r,s=(records[0][k] for k in ('decision','receipt','snapshot'))
        snapshot=json.loads(s['canonical_snapshot']);inputs=json.loads(d['canonical_input'])
        return (d['market']==snapshot['market']==market and d['symbol']==snapshot['symbol']==symbol
            and d['signal_id']==snapshot['signal_id']==str(signal_id)
            and d['forecast_id']==r['forecast_id']==snapshot['forecast_id']==forecast_id
            and digest(s['canonical_snapshot'])==s['brain_snapshot_hash']==r['brain_snapshot_hash']==inputs['brain_snapshot_hash']
            and inputs['brain_snapshot_id']==s['brain_snapshot_id']
            and digest(d['canonical_input'])==d['decision_input_hash']==r['decision_input_hash']
            and inputs['feature_version']==r['feature_version'] and inputs['model_version']==r['model_version']
            and str(inputs['arguments']['signal']['signal_id'])==d['signal_id']
            and inputs['arguments']['signal']['forecast_id']==d['forecast_id']
            and str(s['created_xid'])!=str(d['decision_xid'])
            and snapshot['forecast']['id']==s['source_forecast_row_id']
            and snapshot['original_signal']['id']==s['source_signal_id']
            and _time(s['created_at'])<=_time(r['consumed_at'])<=_time(d['decided_at']))
    except (ValueError,KeyError,TypeError):return False


def install_paper_brain_consumption(*, connect_factory=None):
    global _INSTALLED
    if _INSTALLED:return False
    if not paper_only():return False
    import oracle_intelligence as intelligence
    import opportunity_engine
    import oracle_bot
    from database import connect as default_connect
    connect=connect_factory or default_connect
    original=intelligence.evaluate_opportunity
    signature=inspect.signature(original)
    import engine
    feature_version='features-v1:'+digest(canonical({name:digest(Path(inspect.getfile(fn)).read_text())
        for name,fn in (('setup_features',intelligence.feature_vector),('market_features',engine.analyze_market))}))
    dependencies=('assess_trade','assess_market_memory','assess_global_intelligence','assess_opportunity_radar',
        'assess_scenarios','assess_hybrid_confluence','assess_capital_allocation','assess_portfolio_supercomputer','finalize_decision')
    model_files={name:digest(Path(inspect.getfile(getattr(intelligence,name))).read_text()) for name in dependencies}
    model_files['evaluate_opportunity']=digest(Path(inspect.getfile(original)).read_text())
    model_version='oracle-intelligence-v1:'+digest(canonical(model_files))

    @wraps(original)
    def recorded(signal, **kwargs):
        if not paper_only():raise ValueError('paper-only decision receipts required')
        bound=signature.bind(signal,**kwargs);bound.apply_defaults()
        values=deepcopy(dict(bound.arguments));frozen=values.pop('signal')
        scope=values['market'];symbol=str(_value(frozen,'symbol') or '')
        records=values['historical_records']
        if values['use_market_memory'] and records is None:records=intelligence.load_trade_memory(scope,symbol)
        values['historical_records']=records
        history=intelligence.crypto_history_context(frozen,market=scope)
        values['_provenance_crypto_history']=history
        frozen_fields=dict(frozen) if isinstance(frozen,dict) else vars(frozen)
        brain={'signal_brain_inputs':{k:deepcopy(v) for k,v in frozen_fields.items()
            if k.startswith('brain_') or k.startswith('research_')},
            'trade_memory':records,'crypto_history':history}
        with connect() as writer:
            snapshot_id=persist_snapshot(writer,market=scope,symbol=symbol,signal=frozen,brain=brain)
        # Read back the committed snapshot and use those exact values, not a
        # concurrent Brain update or a later auditor's reconstructed context.
        with connect() as writer:
            c=consume_snapshot(writer,snapshot_id,decision_input={'signal':frozen_fields,'parameters':values,
                'features':intelligence.feature_vector(frozen),
                'runtime_policy':{key:getattr(intelligence,key) for key in ('EXECUTION_MODE','PAPER_BROKER_MODE',
                    'SUPER_HYBRID_ENABLED','SUPER_HYBRID_PAPER_IMPACT_ENABLED')}},
                feature_version=feature_version,model_version=model_version)
            consumed=c['snapshot']['brain']
            for key,value in consumed['signal_brain_inputs'].items():
                if isinstance(frozen,dict):frozen[key]=value
                else:setattr(frozen,key,value)
            values['historical_records']=consumed['trade_memory']
            values['_provenance_crypto_history']=consumed['crypto_history']
            result=original(frozen,**values)
            did='brain-decision:'+str(uuid.uuid4())
            result=replace(result,decision_receipt_id=did)
            decided_at=persist_decision(writer,c,result.to_dict(),decision_id=did)
        if isinstance(signal,dict):signal.update(entry_decision_id=did,decision_receipt_id=did,decision_timestamp=decided_at.isoformat())
        else:
            setattr(signal,'entry_decision_id',did);setattr(signal,'decision_receipt_id',did)
            setattr(signal,'decision_timestamp',decided_at.isoformat())
        log.info('PAPER BRAIN CONSUMPTION | decision_id=%s | forecast_id=%s | brain_snapshot_id=%s | input_hash=%s | committed=True | execution_authority=NONE',
            did,_value(signal,'forecast_id'),snapshot_id,c['decision_input_hash'])
        return result

    @wraps(original)
    def guarded(signal,**kwargs):
        try:return recorded(signal,**kwargs)
        except Exception as exc:
            raise DecisionEvidenceUnavailable('decision evidence unavailable: '+type(exc).__name__) from exc

    intelligence.evaluate_opportunity=guarded
    opportunity_engine.evaluate_opportunity=guarded
    oracle_bot.evaluate_opportunity=guarded
    _INSTALLED=True
    log.info('PAPER BRAIN CONSUMPTION | status=INSTALLED | scope=evaluate_opportunity | live_trading=DISARMED | broker_submission=NONE')
    return True


def link_entry_fill(conn, *, signal, market, symbol, lot_id, ledger_trade_id, execution_trade_id):
    if not _INSTALLED:return
    if not paper_only():raise ValueError('paper-only receipt linkage required')
    did=_value(signal,'decision_receipt_id')
    if not did or _value(signal,'entry_decision_id')!=did or not verify_decision(conn,did,market=market,symbol=symbol,
            signal_id=_value(signal,'signal_id'),forecast_id=_value(signal,'forecast_id')):
        raise ValueError('entry missing original Brain consumption receipt')
    from market_memory import feature_vector
    inputs=json.loads(conn.execute('SELECT canonical_input FROM paper_brain_decisions WHERE decision_id=%s',
        (did,)).fetchone()['canonical_input'])['arguments']
    if canonical(inputs['features'])!=canonical(feature_vector(signal)):
        raise ValueError('entry features differ from consumed decision inputs')
    for key,value in inputs['signal'].items():
        if key.startswith('brain_') or key.startswith('research_'):
            if canonical(_value(signal,key))!=canonical(value):raise ValueError('entry Brain inputs changed after decision')
    fills=conn.execute('SELECT fill_id FROM paper_fills WHERE canonical_trade_id=%s',(execution_trade_id,)).fetchall()
    if len(fills)!=1:raise ValueError('entry missing exact canonical fill')
    conn.execute('''INSERT INTO paper_brain_fill_links(fill_id,lot_id,ledger_trade_id,decision_id)
        VALUES(%s,%s,%s,%s)''',(fills[0]['fill_id'],lot_id,ledger_trade_id,did))


def audit_forward_trade(conn, *, entry_trade_id, market):
    """Full-chain qualification is separate from any individual proof component.

    Bounded to 500 exits/lot, original explicit IDs only; no later quote/forecast
    substitution. This validates paper evidence, not live execution or promotion.
    """
    from paper_fill_provenance import (PROVENANCE_VERSION,attach_exact_fill_lot_evidence,
        load_fill_lot_allocations,number,close)
    from paper_pricing_receipt_audit import audit_sample_pricing_receipts
    from paper_trade_evidence_audit import entry_reference_reasons
    result={'brain_consumption_verified':False,'fully_verified':False,'reasons':[],
        'execution_impact':'NONE','strategy_promotion':'NOT_EVALUATED'}
    def reject(reason):
        result['reasons'].append(reason)
        return result
    if conn.execute("SELECT current_setting('transaction_read_only') AS ro").fetchone()['ro']!='on':
        raise ValueError('forward audit requires read-only transaction')
    if not conn.execute("SELECT to_regclass('paper_brain_fill_links') AS relation").fetchone()['relation']:
        return reject('original_brain_receipt_contract_unavailable')
    links=conn.execute('''SELECT b.*,to_jsonb(t) AS entry,to_jsonb(l) AS lot,d.decided_at,d.canonical_input
        FROM paper_brain_fill_links b JOIN trade_ledger t ON t.trade_id=b.ledger_trade_id
        JOIN position_lots l ON l.lot_id=b.lot_id JOIN paper_brain_decisions d ON d.decision_id=b.decision_id
        WHERE b.ledger_trade_id=%s AND t.market=%s AND t.side='BUY' ''',
        (entry_trade_id,market)).fetchall()
    if len(links)!=1:return reject('missing_original_entry_brain_link')
    link=links[0];entry,lot=link['entry'],link['lot']
    if not verify_decision(conn,link['decision_id'],market=market,symbol=entry['symbol'],
            signal_id=entry.get('entry_signal_id'),forecast_id=entry.get('entry_forecast_id')):
        return reject('unverified_original_brain_consumption')
    if (entry.get('entry_decision_id')!=link['decision_id'] or lot.get('entry_decision_id')!=link['decision_id']
            or _time(entry.get('decision_timestamp'))!=link['decided_at']
            or _time(entry.get('entry_time'))<link['decided_at']):
        return reject('entry_does_not_match_original_decision')
    inputs=json.loads(link['canonical_input'])['arguments']
    original_features=inputs['signal'].get('feature_snapshot')
    if not isinstance(original_features,dict) or not original_features:original_features=inputs['features']
    if canonical(original_features)!=canonical(entry.get('feature_snapshot')):
        return reject('entry_features_not_bound_to_original_decision')
    signals=conn.execute('SELECT * FROM signals WHERE id::text=%s',(entry.get('entry_signal_id'),)).fetchall()
    forecasts=conn.execute('SELECT * FROM forecasts WHERE forecast_id=%s LIMIT 2',(entry.get('entry_forecast_id'),)).fetchall()
    failures=entry_reference_reasons(entry,lot,signals,forecasts)
    if failures:
        result['reasons'].extend(failures)
        return result
    original=conn.execute('''SELECT s.canonical_snapshot FROM paper_brain_snapshots s
        JOIN paper_brain_consumption_receipts r USING(brain_snapshot_id) WHERE r.decision_id=%s''',
        (link['decision_id'],)).fetchone()
    original=json.loads(original['canonical_snapshot'])
    if canonical(dict(forecasts[0]))!=canonical(original['forecast']) or canonical(dict(signals[0]))!=canonical(original['original_signal']):
        return reject('original_forecast_or_signal_changed_after_consumption')
    result['brain_consumption_verified']=True
    exits=conn.execute('''SELECT t.* FROM paper_fill_lot_links a JOIN trade_ledger t ON t.trade_id=a.ledger_trade_id
        WHERE a.lot_id=%s AND t.side='SELL' ORDER BY t.exit_time,t.trade_id LIMIT 501''',(link['lot_id'],)).fetchall()
    if not exits:return reject('no_finalized_exit')
    if len(exits)>500:return reject('exit_audit_bound_exceeded')
    if number(lot.get('quantity_remaining'))!=0:return reject('open_or_partial_position_lot')
    ledger=[dict(entry)]+[dict(r) for r in exits]
    allocations=load_fill_lot_allocations(conn,market=market,trade_ids=[r['trade_id'] for r in ledger])
    checked=attach_exact_fill_lot_evidence(ledger,allocations)
    if any(r.get('_fill_accounting_provenance')!=PROVENANCE_VERSION for r in checked):
        return reject('incomplete_original_fill_or_cost_graph')
    opened=number(lot.get('quantity_opened'))
    closed=sum((number(r['quantity']) for r in checked[1:]),start=number(0))
    if not close(opened,closed,quantity=True):return reject('unfinalized_original_inventory')
    pricing=audit_sample_pricing_receipts(conn,allocations)
    if not pricing['sample_fills'] or pricing['sample_fills']!=pricing['consistent_original_receipts']:
        return reject('unverified_original_entry_or_exit_pricing_receipts')
    fill_ids=sorted({a['fill_id'] for a in allocations})
    receipts=conn.execute('SELECT evidence FROM paper_fill_price_evidence WHERE fill_id=ANY(%s)',(fill_ids,)).fetchall()
    for receipt in receipts:
        evidence=receipt['evidence'];execution=evidence['execution']
        cap=number(execution.get('max_adverse_fill_pct'))
        vwap=number(execution.get('depth_vwap'));reference=number(execution.get('reference_price'))
        if cap is None or cap<0 or vwap is None or reference is None or execution.get('fee_excluded_from_fill_price') is not True:
            return reject('missing_original_fill_cost_policy')
        adverse=min(cap,sum((number(execution[k]) for k in ('slippage_pct','market_impact_pct','latency_pct')),start=number(0)))
        executable=max(reference,vwap) if execution['side']=='BUY' else min(reference,vwap)
        expected=executable*(1+adverse) if execution['side']=='BUY' else executable*max(number('.000001'),1-adverse)
        if not close(execution.get('executable_reference'),executable) or not close(execution['price'],expected):
            return reject('fill_price_does_not_match_original_depth_and_cost_policy')
    net=sum((number(r['gross_pnl'])-number(r['_canonical_fill_fee'])
        -number(r['_canonical_entry_fill_fee'])*number(r['quantity'])/number(r['_canonical_entry_quantity'])
        for r in checked[1:]),start=number(0))
    result.update(fully_verified=True,modeled_paper_net_pnl=str(net),
        fee_provenance='EXACT_RECORDED_MODELED_PAPER_COSTS',exit_ledger_rows=len(exits))
    return result
