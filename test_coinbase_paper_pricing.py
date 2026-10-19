from copy import deepcopy
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import Mock
import threading
import pytest
import coinbase_paper_pricing as pricing
from kraken_research_data import ResearchUnavailable
from test_kraken_reference_book import NOW, ready


def book(**changes):
    return {'time':NOW.isoformat(),'sequence':3,'auction_mode':False,
        'bids':[['100','2',1],['99','5',1]],'asks':[['100.00000001','1',1],['101','5',2]],**changes}


def reference():
    q=ready().quote('BTC/USD',NOW)
    q.update(bid='100',ask='100.00000001',price='100.000000005')
    return q


def valid():return pricing.validate_book('BTC-USD',book(),NOW,reference(),NOW)


def enable(monkeypatch):
    for k,v in {'COINBASE_PAPER_PRICING_ENABLED':'true','EXECUTION_MODE':'paper',
        'ENABLE_BROKER_SUBMISSION':'false','LIVE_TRADING_ARMED':'false','KRAKEN_REFERENCE_WS_ENABLED':'true'}.items():monkeypatch.setenv(k,v)


def provider(monkeypatch):
    enable(monkeypatch)
    product={'id':'BTC-USD','base_currency':'BTC','quote_currency':'USD','status':'online',
        'trading_disabled':False,'cancel_only':False,'limit_only':False,'post_only':False,'auction_mode':False,
        'base_increment':'0.00000001','quote_increment':'0.01','min_market_funds':'1'}
    data=SimpleNamespace(lock=threading.RLock(),request=Mock(side_effect=lambda p,path,*a:book() if path.endswith('/book') else product),last_receipt=NOW)
    return pricing.PaperPricing(data,lambda:{'quotes':{'BTC-USD':reference()},'subscribed_symbols':['BTC-USD']},lambda:NOW,publish=False)


def test_public_book_precision_provenance_and_independent_alignment():
    q=valid()
    assert q['book']['asks'][0][0]=='100.00000001'
    assert q['provider_quote_verified'] is False and q['verified'] is False
    assert q['paper_reference_verified'] is True and q['execution_authority']=='PAPER_SIMULATION_ONLY'
    assert q['paper_fee_pct']==.006 and q['fee_account_verified'] is False
    assert q['independent_comparison']['status']=='ALIGNED_VENUE_COMPARISON'
    from quote_provenance import provider_verified,reference_allowed_in_mode
    assert not provider_verified(q)
    assert reference_allowed_in_mode(q,'paper',True)
    assert not reference_allowed_in_mode(q,'live',True)


@pytest.mark.parametrize('change',[{'auction_mode':True},{'auction_mode':None},{'sequence':True},
    {'bids':[]},{'asks':[['100','0',1]]},{'asks':[['99','1',1]]},
    {'asks':[['100','1',1],['100','2',2]]},{'bids':[['100','1',1],['101','1',1]]},
    {'asks':[['NaN','1',1]]},{'time':None},{'time':(NOW-timedelta(seconds=6)).isoformat()},
    {'time':(NOW+timedelta(seconds=3)).isoformat()},{'asks':[['110','1',1]]}])
def test_invalid_stale_crossed_or_sparse_book_is_rejected(change):
    with pytest.raises(ValueError):pricing.validate_book('BTC-USD',book(**change),NOW,reference(),NOW)


def test_locked_book_is_rejected_even_with_aligned_independent_reference():
    with pytest.raises(ResearchUnavailable, match='LOCKED_PAPER_BOOK'):
        pricing.validate_book('BTC-USD',book(asks=[['100','2',1]]),NOW,reference(),NOW)


def test_locked_book_never_registers_a_paper_pricing_receipt(monkeypatch):
    p=provider(monkeypatch)
    original=p.data.request.side_effect
    p.data.request.side_effect=lambda source,path,*args: (
        book(asks=[['100','2',1]]) if path.endswith('/book') else original(source,path,*args))
    assert p.quote('BTC-USD') is None
    assert not p.receipts
    assert p.last_health['BTC-USD']=={'status':'UNAVAILABLE','reason':'LOCKED_PAPER_BOOK'}


@pytest.mark.parametrize('change',[{'symbol':'ETH-USD'},{'checksum_status':'FAILED'},
    {'quote_timestamp':(NOW-timedelta(seconds=3)).isoformat()},{'price':'102'}])
def test_independent_reference_is_required_and_venues_not_assumed_identical(change):
    with pytest.raises(ResearchUnavailable):pricing.validate_book('BTC-USD',book(),NOW,{**reference(),**change},NOW)


