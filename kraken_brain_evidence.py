"""Durable original Kraken research decisions in Brain, never trade outcomes."""
from datetime import datetime, timezone
import json
from kraken_reference_book import timestamp


def research_source(row):
    p=row['payload'];q=p.get('primary_quote',{});forecast=p.get('forecast')
    if (p.get('status') not in {'SHADOW_FORECAST','ABSTAINED'} or p.get('execution_authority')!='NONE'
            or q.get('execution_quote_eligible') is not False or q.get('reference_only') is not True
            or q.get('quote_verified') is not False or q.get('symbol')!=p.get('symbol')
            or not p.get('history_fingerprint')):raise ValueError('invalid research authority')
    quote_time=timestamp(q['quote_timestamp']);observed=timestamp(row['observed_at'].isoformat() if isinstance(row['observed_at'],datetime) else row['observed_at'])
    closed=timestamp(p['history_last_closed_bar'])
    if closed>=quote_time or quote_time>observed or timestamp(q['received_at'])>observed:
        raise ValueError('invalid research chronology')
    if p['status']=='SHADOW_FORECAST':
        if not isinstance(forecast,dict) or not forecast.get('forecast_id') or forecast.get('validation_status')!='shadow':
            raise ValueError('invalid forecast identity')
    elif forecast is not None or not p.get('reason'):
        raise ValueError('invalid abstention receipt')
    return {'key':'kraken-research:'+str(row['id']),'provider':q['provider'],'symbol':p['symbol'],
        'title':p['symbol']+' '+p['status'],'observed_at':observed,
        'metadata':{'evidence_id':row['id'],'source_table':'kraken_research_evidence','market':'crypto',
            'evidence':p,'context_only':True,'ranking_eligible':False,'trade_outcome':False,'delivery':'IMMUTABLE_RESEARCH_DECISION','current_quote':False,
            'execution_authority':'NONE','broker_submission':'NONE'}}


def sync_research_sources(conn,limit=250):
    if not 1<=limit<=500:raise ValueError('invalid batch size')
    if not conn.execute("SELECT to_regclass('kraken_research_evidence') AS relation").fetchone()['relation']:
        return {'status':'UNAVAILABLE','inserted':0,'skipped':0,'execution_impact':'NONE'}
    records=conn.execute('''SELECT e.id,e.observed_at,e.payload FROM kraken_research_evidence e
        WHERE NOT EXISTS(SELECT 1 FROM oracle_brain_sources s WHERE s.source_key='kraken-research:'||e.id::text)
        ORDER BY e.id LIMIT %s''',(limit,)).fetchall()
    inserted=skipped=0
    for row in records:
        try:s=research_source(row)
        except (KeyError,ValueError,TypeError):skipped+=1;continue
        receipt=conn.execute('''INSERT INTO oracle_brain_sources
            (source_key,source_type,provider,category,symbol,title,body,source_ref,observed_at,
             source_quality,freshness_score,confidence,status,metadata,execution_impact)
            VALUES(%s,'crypto_research_decision',%s,'market_research',%s,%s,
                'Original research decision; not a trade or execution approval.',%s,%s,0.5,1,0,'active',%s::jsonb,'NONE')
            ON CONFLICT(source_key) DO NOTHING RETURNING source_key''',
            (s['key'],s['provider'],s['symbol'],s['title'],'kraken_research_evidence:'+str(row['id']),
             s['observed_at'],json.dumps(s['metadata'],allow_nan=False,default=str))).fetchone()
        inserted+=receipt is not None
    result={'status':'SYNCED','inserted':inserted,'skipped':skipped,'execution_impact':'NONE'}
    conn.execute('''INSERT INTO oracle_brain_learning_state(pipeline_key,market,last_sync_at,last_result)
        VALUES('kraken_research','crypto',NOW(),%s::jsonb) ON CONFLICT(pipeline_key,market)
        DO UPDATE SET last_sync_at=EXCLUDED.last_sync_at,last_result=EXCLUDED.last_result''',(json.dumps(result),))
    return result



RESEARCH_EVIDENCE_MAX_ID = 5000


def read_evidence_capacity(conn):
    """Observe immutable evidence ID occupancy, never count holes as free slots."""
    try:
        value = conn.execute(
            'SELECT COALESCE(MAX(id),0) AS highest_id FROM kraken_research_evidence'
        ).fetchone()
        highest_id = value['highest_id']
        if (type(highest_id) is not int or highest_id < 0
                or highest_id > RESEARCH_EVIDENCE_MAX_ID):
            return {'status':'UNAVAILABLE','reason':'INVALID_HIGHEST_ID',
                    'execution_impact':'NONE'}
        remaining = RESEARCH_EVIDENCE_MAX_ID - highest_id
        if remaining == 0:
            status = 'CAPACITY_REACHED'
        elif remaining <= 250:
            status = 'CAPACITY_CRITICAL'
        elif remaining <= 1000:
            status = 'CAPACITY_WARNING'
        else:
            status = 'CAPACITY_NORMAL'
        return {'status':status,'highest_id':highest_id,
                'max_id':RESEARCH_EVIDENCE_MAX_ID,'remaining_id_slots':remaining,
                'execution_impact':'NONE'}
    except Exception as exc:
        return {'status':'UNAVAILABLE','reason_class':type(exc).__name__,
                'execution_impact':'NONE'}


def committed_receipt(conn):
    """Read committed rows after the caller's ingestion transaction has exited."""
    count=conn.execute('''SELECT COUNT(*) AS matched FROM oracle_brain_sources s
        JOIN kraken_research_evidence e ON s.source_key='kraken-research:'||e.id::text
        WHERE s.metadata->'evidence'=e.payload AND s.execution_impact='NONE'
        AND s.metadata->>'execution_authority'='NONE' ''').fetchone()['matched']
    sample=conn.execute('''SELECT e.id,e.payload->>'symbol' AS symbol,e.payload->>'status' AS decision,
        e.payload->'forecast'->>'forecast_id' AS forecast_id FROM oracle_brain_sources s
        JOIN kraken_research_evidence e ON s.source_key='kraken-research:'||e.id::text
        WHERE s.metadata->'evidence'=e.payload ORDER BY e.id DESC LIMIT 10''').fetchall()
    expected=conn.execute('SELECT COUNT(*) AS n FROM kraken_research_evidence').fetchone()['n']
    return {'expected':expected,'matched':count,'unmatched':expected-count,'sample':[dict(r) for r in sample],
        'evidence_capacity':read_evidence_capacity(conn),'execution_impact':'NONE',
        'trade_outcomes':'NOT_INFERRED','profitability':'NOT_EVALUATED'}
