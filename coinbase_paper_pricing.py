"""Public Coinbase depth for simulated paper fills; never broker/live authority."""
from copy import deepcopy
from datetime import datetime, timezone
from decimal import Decimal, localcontext
import hashlib
import json
import logging
import os
import threading

from asset_routing import infer_asset_class
from kraken_research_data import PublicResearchData, ResearchUnavailable, coinbase_history
from kraken_reference_book import exact_decimal, timestamp, fresh, compare_reference

SOURCE = 'Coinbase Exchange paper pricing'
MODEL = 'coinbase-l2-paper-v1'
FEE = .006  # Published maximum standard taker tier; explicitly modeled, not account verified.
log = logging.getLogger('coinbase-paper-pricing')


def enabled():
    return (os.getenv('COINBASE_PAPER_PRICING_ENABLED','true').lower() == 'true'
        and os.getenv('EXECUTION_MODE','paper').lower() == 'paper'
        and os.getenv('ENABLE_BROKER_SUBMISSION','false').lower() == 'false'
        and os.getenv('LIVE_TRADING_ARMED','false').lower() == 'false'
        and os.getenv('KRAKEN_REFERENCE_WS_ENABLED','false').lower() == 'true')


def now_utc():return datetime.now(timezone.utc)


def validate_product(symbol, product):
    if (not isinstance(product,dict) or product.get('id')!=symbol
            or product.get('base_currency')!=symbol[:-4] or product.get('quote_currency')!='USD'
            or product.get('status')!='online'
            or any(product.get(k) is not False for k in ('trading_disabled','cancel_only','limit_only','post_only','auction_mode'))):
        raise ResearchUnavailable('PRODUCT_UNAVAILABLE_FOR_MARKET_PRICING')
    minimum=exact_decimal(product.get('min_market_funds'))
    if minimum<=0 or any(exact_decimal(product[k])<=0 for k in ('base_increment','quote_increment')):
        raise ResearchUnavailable('PRODUCT_RULES_UNKNOWN')
    return {'product_id':symbol,'product_status':'online','min_order_notional':str(minimum),
        'base_increment':str(exact_decimal(product['base_increment'])),
        'quote_increment':str(exact_decimal(product['quote_increment']))}


def validate_book(symbol, data, received, reference, now):
    if (not symbol.endswith('-USD') or not symbol[:-4].isalnum()
            or not isinstance(data,dict) or data.get('auction_mode') is not False
            or type(data.get('sequence')) is not int or data['sequence'] < 0):
        raise ResearchUnavailable('INVALID_PRODUCT_BOOK')
    event=timestamp(data.get('time'))
    if not fresh(event,received,now) or (now-received).total_seconds()>5 or (now-event).total_seconds()>5:
        raise ResearchUnavailable('STALE_PAPER_BOOK')
    sides={}
    for side in ('bids','asks'):
        levels=data.get(side)
        if not isinstance(levels,list) or not 1 <= len(levels) <= 100000:
            raise ResearchUnavailable('INVALID_BOOK_DEPTH')
        clean=[]
        for row in levels[:50]:
            if not isinstance(row,list) or len(row)!=3:
                raise ResearchUnavailable('INVALID_BOOK_LEVEL')
            price,qty=exact_decimal(row[0]),exact_decimal(row[1])
            if price<=0 or qty<=0 or type(row[2]) is not int or row[2]<=0:
                raise ResearchUnavailable('INVALID_BOOK_LEVEL')
            if clean and ((side=='bids' and price>=exact_decimal(clean[-1][0]))
                          or (side=='asks' and price<=exact_decimal(clean[-1][0]))):
                raise ResearchUnavailable('UNSORTED_OR_DUPLICATE_BOOK')
            clean.append([str(price),str(qty),row[2]])
        sides[side]=clean
    bid,ask=exact_decimal(sides['bids'][0][0]),exact_decimal(sides['asks'][0][0])
    if bid>ask:raise ResearchUnavailable('CROSSED_PAPER_BOOK')
    mid=(bid+ask)/2
    spread=(ask-bid)/mid
    if spread>Decimal('.015'):raise ResearchUnavailable('PAPER_SPREAD_LIMIT')
    quote={'symbol':symbol,'requested_symbol':symbol,'provider_symbol':symbol,'provider_native_symbol':symbol,
        'provider':SOURCE,'price':float(mid),'mid_price':str(mid),'bid':float(bid),'ask':float(ask),'spread_pct':float(spread),
        'quote_timestamp':event.isoformat(),'timestamp':event.isoformat(),'received_at':received.isoformat(),
        'fetched_at':received.isoformat(),'timestamp_basis':'coinbase_payload_time','interval':'1m','source_interval':'1m','stale':False,
        'quote_verified':True,'verified':False,'provider_quote_verified':False,'paper_reference_verified':True,
        'execution_quote_eligible':True,'execution_authority':'PAPER_SIMULATION_ONLY','broker_submission':'NONE',
        'reference_only':False,'source_capability':'public_l2_paper_pricing','verification_basis':'paper:coinbase_l2_kraken_aligned',
        'paper_fee_pct':FEE,'estimated_fees_pct':FEE,'fee_model':'published_max_standard_taker_60bps',
        'fee_account_verified':False,'paper_price_model':MODEL,'sequence':data['sequence'],'book':sides}
    comparison=compare_reference(reference,quote,now)
    if comparison.get('status')!='ALIGNED_VENUE_COMPARISON':
        raise ResearchUnavailable('INDEPENDENT_BOOK_'+str(comparison.get('reason') or comparison.get('status')))
    if abs(Decimal(str(comparison['mid_difference_bps'])))>Decimal('75'):
        raise ResearchUnavailable('VENUE_DIVERGENCE_LIMIT')
    quote['independent_comparison']=comparison
    identity=hashlib.sha256(json.dumps(quote,sort_keys=True,allow_nan=False).encode()).hexdigest()
    quote.update(quote_id='coinbase-paper:'+identity,source_identity='coinbase-paper:'+identity,
                 cache_identity='coinbase-paper:'+symbol+':'+str(data['sequence']))
    return quote