def test_depth_walks_buy_notional_sell_quantity_and_rejects_exhaustion():
    q=valid()
    assert float(pricing.depth_price(q,'BUY',150))>100.00000001
    assert float(pricing.depth_price(q,'SELL',250))<100
    with pytest.raises(ResearchUnavailable,match='DEPTH'):pricing.depth_price(q,'BUY',100000)
    for amount in (0,-1,'NaN',None):
        with pytest.raises(ValueError):pricing.depth_price(q,'BUY',amount)


@pytest.mark.parametrize('key,value',[('EXECUTION_MODE','live'),('ENABLE_BROKER_SUBMISSION','true'),
    ('LIVE_TRADING_ARMED','true'),('COINBASE_PAPER_PRICING_ENABLED','false')])
def test_no_network_or_authority_when_armed_or_disabled(monkeypatch,key,value):
    p=provider(monkeypatch);monkeypatch.setenv(key,value)
    assert p.quote('BTC-USD') is None
    p.data.request.assert_not_called()
    assert not p.authentic(valid(),'BTC-USD')


def test_registered_receipts_cannot_be_relabelled_forged_or_rejuvenated(monkeypatch):
    p=provider(monkeypatch);q=p.quote('BTC-USD')
    assert p.authentic(q,'BTC-USD')
    for key,value in [('provider','Kraken WebSocket v2 reference'),('provider_quote_verified',True),
        ('paper_reference_verified',False),('bid',99),('sequence',4),('paper_fee_pct',0)]:
        altered=deepcopy(q);altered[key]=value
        assert not p.authentic(altered,'BTC-USD')
    assert not p.authentic(q,'ETH-USD')
    p.clock=lambda:NOW+timedelta(seconds=6)
    assert not p.authentic(q,'BTC-USD')


def test_missing_reference_makes_no_requests(monkeypatch):
    p=provider(monkeypatch);p.reference=lambda:{'quotes':{}}
    assert p.quote('BTC-USD') is None
    p.data.request.assert_not_called()


def test_http_latency_uses_actual_post_request_reference_without_relaxing_alignment(monkeypatch):
    p=provider(monkeypatch)
    old={**reference(),'quote_timestamp':(NOW-timedelta(seconds=3)).isoformat(),
         'received_at':(NOW-timedelta(seconds=3)).isoformat()}
    p.reference=Mock(side_effect=[{'quotes':{'BTC-USD':old}},{'quotes':{'BTC-USD':reference()}}])
    q=p.quote('BTC-USD')
    assert q is not None
    assert q['independent_comparison']['reference_timestamp']==NOW.isoformat()
    assert old['quote_timestamp']==(NOW-timedelta(seconds=3)).isoformat()
    p.reference=lambda:{'quotes':{'BTC-USD':old}}
    assert p.quote('BTC-USD') is None
    assert p.last_health['BTC-USD']['reason']=='INDEPENDENT_BOOK_TIMESTAMPS_UNALIGNED'


def install(monkeypatch,real_contract=False):
    import oracle_bot,market_data,paper_execution_reality,market_worker
    p=provider(monkeypatch);q=p.quote('BTC-USD')
    orig_guard=oracle_bot._execution_quote_guard if real_contract else Mock(return_value=(True,''))
    orig_verified=oracle_bot._verified_quote_for if real_contract else Mock(side_effect=lambda s,quotes,m:quotes.get(s))
    for module,name,value in [(oracle_bot,'_execution_quote_guard',orig_guard),(oracle_bot,'_verified_quote_for',orig_verified),
        (market_data,'get_live_snapshot',Mock()),(paper_execution_reality,'simulate_fill',paper_execution_reality.simulate_fill),
        (oracle_bot,'simulate_fill',oracle_bot.simulate_fill)]:monkeypatch.setattr(module,name,value)
    worker=SimpleNamespace(get_history=Mock(),_execution_quote_payload_from_history=Mock(),
        _v39_signal_opportunity=Mock(return_value={'qualified_for_capital':True}),
        _attach_execution_metadata=market_worker._attach_execution_metadata,get_many_snapshots=Mock(),_quote_payload_from_history=Mock(return_value={}))
    assert pricing.install_paper_pricing(worker,p)
    return p,q,worker,orig_guard


