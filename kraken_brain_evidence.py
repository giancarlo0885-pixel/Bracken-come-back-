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
    from kraken_research_store import (LEGACY_TABLE, OVERFLOW_TABLE, EXTENSION_TABLE,
        LEGACY_LIMIT, OVERFLOW_LIMIT, EXTENSION_LIMIT)
    table = row.get('source_table',LEGACY_TABLE)
    if (type(row.get('id')) is not int or
            not ((table == LEGACY_TABLE and 1 <= row['id'] <= LEGACY_LIMIT)
                 or (table == OVERFLOW_TABLE and LEGACY_LIMIT < row['id'] <= OVERFLOW_LIMIT)
                 or (table == EXTENSION_TABLE and OVERFLOW_LIMIT < row['id'] <= EXTENSION_LIMIT))):
        raise ValueError('invalid research storage identity')
    return {'key':'kraken-research:'+str(row['id']),'provider':q['provider'],'symbol':p['symbol'],
        'title':p['symbol']+' '+p['status'],'observed_at':observed,
        'metadata':{'evidence_id':row['id'],'source_table':table,'market':'crypto',
            'evidence':p,'context_only':True,'ranking_eligible':False,'trade_outcome':False,'delivery':'IMMUTABLE_RESEARCH_DECISION','current_quote':False,
            'execution_authority':'NONE','broker_submission':'NONE'}}


def sync_research_sources(conn,limit=250):
    if not 1<=limit<=500:raise ValueError('invalid batch size')
    if not conn.execute("SELECT to_regclass('kraken_research_evidence') AS relation").fetchone()['relation']:
        return {'status':'UNAVAILABLE','inserted':0,'skipped':0,'execution_impact':'NONE'}
    from kraken_research_store import evidence_layout, evidence_sql
    source = evidence_sql(evidence_layout(conn),'id,observed_at,payload')
    records=conn.execute(f'''SELECT e.id,e.observed_at,e.payload,e.source_table FROM ({source}) e
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
            (s['key'],s['provider'],s['symbol'],s['title'],s['metadata']['source_table']+':'+str(row['id']),
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
        from kraken_research_store import evidence_layout, evidence_sql
        layout = evidence_layout(conn)
        # Each table has a primary-key index. Per-table scalar MAX can use
        # its index instead of aggregating the combined UNION ALL rows.
        # Only trusted table names from evidence_layout enter this SQL.
        heads = [f'COALESCE((SELECT MAX(id) FROM {table}),0)' for table in layout['tables']]
        expression = heads[0] if len(heads) == 1 else f"GREATEST({','.join(heads)})"
        value = conn.execute(f'SELECT {expression} AS highest_id').fetchone()
        highest_id = value['highest_id']
        if (type(highest_id) is not int or highest_id < 0
                or highest_id > layout['max_id']):
            return {'status':'UNAVAILABLE','reason':'INVALID_HIGHEST_ID',
                    'execution_impact':'NONE'}
        remaining = layout['max_id'] - highest_id
        if remaining == 0:
            status = 'CAPACITY_REACHED'
        elif remaining <= 250:
            status = 'CAPACITY_CRITICAL'
        elif remaining <= 1000:
            status = 'CAPACITY_WARNING'
        else:
            status = 'CAPACITY_NORMAL'
        return {'status':status,'highest_id':highest_id,
                'max_id':layout['max_id'],'remaining_id_slots':remaining,
                'execution_impact':'NONE'}
    except Exception as exc:
        return {'status':'UNAVAILABLE','reason_class':type(exc).__name__,
                'execution_impact':'NONE'}


def committed_receipt(conn):
    """Read committed rows after the caller's ingestion transaction has exited."""
    from kraken_research_store import evidence_layout, evidence_sql, read_storage_footprint
    source = evidence_sql(evidence_layout(conn),'id,payload')
    count=conn.execute(f'''SELECT COUNT(*) AS matched FROM oracle_brain_sources s
        JOIN ({source}) e ON s.source_key='kraken-research:'||e.id::text
        WHERE s.metadata->'evidence'=e.payload AND s.metadata->>'source_table'=e.source_table AND s.metadata->>'evidence_id'=e.id::text AND s.source_ref=e.source_table||':'||e.id::text AND s.execution_impact='NONE'
        AND s.metadata->>'execution_authority'='NONE' ''').fetchone()['matched']
    sample=conn.execute(f'''SELECT e.id,e.payload->>'symbol' AS symbol,e.payload->>'status' AS decision,
        e.payload->'forecast'->>'forecast_id' AS forecast_id FROM oracle_brain_sources s
        JOIN ({source}) e ON s.source_key='kraken-research:'||e.id::text
        WHERE s.metadata->'evidence'=e.payload AND s.metadata->>'source_table'=e.source_table AND s.metadata->>'evidence_id'=e.id::text AND s.source_ref=e.source_table||':'||e.id::text AND s.execution_impact='NONE' AND s.metadata->>'execution_authority'='NONE' ORDER BY e.id DESC LIMIT 10''').fetchall()
    expected=conn.execute(f'SELECT COUNT(*) AS n FROM ({source}) e').fetchone()['n']
    return {'expected':expected,'matched':count,'unmatched':expected-count,'sample':[dict(r) for r in sample],
        'evidence_capacity':read_evidence_capacity(conn),'storage_footprint':read_storage_footprint(conn),'execution_impact':'NONE',
        'trade_outcomes':'NOT_INFERRED','profitability':'NOT_EVALUATED'}