def depth_price(quote, side, order_value):
    with localcontext() as context:
        context.prec=80
        return _depth_price(quote,side,order_value)


def _depth_price(quote, side, order_value):
    value=exact_decimal(str(order_value))
    if value<=0:raise ResearchUnavailable('ORDER_NOTIONAL_UNKNOWN')
    if value<exact_decimal(quote.get('min_order_notional','1')):
        raise ResearchUnavailable('BELOW_PRODUCT_MINIMUM')
    if side not in ('BUY','SELL'):raise ResearchUnavailable('INVALID_SIDE')
    levels=quote['book']['asks' if side=='BUY' else 'bids']
    quantity=value/exact_decimal(quote['mid_price']) if side=='SELL' else None
    remaining=value if side=='BUY' else quantity
    cost=units=Decimal(0)
    for price,size,_ in levels:
        p,q=exact_decimal(price),exact_decimal(size)
        take=min(q,remaining/p) if side=='BUY' else min(q,remaining)
        cost+=take*p;units+=take
        remaining-=take*p if side=='BUY' else take
        if remaining<=Decimal('1e-24'):break
    if remaining>max(Decimal('1e-24'),value*Decimal('1e-24')):
        raise ResearchUnavailable('INSUFFICIENT_DISPLAYED_DEPTH')
    return cost/units


