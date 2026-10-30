"""Kraken-primary analysis isolated from trade signals and broker execution."""
from dataclasses import asdict, replace
from datetime import datetime, timezone, timedelta
import hashlib
import json
import logging
import os
import threading
import zlib

from kraken_reference_book import SOURCE, exact_decimal, fresh, timestamp, compare_reference
from kraken_research_data import PublicResearchData, ResearchUnavailable, coinbase_quote, coinbase_history, validate_closed_history

log = logging.getLogger('kraken-primary-research')
DDL = '''CREATE TABLE IF NOT EXISTS kraken_research_state (
 id SMALLINT PRIMARY KEY CHECK(id=1), updated_at TIMESTAMPTZ NOT NULL, payload JSONB NOT NULL)'''
EVIDENCE_DDL = """CREATE TABLE IF NOT EXISTS kraken_research_evidence (
 id SMALLINT PRIMARY KEY CHECK(id BETWEEN 1 AND 5000),
 evidence_key TEXT UNIQUE NOT NULL, observed_at TIMESTAMPTZ NOT NULL,
 payload JSONB NOT NULL CHECK(octet_length(payload::text)<=32768), closed_history BYTEA NOT NULL CHECK(octet_length(closed_history)<=131072))"""
RESEARCH_LOCK = 73420261604


def enabled():
    return (os.getenv('KRAKEN_REFERENCE_WS_ENABLED','false').lower() == 'true'
        and os.getenv('KRAKEN_PRIMARY_RESEARCH_ENABLED','false').lower() == 'true'
        and os.getenv('EXECUTION_MODE','paper').lower() == 'paper'
        and os.getenv('ENABLE_BROKER_SUBMISSION','false').lower() == 'false'
        and os.getenv('LIVE_TRADING_ARMED','false').lower() == 'false')


def research_forecast(quote, history, now, forecast=None):
    """Validate research authority separately; never change live forecast gates."""
    from forecasting import forecast_price
    symbol = quote['symbol']
    kraken = quote.get('provider') == SOURCE and quote.get('checksum_status') == 'VALID'
    coinbase = quote.get('provider') == 'Coinbase Exchange public reference' and quote.get('checksum_status') == 'NOT_PROVIDED'
    if (not (kraken or coinbase)
            or any(quote.get(k) is True for k in ('verified','quote_verified','provider_quote_verified','paper_reference_verified'))
            or quote.get('reference_only') is not True or quote.get('execution_quote_eligible') is not False
            or not fresh(timestamp(quote['quote_timestamp']), timestamp(quote['received_at']), now)):
        raise ResearchUnavailable('INVALID_RESEARCH_QUOTE')
    bid, ask = exact_decimal(quote['bid']), exact_decimal(quote['ask'])
    if bid <= 0 or ask < bid or exact_decimal(quote['price']) != (bid+ask)/2:
        raise ResearchUnavailable('INVALID_RESEARCH_BOOK')
    route = history.attrs.get('provider_route', {})
    if route.get('provider_symbol') != symbol or route.get('requested_symbol') != symbol:
        raise ResearchUnavailable('HISTORY_SYMBOL_MISMATCH')
    expected_history = 'Kraken Public OHLC' if kraken else 'Coinbase Exchange'
    if history.attrs.get('provider') != expected_history:
        raise ResearchUnavailable('HISTORY_VENUE_MISMATCH')
    validate_closed_history(history, now)
    if history.index[-1].to_pydatetime()+timedelta(minutes=5) > timestamp(quote['quote_timestamp']):
        raise ResearchUnavailable('HISTORY_AFTER_QUOTE')
    diagnostics = {}
    prediction = (forecast or forecast_price)(history, None, market='crypto', source_interval='5m',
                       horizon_minutes=15, diagnostics=diagnostics, require_live_quote=False)
    from forecasting import active_crypto_model_identity
    model_name,model_version = active_crypto_model_identity()
    result = {'model_identity':{'model':model_name,'model_version':model_version},'symbol':symbol, 'purpose':'SHADOW_RESEARCH', 'primary_quote':dict(quote), 'data_role':'KRAKEN_PRIMARY' if kraken else 'COINBASE_RESEARCH_BACKUP',
        'history_provider':history.attrs.get('provider'), 'history_last_closed_bar':history.index[-1].isoformat(),
        'history_bars':len(history), 'history_fingerprint':hashlib.sha256(history.to_csv().encode()).hexdigest(),
        'model_governance':'UNCHANGED_NOT_APPROVED_FOR_EXECUTION', 'execution_authority':'NONE',
        'broker_submission':'NONE', 'profitability_status':'UNPROVEN', 'qualified_post_cost_outcomes':None}
    if prediction is None:
        result.update(status='ABSTAINED', reason=diagnostics.get('unavailable_reason','model_abstention'), forecast=None)
        return result
    # Preserve the unchanged model's return/probability, anchor research prices
    # to the actual Kraken mid. Do not relabel candles as current quotes.
    scale = float(quote['price']) / prediction.spot_price
    identity = hashlib.sha256((prediction.forecast_id+'|'+quote['quote_timestamp']+'|'+quote['provider']).encode()).hexdigest()[:24]
    prediction = replace(prediction, spot_price=float(quote['price']), target_price=prediction.target_price*scale,
        low_price=prediction.low_price*scale, high_price=prediction.high_price*scale,
        source_quote_timestamp=quote['quote_timestamp'], forecast_id=identity, validation_status='shadow')
    result.update(status='SHADOW_FORECAST', forecast=asdict(prediction))
    return result


