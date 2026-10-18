"""Read-only committed paper graph audit. Never repairs or qualifies trade records."""
from collections import Counter, defaultdict
from datetime import datetime, timezone
import json
import logging
import os

from paper_fill_provenance import (PROVENANCE_VERSION, _time, _same_json,
    attach_exact_fill_lot_evidence, load_fill_lot_allocations)

log = logging.getLogger('paper-trade-evidence')
SAMPLE_LIMIT = 50
ENTRY_IDS = ('entry_signal_id','entry_decision_id','entry_forecast_id','entry_quote_id')


def entry_reference_reasons(entry, lot, signals, forecasts):
    """Stored references must resolve causally; never choose the latest record."""
    reasons = []
    entered, decision = _time(entry.get('entry_time')), _time(entry.get('decision_timestamp'))
    if entered is None or decision is None or decision > entered:
        reasons.append('invalid_entry_decision_time')
    if any(entry.get(k) != lot.get(k) for k in ENTRY_IDS):
        reasons.append('lot_entry_reference_mismatch')
    if not _same_json(entry.get('feature_snapshot'),lot.get('feature_snapshot')):
        reasons.append('lot_entry_snapshot_mismatch')
    if _time(entry.get('decision_timestamp')) != _time(lot.get('decision_timestamp')):
        reasons.append('lot_entry_decision_time_mismatch')
    for field in ('entry_decision_id','entry_quote_id'):
        if not entry.get(field):reasons.append('missing_'+field)
    if not isinstance(entry.get('feature_snapshot'),dict) or not entry['feature_snapshot']:
        reasons.append('missing_entry_features')
    scope = (entry.get('market'),entry.get('symbol'))
    signal_id, forecast_id = entry.get('entry_signal_id'),entry.get('entry_forecast_id')
    matches = [r for r in signals if str(r.get('id')) == str(signal_id)
               and (r.get('market'),r.get('symbol')) == scope] if signal_id else []
    if not signal_id:
        reasons.append('missing_entry_signal_id')
    elif len(matches) != 1:
        reasons.append('unresolved_or_ambiguous_entry_signal')
    elif (_time(matches[0].get('created_at')) is None or entered is None
          or _time(matches[0]['created_at']) > entered):
        reasons.append('late_or_invalid_entry_signal')
    matches = [r for r in forecasts if r.get('forecast_id') == forecast_id
        and (r.get('market'),r.get('symbol')) == scope
        and str(r.get('signal_id')) == str(signal_id)] if forecast_id and signal_id else []
    if not forecast_id:
        reasons.append('missing_entry_forecast_id')
    elif len(matches) != 1:
        reasons.append('unresolved_or_ambiguous_entry_forecast')
    else:
        generated, quote = _time(matches[0].get('created_at')), _time(matches[0].get('source_quote_timestamp'))
        if (entered is None or generated is None or quote is None
                or generated > entered or quote > generated):
            reasons.append('late_or_invalid_entry_forecast')
    return reasons