def test_final_guard_preserves_model_veto_and_exact_forecast_link(monkeypatch):
    import oracle_bot
    p,q,worker,original=install(monkeypatch)
    monkeypatch.setattr(pricing,'model_approved',lambda s:(False,'MODEL_GOVERNANCE_VETO'))
    signal={'symbol':'BTC-USD','signal_id':1,'forecast_id':'f'}
    assert oracle_bot._execution_quote_guard('crypto','BTC-USD',q['price'],signal,quote_metadata=q)==(False,'MODEL_GOVERNANCE_VETO')
    monkeypatch.setattr(pricing,'model_approved',lambda s:(True,''))
    monkeypatch.setattr(pricing,'exact_forecast_link',lambda *a:False)
    assert oracle_bot._execution_quote_guard('crypto','BTC-USD',q['price'],signal,quote_metadata=q)[1]=='ENTRY_FORECAST_LINK_INVALID'
    assert not original.called
    monkeypatch.setattr(pricing,'exact_forecast_link',lambda *a:True)
    assert oracle_bot._execution_quote_guard('crypto','BTC-USD',q['price'],signal,quote_metadata=q)==(True,'')
    # Independent research quote copied directly cannot cross the final fill boundary.
    assert oracle_bot._execution_quote_guard('crypto','BTC-USD',100,signal,quote_metadata=reference())[0] is False


def test_registered_source_and_depth_fill_pass_existing_quote_contract_only_in_paper(monkeypatch):
    import oracle_bot,paper_execution_reality
    # Fixture time is controlled; freshness is checked by the adapter's clock.
    monkeypatch.setattr(oracle_bot,'quote_is_fresh',lambda *a,**k:True)
    monkeypatch.setattr(oracle_bot,'EXECUTION_MODE','paper')
    monkeypatch.setattr(oracle_bot,'PAPER_BROKER_MODE',True)
    p,q,worker,_=install(monkeypatch,real_contract=True)
    assert oracle_bot._verified_quote_for('BTC-USD',{'BTC-USD':q},'crypto')['provider']==pricing.SOURCE
    signal={'symbol':'BTC-USD','signal_id':1,'forecast_id':'original-forecast'}
    monkeypatch.setattr(pricing,'model_approved',lambda s:(True,''))
    monkeypatch.setattr(pricing,'exact_forecast_link',lambda *a:True)
    assert oracle_bot._execution_quote_guard('crypto','BTC-USD',q['price'],signal,quote_metadata=q)[0]
    adjusted,fill=paper_execution_reality._execution_quote(market='crypto',side='BUY',
        reference_price=q['price'],quote=q,order_value=150)
    assert adjusted['paper_original_price_quote']==p.receipts[q['quote_id']]
    assert oracle_bot._execution_quote_guard('crypto','BTC-USD',fill.fill_price,signal,quote_metadata=adjusted)[0]
    monkeypatch.setattr(oracle_bot,'EXECUTION_MODE','live')
    assert oracle_bot._verified_quote_for('BTC-USD',{'BTC-USD':q},'crypto') is None
    assert not oracle_bot._execution_quote_guard('crypto','BTC-USD',fill.fill_price,signal,quote_metadata=adjusted)[0]


def test_stock_path_unchanged_and_pricing_requires_known_notional(monkeypatch):
    import oracle_bot,paper_execution_reality
    p,q,worker,original=install(monkeypatch)
    assert oracle_bot._execution_quote_guard('cash','MSFT',100,{},quote_metadata={'provider':'Alpaca IEX'})==(True,'')
    with pytest.raises(ValueError):paper_execution_reality.simulate_fill(side='BUY',market='crypto',reference_price=q['price'],quote=q)
    fill=paper_execution_reality.simulate_fill(side='BUY',market='crypto',reference_price=q['price'],quote=q,order_value=150)
    assert fill.executable_reference>q['ask'] and fill.market_impact_pct==0
    assert q['paper_depth_order_value']==150


def test_capital_candidates_require_same_registered_fresh_pricing_source(monkeypatch):
    p,q,worker,_=install(monkeypatch)
    signal=SimpleNamespace(symbol='BTC-USD')
    def opportunity(quote):
        return worker._v39_signal_opportunity('crypto',signal,{'BTC-USD':quote},{},'fast')
    assert opportunity(q)['execution_source_eligible'] is True
    worker._v39_signal_opportunity('cash',SimpleNamespace(symbol='MSFT'),{}, {}, 'fast')
    assert opportunity(reference())['qualified_for_capital'] is False
    p.clock=lambda:NOW+timedelta(seconds=6)
    assert opportunity(q)['qualified_for_capital'] is False