def persist_evidence(conn, result, history):
    # The runner's session advisory lock serializes writers. Fixed slot IDs and
    # payload bounds stop storage growth without deleting permanent evidence.
    if result['status'] not in {'SHADOW_FORECAST','ABSTAINED'}:
        return None
    model_version=(result.get('forecast') or result.get('model_identity') or {})['model_version']
    key = '|'.join([result['symbol'],result['history_provider'],result['history_last_closed_bar'],model_version])
    if result['status']=='ABSTAINED':key+='|ABSTAINED|'+result['reason']
    from kraken_research_store import evidence_layout, evidence_sql, LEGACY_TABLE, OVERFLOW_TABLE, LEGACY_LIMIT
    layout = evidence_layout(conn)
    source = evidence_sql(layout, 'id,evidence_key')
    existing = conn.execute(f'SELECT id FROM ({source}) e WHERE evidence_key=%s',(key,)).fetchone()
    if existing:
        raise ResearchUnavailable('BAR_ALREADY_RECORDED')
    compressed = zlib.compress(history.to_csv().encode())
    if len(compressed) > 131072:
        raise ResearchUnavailable('EVIDENCE_TOO_LARGE')
    highest = conn.execute(f'SELECT COALESCE(MAX(id),0) AS highest_id FROM ({source}) e').fetchone()['highest_id']
    target = OVERFLOW_TABLE if highest >= LEGACY_LIMIT and OVERFLOW_TABLE in layout['tables'] else LEGACY_TABLE
    row = conn.execute(f"""INSERT INTO {target}
        SELECT COALESCE(MAX(id),0)+1,%s,%s,%s::jsonb,%s FROM ({source}) e
        HAVING COALESCE(MAX(id),0)<{layout['max_id']} ON CONFLICT(evidence_key) DO NOTHING RETURNING id""",
        (key,datetime.now(timezone.utc),json.dumps(result,allow_nan=False),compressed)).fetchone()
    if not row:
        raise ResearchUnavailable('EVIDENCE_CAPACITY_REACHED')
    return row['id']


def display_research(payload, now):
    """A retained research result is historical, never a current execution quote."""
    value = json.loads(json.dumps(payload))
    value.update(execution_authority='NONE', broker_submission='NONE')
    for record in value.get('symbols',{}).values():
        quote = record.get('primary_quote')
        record['current'] = bool(quote and fresh(timestamp(quote['quote_timestamp']), timestamp(quote['received_at']), now))
        record['delivery'] = 'RETAINED_RESEARCH_OBSERVATION'
        if quote:
            quote.update(fresh=record['current'], stale=not record['current'],
                age_seconds=(now-timestamp(quote['quote_timestamp'])).total_seconds(),
                verified=False, quote_verified=False, provider_quote_verified=False,
                paper_reference_verified=False, execution_quote_eligible=False,
                execution_authority='NONE', reference_only=True)
        # The immutable stored comparison remains evidence; reader diagnostics
        # cannot advertise its historical divergence as a current venue basis.
        for key in ('independent_comparison','broker_comparison'):
            comparison = record.get(key)
            if isinstance(comparison,dict) and comparison.get('status') == 'ALIGNED_VENUE_COMPARISON':
                from kraken_reference_runtime import comparison_current
                check = {**comparison, 'checked_at':comparison.get('reference_timestamp')}
                paired = comparison.get('reference_quote',{})
                if not comparison_current(record.get('symbol'),check,paired,now):
                    record[key] = {'status':'RETAINED_COMPARISON_EXPIRED','execution_impact':'NONE'}
    return value