def forecast_generation_evidence(entry, signals, forecasts):
    """Diagnose original entry evidence without inferring or repairing a link."""
    scope = (entry.get('market'), entry.get('symbol'))
    signal_id = entry.get('entry_signal_id')
    matches = [r for r in signals if str(r.get('id')) == str(signal_id)
               and (r.get('market'), r.get('symbol')) == scope] if signal_id else []
    if len(matches) != 1:
        return {'status': 'UNKNOWN_SIGNAL_UNRESOLVED'}
    signal = matches[0]
    entered, created = _time(entry.get('entry_time')), _time(signal.get('created_at'))
    if entered is None or created is None or created > entered:
        return {'status': 'UNKNOWN_INVALID_SIGNAL_TIME'}
    details = signal.get('details')
    if isinstance(details, str):
        try:
            details = json.loads(details)
        except (ValueError, TypeError):
            details = None
    details = details if isinstance(details, dict) else {}
    reason = details.get('forecast_unavailable_reason')
    reason = reason if isinstance(reason, str) and reason.strip() else None
    declared_id = details.get('forecast_id')
    linked_id = entry.get('entry_forecast_id')
    # Only the original signal's exact scope and pre-entry rows are evidence.
    related = [r for r in forecasts if str(r.get('signal_id')) == str(signal_id)
               and (r.get('market'), r.get('symbol')) == scope
               and _time(r.get('created_at')) is not None
               and _time(r['created_at']) <= entered]
    all_scoped = [r for r in forecasts if str(r.get('signal_id')) == str(signal_id)
                  and (r.get('market'), r.get('symbol')) == scope]
    if len(all_scoped) > 1:
        return {'status': 'AMBIGUOUS_PERSISTED_FORECASTS'}
    if reason and (declared_id or linked_id or related):
        return {'status': 'CONFLICTING_GENERATION_EVIDENCE', 'unavailable_reason': reason}
    if reason:
        return {'status': 'RECORDED_FORECAST_UNAVAILABLE', 'unavailable_reason': reason}
    if related:
        if len(related) != 1:
            return {'status': 'AMBIGUOUS_PERSISTED_FORECASTS'}
        row = related[0]
        generated, quote = _time(row.get('created_at')), _time(row.get('source_quote_timestamp'))
        if quote is None or quote > generated or not row.get('forecast_id'):
            return {'status': 'UNKNOWN_INVALID_FORECAST_EVIDENCE'}
        if declared_id and declared_id != row['forecast_id']:
            return {'status': 'CONFLICTING_GENERATION_EVIDENCE'}
        if not linked_id:
            return {'status': 'PERSISTED_FORECAST_MISSING_ENTRY_LINK'}
        if linked_id != row['forecast_id'] or (declared_id and declared_id != linked_id):
            return {'status': 'CONFLICTING_GENERATION_EVIDENCE'}
        return {'status': 'PERSISTED_EXACT_ENTRY_LINK'}
    if declared_id or linked_id:
        # Absence cannot distinguish a failed INSERT from historical retention.
        return {'status': 'REFERENCED_FORECAST_NOT_FOUND'}
    return {'status': 'UNKNOWN_NO_GENERATION_RECEIPT'}