class PaperPricing:
    def __init__(self,data=None,reference=None,clock=now_utc,publish=True):
        from kraken_reference_runtime import read_kraken_reference_state
        self.data=data or PublicResearchData()
        self.reference=reference or read_kraken_reference_state
        self.clock=clock;self.publish=publish;self.lock=threading.RLock();self.receipts={};self.fill_receipts={};self.last_health={}

    def quote(self,symbol):
        symbol=str(symbol).upper().strip()
        if not enabled():return None
        with self.lock:
            try:
                reference=self.reference().get('quotes',{}).get(symbol)
                if not reference:raise ResearchUnavailable('NO_FRESH_INDEPENDENT_REFERENCE')
                with self.data.lock:
                    product=validate_product(symbol,self.data.request('Coinbase Public Research','products/'+symbol))
                    product['product_metadata_received_at']=self.data.last_receipt.isoformat()
                    data=self.data.request('Coinbase Public Research','products/'+symbol+'/book',{'level':2})
                    received=self.data.last_receipt
                # HTTP work can outlive the alignment window. Compare the book
                # with the closest actual pre/post-request reference observation,
                # never a manufactured or refreshed exchange timestamp.
                latest=self.reference().get('quotes',{}).get(symbol)
                event=timestamp(data.get('time'));current=self.clock();candidates=[]
                for candidate in (reference,latest):
                    try:
                        if (candidate and candidate.get('symbol')==symbol and candidate.get('checksum_status')=='VALID'
                                and fresh(timestamp(candidate['quote_timestamp']),timestamp(candidate['received_at']),current)):
                            candidates.append(candidate)
                    except (ValueError,KeyError,TypeError):
                        continue
                if not candidates:raise ResearchUnavailable('NO_FRESH_INDEPENDENT_REFERENCE')
                reference=min(candidates,key=lambda c:abs((timestamp(c['quote_timestamp'])-event).total_seconds()))
                q=validate_book(symbol,data,received,reference,self.clock())
                q.update(product,fee_source='https://help.coinbase.com/en/exchange/trading-and-funding/exchange-fees')
                identity=hashlib.sha256(json.dumps(q,sort_keys=True,allow_nan=False).encode()).hexdigest()
                q.update(quote_id='coinbase-paper:'+identity,source_identity='coinbase-paper:'+identity)
                self.receipts={k:v for k,v in self.receipts.items() if (self.clock()-timestamp(v['received_at'])).total_seconds()<=5}
                self.receipts[q['quote_id']]=deepcopy(q)
                self.last_health[symbol]={'status':'VALID_PAPER_PRICE','quote_timestamp':q['quote_timestamp'],
                    'quote_id':q['quote_id'],'execution_authority':'PAPER_SIMULATION_ONLY'}
                return deepcopy(q)
            except Exception as exc:
                if len(self.last_health)>=8 and symbol not in self.last_health:
                    self.last_health.pop(next(iter(self.last_health)))
                self.last_health[symbol]={'status':'UNAVAILABLE','reason':str(exc) if isinstance(exc,ResearchUnavailable) else type(exc).__name__}
                return None
            finally:
                if self.publish:
                    try:
                        from database import connect
                        with connect() as conn:
                            conn.execute('''INSERT INTO coinbase_paper_price_state VALUES(1,NOW(),%s::jsonb)
                                ON CONFLICT(id) DO UPDATE SET updated_at=EXCLUDED.updated_at,payload=EXCLUDED.payload''',
                                (json.dumps({'provider':SOURCE,'symbols':self.last_health,'broker_submission':'NONE'}),))
                    except Exception:pass
                log.info('COINBASE PAPER PRICING | %s',json.dumps({'symbols':self.last_health,'broker_submission':'NONE'}))

    def authentic(self,quote,symbol):
        if not enabled() or not isinstance(quote,dict):return False
        with self.lock:
            original=self.receipts.get(quote.get('quote_id'))
            if not original or original['symbol']!=symbol:return False
            if any(quote.get(k)!=v for k,v in original.items() if k!='price'):return False
            if quote.get('price')!=original['price']:
                key=(quote.get('quote_id'),quote.get('paper_depth_side'),str(quote.get('paper_depth_order_value')))
                fill=self.fill_receipts.get(key)
                if (not fill or quote.get('price')!=fill['price'] or quote.get('paper_fill_price')!=fill['price']
                        or quote.get('paper_depth_vwap')!=fill['vwap']
                        or quote.get('paper_reference_price')!=original['price']):return False
            current=self.clock()
            return (fresh(timestamp(original['quote_timestamp']),timestamp(original['received_at']),current)
                and (current-timestamp(original['quote_timestamp'])).total_seconds()<=5
                and (current-timestamp(original['received_at'])).total_seconds()<=5)


def model_approved(symbol):
    from forecasting import active_crypto_model_identity
    from forecast_quality import model_execution_approved
    from capital_model_governance import model_governance_assessment
    from model_registry import model_can_approve_execution
    model,version=active_crypto_model_identity()
    try:
        if not model_governance_assessment(model,version).eligible_for_approval:return False,'MODEL_GOVERNANCE_VETO'
        if not model_can_approve_execution(model,version):return False,'MODEL_NOT_APPROVED'
        return model_execution_approved(symbol,'crypto','5m',model,version)
    except Exception:return False,'MODEL_GOVERNANCE_UNAVAILABLE'