def read_research(fetch_rows, now=None):
    # The default-off writer has not necessarily created its optional table.
    relation = fetch_rows("SELECT to_regclass('kraken_research_state') AS relation")
    if not relation or relation[0].get('relation') is None:
        return {'status':'UNAVAILABLE', 'execution_authority':'NONE', 'symbols':{}}
    rows = fetch_rows('SELECT payload FROM kraken_research_state WHERE id=1')
    if not rows:
        return {'status':'UNAVAILABLE', 'execution_authority':'NONE', 'symbols':{}}
    return display_research(rows[0]['payload'], now or datetime.now(timezone.utc))


class PrimaryResearch:
    def __init__(self, connect_factory=None, data=None, paper_pricing=None):
        from database import connect
        self.connect = connect_factory or connect
        self.data = data or PublicResearchData()
        self.paper_pricing = paper_pricing
        self.stop = threading.Event()
        self.thread = None

    def start(self):
        if self.thread and self.thread.is_alive():
            return False
        self.thread = threading.Thread(target=self.run, name='kraken-primary-research', daemon=True)
        self.thread.start()
        return True

    def cycle(self, conn):
        from kraken_reference_runtime import read_kraken_reference_state
        from kraken_brain_evidence import read_evidence_capacity
        capacity = read_evidence_capacity(conn)
        # Separate, explicit opt-in. Reuse the installed pricer and shared feed;
        # no orders, new connections, canonical fills or strategy approvals.
        import prospective_paper_research as prospective
        pilot = None
        prospective_status = {'status':'DISABLED','execution_authority':'NONE'}
        if prospective.enabled() and self.paper_pricing is not None:
            try:
                with conn.transaction():
                    pilot = prospective.pilot_config(conn,capacity,datetime.now(timezone.utc))
                    if pilot:
                        prospective.evaluate_pending(conn,self.paper_pricing,pilot,datetime.now(timezone.utc))
                        prospective_status = prospective.summarize(conn,pilot)
                    else:
                        prospective_status = {'status':'NO_UNUSED_SOURCE_CAPACITY','execution_authority':'NONE'}
            except Exception as exc:
                pilot = None
                prospective_status = {'status':'UNAVAILABLE','reason_class':type(exc).__name__,
                                      'execution_authority':'NONE'}
        capacity_status = capacity.get('status')
        blocked = ('EVIDENCE_CAPACITY_REACHED' if capacity_status == 'CAPACITY_REACHED'
                   else None if capacity_status in {'CAPACITY_NORMAL','CAPACITY_WARNING','CAPACITY_CRITICAL'}
                   else 'EVIDENCE_CAPACITY_UNAVAILABLE')
        state = read_kraken_reference_state()
        results = {}
        for symbol in list(state.get('subscribed_symbols') or state.get('quotes',{}))[:4]:
            if blocked:
                results[symbol] = {'status':'UNAVAILABLE','reason':blocked,'execution_authority':'NONE'}
                continue
            try:
                if symbol in state.get('quotes',{}):
                    history = self.data.history(symbol, datetime.now(timezone.utc))
                    # Network/model work cannot renew the source timestamp.
                    quote = read_kraken_reference_state().get('quotes',{}).get(symbol)
                    if not quote:
                        raise ResearchUnavailable('PRIMARY_BECAME_UNAVAILABLE')
                else:
                    history = coinbase_history(self.data, symbol, datetime.now(timezone.utc))
                    quote = coinbase_quote(self.data, symbol)
                result = research_forecast(quote, history, datetime.now(timezone.utc))
                result['broker_comparison'] = state.get('comparisons',{}).get(symbol)
                try:
                    if not read_kraken_reference_state().get('quotes',{}).get(symbol):
                        raise ResearchUnavailable('REFERENCE_UNAVAILABLE')
                    independent = coinbase_quote(self.data, symbol)
                    latest = read_kraken_reference_state().get('quotes',{}).get(symbol)
                    result['independent_comparison'] = compare_reference(latest, independent, datetime.now(timezone.utc))
                except Exception as exc:
                    result['independent_comparison'] = {'status':'UNAVAILABLE',
                        'reason':str(exc) if isinstance(exc,ResearchUnavailable) else type(exc).__name__}

                if pilot and pilot['first_source_id'] <= capacity['highest_id']+1 <= pilot['last_source_id']:
                    try:
                        result['forward_trial'] = prospective.capture_trial(result,self.paper_pricing,datetime.now(timezone.utc))
                        if len(json.dumps(result,allow_nan=False).encode()) > 24000:
                            raise ResearchUnavailable('PROSPECTIVE_SOURCE_PAYLOAD_TOO_LARGE')
                    except Exception as exc:
                        result['forward_trial'] = {'version':prospective.VERSION,'status':'UNAVAILABLE',
                            'reason_class':type(exc).__name__,'execution_authority':'NONE','canonical_trade':False}
                result['evidence_id'] = persist_evidence(conn, result, history)
                results[symbol] = result
                evidence_id = result['evidence_id']
                if type(evidence_id) is int:
                    # The existing elected writer serializes slot allocation.
                    # Stop within this batch when the last durable slot is used.
                    remaining = max(0, capacity['max_id'] - evidence_id)
                    capacity = {**capacity, 'highest_id':evidence_id,
                                'remaining_id_slots':remaining}
                    if remaining == 0:
                        blocked = 'EVIDENCE_CAPACITY_REACHED'
                        capacity['status'] = 'CAPACITY_REACHED'
                    elif remaining <= 250:
                        capacity['status'] = 'CAPACITY_CRITICAL'
                    elif remaining <= 1000:
                        capacity['status'] = 'CAPACITY_WARNING'
            except Exception as exc:
                results[symbol] = {'status':'UNAVAILABLE', 'reason':str(exc) if isinstance(exc,ResearchUnavailable) else type(exc).__name__,
                                   'execution_authority':'NONE'}
        now = datetime.now(timezone.utc)
        payload = {'status':blocked or ('ACTIVE' if results else 'PRIMARY_UNAVAILABLE'),
                   'prospective_paper':prospective_status,
                   'evidence_capacity':capacity, 'primary_policy':'KRAKEN_THEN_COINBASE_RESEARCH_ONLY',
                   'symbols':results,'observed_at':now.isoformat(), 'execution_authority':'NONE',
                   'broker_submission':'NONE','profitability_status':'UNPROVEN'}
        conn.execute('''INSERT INTO kraken_research_state VALUES(1,%s,%s::jsonb)
                    ON CONFLICT(id) DO UPDATE SET updated_at=EXCLUDED.updated_at,payload=EXCLUDED.payload''',
                    (now,json.dumps(payload, allow_nan=False)))
        try:
            from kraken_brain_evidence import sync_research_sources, committed_receipt
            with conn.transaction():
                learning = sync_research_sources(conn)
            # The source INSERTs have committed on this autocommit writer connection.
            receipt = committed_receipt(conn)
            log.info('KRAKEN BRAIN RECEIPT | %s',json.dumps({'learning':learning,'committed':receipt},default=str))
        except Exception as exc:
            log.warning('KRAKEN BRAIN RECEIPT | status=UNAVAILABLE | reason=%s | execution_impact=NONE',type(exc).__name__)
        log.info('KRAKEN PRIMARY RESEARCH | %s', json.dumps({'status':payload['status'],
            'prospective_paper':prospective_status,
            'symbols':{s:{'status':r['status'],'reason':r.get('reason'),
                'data_role':r.get('data_role'),'quote_timestamp':r.get('primary_quote',{}).get('quote_timestamp'),
                'evidence_id':r.get('evidence_id'),
                'independent_comparison':r.get('independent_comparison',{}).get('status')} for s,r in results.items()},
            'execution_authority':'NONE','broker_submission':'NONE','live_trading':'DISARMED'}))

    def run(self):
        while not self.stop.is_set() and enabled():
            try:
                with self.connect() as conn:
                    conn.autocommit = True
                    if not conn.execute('SELECT pg_try_advisory_lock(%s) AS owned',(RESEARCH_LOCK,)).fetchone()['owned']:
                        self.stop.wait(30)
                        continue
                    conn.execute("SET statement_timeout='3s'")
                    conn.execute(DDL)
                    conn.execute(EVIDENCE_DDL)
                    from kraken_research_store import ensure_overflow_schema
                    ensure_overflow_schema(conn)
                    self.stop.wait(10)  # Allow the elected feed to obtain its initial snapshots.
                    while not self.stop.is_set() and enabled():
                        self.cycle(conn)
                        # The elected five-minute research loop reports configured
                        # budget occupancy without issuing provider HTTP requests.
                        try:
                            from provider_budget_diagnostics import emit_public_research_budget_report
                            emit_public_research_budget_report(conn)
                        except Exception as exc:
                            log.warning('PUBLIC RESEARCH BUDGET | status=UNAVAILABLE | reason=%s | execution_impact=NONE',
                                        type(exc).__name__)
                        self.stop.wait(300)
            except Exception as exc:
                log.warning('KRAKEN PRIMARY RESEARCH | status=STORAGE_OR_CYCLE_UNAVAILABLE | reason=%s | execution_authority=NONE',type(exc).__name__)
                self.stop.wait(30)