def summarize_trade_evidence(conn, *, market, limit=SAMPLE_LIMIT):
    if market not in {'cash','crypto'}:
        raise ValueError('invalid market')
    if type(limit) is not int or not 1 <= limit <= SAMPLE_LIMIT:
        raise ValueError('invalid sample limit')
    conn.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY')
    conn.execute("SET LOCAL statement_timeout='5000ms'")
    if conn.execute("SELECT current_setting('transaction_read_only') AS ro").fetchone()['ro'] != 'on':
        raise RuntimeError('read-only audit required')
    canonical = list(conn.execute('SELECT side,COUNT(*) AS total FROM trades WHERE market=%s GROUP BY side ORDER BY side',(market,)).fetchall())
    modes = list(conn.execute('''SELECT broker_mode,account_environment,side,COUNT(*) AS total
        FROM trade_ledger WHERE market=%s GROUP BY broker_mode,account_environment,side
        ORDER BY broker_mode,account_environment,side''',(market,)).fetchall())
    fills = dict(conn.execute('''SELECT COUNT(*) AS total,
        COUNT(*) FILTER(WHERE f.canonical_trade_id IS NULL) AS unlinked_legacy,
        COUNT(*) FILTER(WHERE f.canonical_trade_id IS NOT NULL AND t.id IS NULL) AS dangling_trade_id,
        COUNT(*) FILTER(WHERE f.fee_amount IS NULL OR f.fee_amount<0
            OR f.fee_amount>='Infinity'::double precision) AS invalid_fee,
        COUNT(*) FILTER(WHERE NOT EXISTS(SELECT 1 FROM paper_fill_lot_links a WHERE a.fill_id=f.fill_id)) AS missing_lot_links
        FROM paper_fills f LEFT JOIN trades t ON t.id=f.canonical_trade_id WHERE f.market=%s''',(market,)).fetchone())
    ledger = [dict(r) for r in conn.execute('''SELECT * FROM trade_ledger ledger
        WHERE market=%s AND broker_mode='PAPER' AND account_environment='PAPER'
        AND EXISTS(SELECT 1 FROM paper_fill_lot_links a WHERE a.ledger_trade_id=ledger.trade_id)
        ORDER BY ledger.id DESC LIMIT %s''',(market,limit)).fetchall()]
    as_of = datetime.now(timezone.utc)
    allocations = load_fill_lot_allocations(conn,market=market,trade_ids=[r['trade_id'] for r in ledger],as_of=as_of)
    verified = attach_exact_fill_lot_evidence(ledger,allocations,as_of=as_of)
    by_trade = defaultdict(list)
    for a in allocations:by_trade[a['trade_id']].append(a)
    entries = {}
    for row in verified:
        if row.get('_fill_accounting_provenance') != PROVENANCE_VERSION:continue
        candidates = by_trade[row['trade_id']]
        if len(candidates) != 1:continue
        a=candidates[0]
        opening=a['ledger'] if row['side']=='BUY' else a['entry_ledger']
        entries[opening['trade_id']] = (opening,a['lot'])
    signal_ids = sorted({str(e.get('entry_signal_id')) for e,_ in entries.values() if e.get('entry_signal_id')})
    forecast_ids = sorted({e.get('entry_forecast_id') for e,_ in entries.values() if e.get('entry_forecast_id')})
    signals = list(conn.execute('SELECT id,market,symbol,created_at,details FROM signals WHERE id::text=ANY(%s)',(signal_ids,)).fetchall()) if signal_ids else []
    forecasts = list(conn.execute('''SELECT forecast_id,signal_id,market,symbol,created_at,source_quote_timestamp
        FROM forecasts WHERE forecast_id=ANY(%s)''',(forecast_ids,)).fetchall()) if forecast_ids else []
    # Bound related rows per sampled signal; two rows suffice to detect ambiguity.
    numeric_ids = [int(i) for i in signal_ids if i.isdecimal() and int(i) <= 9223372036854775807]
    related_forecasts = list(conn.execute("""SELECT f.* FROM unnest(%s::bigint[]) AS ids(signal_id)
        CROSS JOIN LATERAL (SELECT forecast_id,signal_id,market,symbol,created_at,source_quote_timestamp
            FROM forecasts f0 WHERE signal_id=ids.signal_id
              AND EXISTS(SELECT 1 FROM signals s0 WHERE s0.id=ids.signal_id
                AND s0.market=f0.market AND s0.symbol=f0.symbol)
            ORDER BY created_at LIMIT 2) f""",
        (numeric_ids,)).fetchall()) if numeric_ids else []
    reasons=Counter(); generation_counts=Counter(); samples=[]; complete=0
    for trade_id,(entry,lot) in sorted(entries.items()):
        failures=entry_reference_reasons(entry,lot,signals,forecasts)
        reasons.update(failures);complete+=not failures
        generation = forecast_generation_evidence(entry, signals, related_forecasts)
        generation_counts[generation['status']] += 1
        samples.append({'entry_trade_id':trade_id,'entry_signal_id':entry.get('entry_signal_id'),
            'entry_forecast_id':entry.get('entry_forecast_id'),'symbol':entry['symbol'],'reasons':failures,
            'forecast_generation':generation})
    return {'market':market,'as_of':as_of.isoformat(),'canonical_trade_counts':[dict(r) for r in canonical],
        'ledger_mode_counts':[dict(r) for r in modes],'paper_fills':fills,
        'sample_scope':'latest_explicitly_linked_ledger_rows_only','sample_limit':limit,
        'sample_rows':len(ledger),'sample_accounting_verified':sum(r.get('_fill_accounting_provenance')==PROVENANCE_VERSION for r in verified),
        'sample_entry_lots':len(entries),'sample_complete_entry_references':complete,
        'sample_forecast_generation_counts':dict(generation_counts),
        'sample_entry_exclusions':dict(reasons),'sample_entry_references':samples,
        'historical_repair':'NONE','execution_impact':'NONE','profitability':'NOT_EVALUATED',
        'scope_warning':'Sample counts are not all-history qualifications, independent episodes, or decision-time Brain-consumption receipts.'}


def emit_trade_evidence_audit(market, *, connect_factory=None):
    if (os.getenv('EXECUTION_MODE','paper').strip().lower() != 'paper'
            or any(os.getenv(k,'false').strip().lower() != 'false' for k in ('ENABLE_BROKER_SUBMISSION','LIVE_TRADING_ARMED'))):
        return {'status':'DISABLED','execution_impact':'NONE'}
    if connect_factory is None:
        from database import connect
        connect_factory=connect
    try:
        with connect_factory() as conn:report=summarize_trade_evidence(conn,market=market)
        log.info('PAPER TRADE EVIDENCE AUDIT | %s',json.dumps(report,default=str,allow_nan=False,sort_keys=True))
        return report
    except Exception as exc:
        log.warning('PAPER TRADE EVIDENCE AUDIT | market=%s | status=UNAVAILABLE | error=%s | execution_impact=NONE',market,type(exc).__name__)
        return {'status':'UNAVAILABLE','reason':type(exc).__name__,'execution_impact':'NONE'}