def test_closed_bar_time_stays_distinct_from_actual_live_paper_quote(monkeypatch):
    import market_worker,forecasting
    from datetime import datetime
    from test_kraken_primary_research import history
    p,q,worker,_=install(monkeypatch)
    h=history();h.attrs['provider']='Coinbase Exchange'
    monkeypatch.setattr(pricing,'coinbase_history',lambda *a:h)
    actual=worker.get_history('BTC-USD','5d','5m')
    assert actual.attrs['provider_route']['analysis_bar_timestamp']==actual.index[-1].isoformat()
    assert actual.attrs['provider_route']['quote_timestamp']==q['quote_timestamp']
    assert actual.attrs['provider_route']['analysis_bar_timestamp']!=q['quote_timestamp']
    signal=SimpleNamespace(symbol='BTC-USD',price=100)
    worker._attach_execution_metadata(signal,actual,'fast')
    assert signal.price==q['price'] and signal.analysis_price==100
    worker._quote_payload_from_history=market_worker._quote_payload_from_history
    handoff=worker._execution_quote_payload_from_history('BTC-USD',actual,signal.price,scan_type='fast')
    assert handoff['provider']==pricing.SOURCE and handoff['provider_quote_verified'] is False
    assert handoff['analysis_quote_timestamp']==actual.index[-1].isoformat()
    class Clock(datetime):
        @classmethod
        def now(cls,tz=None):return NOW
    monkeypatch.setattr(forecasting,'datetime',Clock)
    monkeypatch.setattr(forecasting,'predict_crypto_direction',lambda *a:{'probability_up':.6,'predicted_log_return':.002,'vol_per_bar':.01})
    diagnostics={}
    forecast=forecasting.forecast_price(actual,None,market='crypto',source_interval='5m',horizon_minutes=15,
        require_live_quote=True,diagnostics=diagnostics)
    assert forecast is not None,diagnostics
    assert forecast.source_quote_timestamp==q['quote_timestamp']
    assert forecast.provider_symbol=='BTC-USD' and forecast.forecast_id
    p.clock=lambda:NOW+timedelta(seconds=6)
    assert worker._execution_quote_payload_from_history('BTC-USD',actual,signal.price) is None


def test_fill_price_cannot_be_forged_from_a_valid_quote_id(monkeypatch):
    import paper_execution_reality
    p,q,worker,_=install(monkeypatch)
    forged={**q,'price':q['price']/2,'paper_fill_price':q['price']/2}
    assert not p.authentic(forged,'BTC-USD')
    fill=paper_execution_reality.simulate_fill(side='BUY',market='crypto',reference_price=q['price'],quote=q,order_value=150)
    q.update(price=fill.fill_price,paper_fill_price=fill.fill_price,paper_reference_price=fill.reference_price)
    assert p.authentic(q,'BTC-USD')
    forged={**q,'price':fill.fill_price-1,'paper_fill_price':fill.fill_price-1}
    assert not p.authentic(forged,'BTC-USD')


def test_negative_governance_and_unpromoted_models_remain_hard_vetoes(monkeypatch):
    import capital_model_governance,model_registry,forecast_quality
    monkeypatch.setattr(capital_model_governance,'model_governance_assessment',lambda *a:SimpleNamespace(eligible_for_approval=False))
    monkeypatch.setattr(model_registry,'model_can_approve_execution',lambda *a:True)
    monkeypatch.setattr(forecast_quality,'model_execution_approved',lambda *a:(True,''))
    assert pricing.model_approved('BTC-USD')==(False,'MODEL_GOVERNANCE_VETO')
    monkeypatch.setattr(capital_model_governance,'model_governance_assessment',lambda *a:SimpleNamespace(eligible_for_approval=True))
    monkeypatch.setattr(model_registry,'model_can_approve_execution',lambda *a:False)
    assert pricing.model_approved('BTC-USD')==(False,'MODEL_NOT_APPROVED')


def test_read_only_pricing_status_never_displays_cached_books_as_live(monkeypatch):
    monkeypatch.setattr(pricing,'now_utc',lambda:NOW+timedelta(seconds=6))
    state={'symbols':{'BTC-USD':{'status':'VALID_PAPER_PRICE','quote_timestamp':NOW.isoformat()}}}
    def fetch(query):
        return [{'relation':'coinbase_paper_price_state'}] if 'to_regclass' in query else [{'updated_at':NOW,'payload':state}]
    result=pricing.read_pricing_status(fetch)
    assert result['symbols']['BTC-USD']['current'] is False
    assert 'current' not in state['symbols']['BTC-USD']


@pytest.mark.parametrize('change',[{'status':'offline'},{'trading_disabled':True},{'cancel_only':True},
    {'post_only':True},{'limit_only':True},{'auction_mode':True},{'id':'ETH-USD'},{'min_market_funds':None}])
def test_product_coverage_and_market_status_are_not_invented(monkeypatch,change):
    p=provider(monkeypatch)
    product=p.data.request('Coinbase Public Research','products/BTC-USD')
    with pytest.raises(ValueError):pricing.validate_product('BTC-USD',{**product,**change})