def exact_forecast_link(symbol, signal_id, forecast_id):
    from database import rows
    from forecasting import active_crypto_model_identity
    model,version=active_crypto_model_identity()
    try:
        records=rows("""SELECT f.created_at,f.source_quote_timestamp FROM forecasts f
            JOIN signals s ON s.id=f.signal_id AND s.market=f.market AND s.symbol=f.symbol
            WHERE f.forecast_id=%s AND f.signal_id::text=%s AND f.market='crypto' AND f.symbol=%s
              AND f.model=%s AND f.model_version=%s
              AND NULLIF(s.created_at,'')::timestamptz<=NOW()
              AND NULLIF(f.created_at,'')::timestamptz<=NOW() LIMIT 2""",
            (forecast_id,str(signal_id),symbol,model,version))
        return len(records)==1 and timestamp(records[0]['source_quote_timestamp'])<=timestamp(records[0]['created_at'])
    except Exception:return False


def install_paper_pricing(worker,provider=None):
    if not enabled():return False
    import oracle_bot as oracle
    import paper_execution_reality as reality
    import market_data
    if getattr(worker,'_coinbase_paper_pricing',None) is not None:return False
    provider=provider or PaperPricing()
    original_guard=oracle._execution_quote_guard;original_verified=oracle._verified_quote_for
    original_history=worker.get_history;original_handoff=worker._execution_quote_payload_from_history
    original_attach=worker._attach_execution_metadata;original_many=worker.get_many_snapshots
    original_live=market_data.get_live_snapshot;original_simulate=reality.simulate_fill
    is_crypto=lambda symbol:infer_asset_class(str(symbol).upper())=='crypto'

    def handoff(symbol,history,price=None,*,scan_type=''):
        if not is_crypto(symbol):return original_handoff(symbol,history,price,scan_type=scan_type)
        q=(getattr(history,'attrs',{}).get('provider_route') or {}).get('paper_quote')
        if not provider.authentic(q,str(symbol).upper()):return None
        payload=worker._quote_payload_from_history(symbol,history,price,scan_type=scan_type)
        payload.update(q)
        payload.update(analysis_source_interval='5m',analysis_quote_timestamp=history.index[-1].isoformat(),
            source_quote_timestamp=q['quote_timestamp'])
        return payload

    def get_history(symbol,period='5d',interval='1d',*a,**k):
        if not is_crypto(symbol) or interval!='5m':return original_history(symbol,period,interval,*a,**k)
        if symbol not in provider.reference().get('subscribed_symbols',[]):
            return original_history(symbol,period,interval,*a,**k)
        try:
            history=coinbase_history(provider.data,symbol,provider.clock())
            q=provider.quote(symbol)
            if q:
                route=history.attrs['provider_route']
                route.update(analysis_bar_timestamp=history.index[-1].isoformat(),quote_timestamp=q['quote_timestamp'],
                    quote_provider=SOURCE,quote_verified=True,paper_reference_verified=True,provider_quote_verified=False,
                    paper_quote=q,source_identity=q['source_identity'])
            return history
        except Exception:return original_history(symbol,period,interval,*a,**k)

    def attach(signal,history,scan_type):
        route=original_attach(signal,history,scan_type)
        q=route.get('paper_quote')
        if q and provider.authentic(q,str(getattr(signal,'symbol','')).upper()):
            setattr(signal,'analysis_price',getattr(signal,'price',None));setattr(signal,'price',q['price'])
        return route

    def verified(symbol,quotes,market='cash'):
        if not is_crypto(symbol) and str(market).lower()!='crypto':return original_verified(symbol,quotes,market)
        q=(quotes or {}).get(str(symbol).upper())
        if not provider.authentic(q,str(symbol).upper()):return None
        return original_verified(symbol,quotes,market)

    def guard(market,symbol,price,signal,position=None,quote_metadata=None):
        if is_crypto(symbol) or str(market).lower()=='crypto':
            if not provider.authentic(quote_metadata,str(symbol).upper()):return False,'PAPER_PRICE_UNVERIFIED_OR_STALE'
            if position is None:
                value=lambda k:signal.get(k) if isinstance(signal,dict) else getattr(signal,k,None)
                if not value('signal_id') or not value('forecast_id'):return False,'ENTRY_FORECAST_LINK_REQUIRED'
                approved,reason=model_approved(symbol)
                if not approved:return False,reason
                if not exact_forecast_link(symbol,value('signal_id'),value('forecast_id')):
                    return False,'ENTRY_FORECAST_LINK_INVALID'
        return original_guard(market,symbol,price,signal,position,quote_metadata)

    def simulate(**kwargs):
        q=kwargs.get('quote') or {}
        if q.get('provider')!=SOURCE:return original_simulate(**kwargs)
        if not provider.authentic(q,q.get('symbol')):raise ResearchUnavailable('PAPER_PRICE_UNVERIFIED_OR_STALE')
        side=kwargs['side'];vwap=float(depth_price(q,side,kwargs.get('order_value')))
        depth_quote=deepcopy(q);depth_quote['ask' if side=='BUY' else 'bid']=vwap
        q['paper_original_price_quote']=deepcopy(provider.receipts[q['quote_id']])
        q.update(paper_depth_vwap=vwap,paper_depth_order_value=kwargs.get('order_value'),paper_depth_side=side)
        fill=original_simulate(**{**kwargs,'quote':depth_quote,'market_impact_pct':0.0})
        with provider.lock:
            if len(provider.fill_receipts)>=128:provider.fill_receipts.pop(next(iter(provider.fill_receipts)))
            provider.fill_receipts[(q['quote_id'],side,str(kwargs.get('order_value')))]=dict(price=fill.fill_price,vwap=vwap)
        return fill

    def snapshot(symbol):
        if not is_crypto(symbol):return original_live(symbol)
        q=provider.quote(symbol)
        if not q:return None
        value=market_data.MarketSnapshot(symbol=symbol,price=q['price'],change_pct=0,volume=0,timestamp=q['quote_timestamp'],
            provider=SOURCE,interval='1m',fetched_at=q['received_at'],requested_symbol=symbol,provider_symbol=symbol,
            bid=q['bid'],ask=q['ask'],quote_verified=True,stale=False,provider_quote_verified=False,
            paper_reference_verified=True,execution_quote_eligible=True,source_identity=q['source_identity'])
        value.to_quote_payload=lambda:deepcopy(q)
        return value

    def many(symbols,live=False):
        symbols=list(symbols);stocks=[s for s in symbols if not is_crypto(s)]
        result=original_many(stocks,live=live) if stocks else {}
        for symbol in symbols:
            if is_crypto(symbol):
                value=snapshot(symbol)
                if value is not None:result[symbol]=value
        return result

    worker.get_history=get_history;worker._execution_quote_payload_from_history=handoff
    worker._attach_execution_metadata=attach;worker.get_many_snapshots=many
    market_data.get_live_snapshot=snapshot
    oracle._verified_quote_for=verified;oracle._execution_quote_guard=guard
    reality.simulate_fill=simulate;oracle.simulate_fill=simulate
    worker._coinbase_paper_pricing=provider
    from crypto_execution_source import install_crypto_candidate_source_guard
    install_crypto_candidate_source_guard(worker,provider.authentic,'PAPER_PRICE_UNVERIFIED_OR_STALE')
    log.info('COINBASE PAPER PRICING | status=INSTALLED | authority=PAPER_SIMULATION_ONLY | live_trading=DISARMED')
    return True


def read_pricing_status(fetch):
    result={'provider':SOURCE,'status':'UNAVAILABLE','execution_authority':'PAPER_SIMULATION_ONLY',
        'broker_submission':'NONE','symbols':{}}
    try:
        relation=fetch("SELECT to_regclass('coinbase_paper_price_state') AS relation")
        if not relation or not relation[0].get('relation'):return result
        rows=fetch('SELECT updated_at,payload FROM coinbase_paper_price_state WHERE id=1')
        if not rows:return result
        state=deepcopy(rows[0]['payload']);current=now_utc()
        for value in state.get('symbols',{}).values():
            event=value.get('quote_timestamp')
            value['current']=bool(event and -2 <= (current-timestamp(event)).total_seconds()<=5)
        state.update(status='ON_DEMAND_PAPER_PRICING',execution_authority='PAPER_SIMULATION_ONLY',
            broker_submission='NONE',live_trading='DISARMED',published_at=str(rows[0]['updated_at']))
        return state
    except Exception:return result
