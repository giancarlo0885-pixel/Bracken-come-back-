from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import Mock
import json
import pandas as pd
import pytest

import kraken_primary_research as research
import kraken_research_data as data
from forecasting import Forecast, forecast_price
from test_kraken_reference_book import NOW, ready


def history():
    frame = pd.DataFrame({'Open':[100.]*60,'High':[101.]*60,'Low':[99.]*60,'Close':[100.]*60,'Volume':[2.]*60},
        index=pd.date_range(end=NOW-timedelta(minutes=5),periods=60,freq='5min'))
    frame.attrs.update(provider='Kraken Public OHLC',provider_route={
        'provider':'Kraken Public OHLC','requested_symbol':'BTC-USD','provider_symbol':'BTC-USD',
        'interval':'5m','quote_verified':False})
    return frame


def quote(): return ready().quote('BTC/USD',NOW)


def prediction(*args,**kwargs):
    assert kwargs['require_live_quote'] is False and kwargs['horizon_minutes'] == 15
    return Forecast(symbol='BTC-USD',market='crypto',spot_price=100,target_price=102,low_price=95,high_price=105,
                    probability_up=.6,expected_move_pct=2,model_version='test',forecast_id='test')


def test_research_anchor_preserves_model_returns_and_execution_authority():
    q=quote(); h=history(); out=research.research_forecast(q,h,NOW,forecast=prediction)
    assert out['forecast']['spot_price'] == float(q['price'])
    assert out['forecast']['expected_move_pct'] == 2
    assert out['forecast']['probability_up'] == .6
    assert out['history_provider'] == 'Kraken Public OHLC'
    assert out['execution_authority'] == 'NONE' and out['qualified_post_cost_outcomes'] is None
    assert q['quote_verified'] is False and h.attrs['provider_route']['quote_verified'] is False
    assert out['forecast']['validation_status'] == 'shadow'


@pytest.mark.parametrize('change',[
    {'checksum_status':'FAILED'},{'reference_only':False},{'execution_quote_eligible':True},
    {'provider':'Robinhood Crypto'},{'bid':'200','ask':'100'},
    {'quote_timestamp':(NOW-timedelta(seconds=11)).isoformat()},
    {'received_at':(NOW+timedelta(seconds=1)).isoformat()}])
def test_research_rejects_bad_quotes_before_model(change):
    q={**quote(),**change}; model=Mock()
    with pytest.raises(ValueError): research.research_forecast(q,history(),NOW,forecast=model)
    model.assert_not_called()


def test_live_forecast_gate_is_unchanged():
    d={}; assert forecast_price(history(),None,market='crypto',horizon_minutes=15,require_live_quote=True,diagnostics=d) is None
    assert d['unavailable_reason'] == 'unverified_live_quote'


def test_model_abstention_is_preserved():
    def abstain(*args,**kwargs): kwargs['diagnostics']['unavailable_reason']='causal_model_abstention'
    result=research.research_forecast(quote(),history(),NOW,forecast=abstain)
    assert result['status']=='ABSTAINED' and result['forecast'] is None
    assert result['reason']=='causal_model_abstention'


@pytest.mark.parametrize('kind',['symbol','venue','future'])
def test_history_identity_and_causality(kind):
    h=history()
    if kind=='symbol': h.attrs['provider_route']['provider_symbol']='ETH-USD'
    if kind=='venue': h.attrs['provider']='Yahoo Finance'
    if kind=='future': h.index=h.index+timedelta(minutes=10)
    with pytest.raises(data.ResearchUnavailable): research.research_forecast(quote(),h,NOW,forecast=Mock())


def test_reader_never_displays_retained_forecast_as_current():
    r=research.research_forecast(quote(),history(),NOW,forecast=prediction)
    p={'symbols':{'BTC-USD':r}}
    view=research.display_research(p,NOW+timedelta(seconds=11))
    assert view['symbols']['BTC-USD']['current'] is False
    assert view['symbols']['BTC-USD']['primary_quote']['fresh'] is False
    assert view['symbols']['BTC-USD']['primary_quote']['stale'] is True
    assert view['symbols']['BTC-USD']['delivery']=='RETAINED_RESEARCH_OBSERVATION'
    assert 'current' not in p['symbols']['BTC-USD']


@pytest.mark.parametrize('setting,value', [('EXECUTION_MODE','live'),('ENABLE_BROKER_SUBMISSION','true'),
    ('LIVE_TRADING_ARMED','true'),('KRAKEN_REFERENCE_WS_ENABLED','false'),('KRAKEN_PRIMARY_RESEARCH_ENABLED','false')])
def test_research_install_safety_veto(monkeypatch,setting,value):
    for k,v in {'EXECUTION_MODE':'paper','ENABLE_BROKER_SUBMISSION':'false','LIVE_TRADING_ARMED':'false',
                'KRAKEN_REFERENCE_WS_ENABLED':'true','KRAKEN_PRIMARY_RESEARCH_ENABLED':'true'}.items():monkeypatch.setenv(k,v)
    monkeypatch.setenv(setting,value)
    assert research.enabled() is False


def request_client(monkeypatch, responses):
    monkeypatch.setattr(data,'provider_cooldown_active_live',lambda p:{'active':False})
    monkeypatch.setattr(data,'reserve_provider_budget_live',lambda *a,**k:{'reserved':True})
    marker=Mock();monkeypatch.setattr(data,'mark_provider_cooldown_live',marker)
    get=Mock(side_effect=responses);return data.PublicResearchData(get),get,marker


def response(payload,status=200):return SimpleNamespace(status_code=status,json=lambda:payload)


def test_budget_blocks_network(monkeypatch):
    c,get,_=request_client(monkeypatch,[])
    monkeypatch.setattr(data,'reserve_provider_budget_live',lambda *a,**k:{'reserved':False})
    with pytest.raises(data.ResearchUnavailable,match='BUDGET'):c.instruments()
    get.assert_not_called()


@pytest.mark.parametrize('denial,expected', [
    ({'reserved': False, 'reason': 'provider capability budget exhausted'},
     'PROVIDER_BUDGET_EXHAUSTED'),
    ({'reserved': False, 'reason': 'shared provider ledger unavailable; fail closed: OperationalError'},
     'PROVIDER_LEDGER_UNAVAILABLE'),
    ({'reserved': False, 'reason': 'provider budget row unavailable after atomic initialization'},
     'PROVIDER_LEDGER_UNAVAILABLE'),
    ({'reserved': False, 'reason': 'provider capability in cooldown'},
     'PROVIDER_COOLDOWN'),
    ({'reserved': False, 'reason': 'provider capability has no initialized capacity'},
     'PROVIDER_BUDGET_BLOCKED'),
    ({'reserved': False, 'reason': 'unrecognized private provider error'},
     'PROVIDER_BUDGET_BLOCKED'),
])
def test_budget_denial_is_classified_without_network_or_sensitive_error(monkeypatch, denial, expected):
    c, get, _ = request_client(monkeypatch, [])
    monkeypatch.setattr(data, 'reserve_provider_budget_live', lambda *a, **k: denial)
    with pytest.raises(data.ResearchUnavailable) as error:
        c.instruments()
    assert str(error.value) == expected
    get.assert_not_called()


def test_shared_cooldown_ledger_failure_is_not_mislabeled_provider_cooldown(monkeypatch):
    c, get, _ = request_client(monkeypatch, [])
    monkeypatch.setattr(data, 'provider_cooldown_active_live',
                        lambda *a: {'active': True,
                                    'reason': 'shared provider cooldown unavailable; fail closed: DatabaseError'})
    with pytest.raises(data.ResearchUnavailable) as error:
        c.instruments()
    assert str(error.value) == 'PROVIDER_LEDGER_UNAVAILABLE'
    get.assert_not_called()


@pytest.mark.parametrize('payload,status', [({},429),({'error':['EAPI:Rate limit exceeded']},200)])
def test_rate_limit_is_shared_and_local_without_repeat_requests(monkeypatch,payload,status):
    c,get,marker=request_client(monkeypatch,[response(payload,status)])
    for _ in range(2):
        with pytest.raises(data.ResearchUnavailable):c.instruments()
    assert get.call_count==1 and marker.call_count==1


def test_cache_preserves_original_receipt_and_budget(monkeypatch):
    c,get,_=request_client(monkeypatch,[response({'error':[],'result':{'BTC/USD':{}}})])
    first=c.instruments(); receipt=c.last_receipt
    assert c.instruments() is first and c.last_receipt==receipt and get.call_count==1


def test_discovery_is_exact_online_currency_usd_and_bounded():
    meta={s+'/USD':{'base':s,'quote':'USD','status':'online','aclass_base':'currency'} for s in ['SOL','LTC','AAVE']}
    meta['BAD/USD']={'base':'BAD','quote':'USD','status':'cancel_only','aclass_base':'currency'}
    result=data.select_instruments(meta,['SOL-USD','LTC-USD','AAVE-USD','BAD-USD','BTC-USDT','XBT-USD'])
    assert len(result)==4 and result['BTC/USD']=='BTC-USD' and 'BAD/USD' not in result and 'XBT/USD' not in result


def rows():
    h=history()
    return [[int(t.timestamp()),'100','101','99','100','100','2',1] for t in h.index]+[
        [int(NOW.timestamp()),'100','999999','1','999999','100','2',1]]


def test_bootstrap_discards_unclosed_candle_and_preserves_source(monkeypatch):
    c,_,_=request_client(monkeypatch,[response({'error':[],'result':{'BTC/USD':rows(),'last':1}})])
    h=c.history('BTC-USD',NOW)
    assert len(h)==60 and h.Close.max()==100 and h.attrs['provider']=='Kraken Public OHLC'
    assert h.attrs['quote_verified'] is False


@pytest.mark.parametrize('bad',['gap','duplicate','nan','crossed','stale','wrong_symbol'])
def test_malformed_bootstrap_is_not_training_data(monkeypatch,bad):
    r=rows();pair='BTC/USD'
    if bad=='gap':r.pop(2)
    if bad=='duplicate':r[2][0]=r[1][0]
    if bad=='nan':r[2][4]='NaN'
    if bad=='crossed':r[2][2]='90'
    if bad=='stale':
        for row in r:row[0]-=3600
    if bad=='wrong_symbol':pair='ETH/USD'
    c,_,_=request_client(monkeypatch,[response({'error':[],'result':{pair:r,'last':1}})])
    with pytest.raises(ValueError):c.history('BTC-USD',NOW)


@pytest.mark.parametrize('change',[{'auction_mode':True},{'time':(NOW-timedelta(seconds=11)).isoformat()},
    {'bids':[['102','1',1]]},{'asks':[['101','0',1]]},{'time':None}])
def test_coinbase_diagnostic_rejects_bad_data(monkeypatch,change):
    payload={'time':NOW.isoformat(),'bids':[['100','1',1]],'asks':[['101','1',1]],**change}
    c,_,_=request_client(monkeypatch,[response(payload)])
    with pytest.raises(ValueError):data.coinbase_quote(c,'BTC-USD',NOW)


def test_coinbase_backup_is_explicit_and_cannot_mix_history_venues():
    q=quote();q.update(provider='Coinbase Exchange public reference',checksum_status='NOT_PROVIDED')
    with pytest.raises(data.ResearchUnavailable,match='VENUE'):research.research_forecast(q,history(),NOW,forecast=prediction)
    h=history();h.attrs['provider']='Coinbase Exchange'
    out=research.research_forecast(q,h,NOW,forecast=prediction)
    assert out['data_role']=='COINBASE_RESEARCH_BACKUP' and out['execution_authority']=='NONE'


def test_expanded_books_use_same_checksum_and_authority_boundaries():
    from kraken_reference_book import ReferenceBooks
    from test_kraken_reference_book import ack,message
    import json
    books=ReferenceBooks({'BTC/USD':'BTC-USD','ETH/USD':'ETH-USD','SOL/USD':'SOL-USD'})
    books.opened(NOW)
    acknowledgement=json.loads(ack());acknowledgement['result']['symbol']='SOL/USD'
    snapshot=json.loads(message());snapshot['data'][0]['symbol']='SOL/USD'
    books.ingest(json.dumps(acknowledgement),NOW)
    books.ingest(json.dumps(snapshot),NOW)
    q=books.quote('SOL/USD',NOW)
    assert q['symbol']=='SOL-USD' and q['checksum_status']=='VALID'
    assert q['quote_verified'] is False and q['execution_quote_eligible'] is False


def test_brain_consumes_research_separately_from_qualified_outcomes():
    from oracle_brain import build_oracle_brain_snapshot
    from test_oracle_brain import _fetch
    r=research.research_forecast(quote(),history(),NOW,forecast=prediction)
    def fetch(query,*args,**kwargs):
        if 'to_regclass' in query:return [{'relation':'kraken_research_state'}]
        if 'FROM kraken_research_state' in query:return [{'payload':{'symbols':{'BTC-USD':r}}}]
        return _fetch(query,*args,**kwargs)
    snap=build_oracle_brain_snapshot(fetch,market_scope='crypto')
    assert snap['crypto_primary_research']['symbols']['BTC-USD']['profitability_status']=='UNPROVEN'
    assert snap['summary']['exact_episodes']==0


def test_cooldown_expiry_and_shared_cooldown_prevent_network(monkeypatch):
    c,get,_=request_client(monkeypatch,[response({'error':[],'result':{}})])
    clock=[0.];monkeypatch.setattr(data.time,'monotonic',lambda:clock[0])
    c.cooldowns['Kraken Public Research']=300
    with pytest.raises(data.ResearchUnavailable):c.instruments()
    clock[0]=301
    monkeypatch.setattr(data,'provider_cooldown_active_live',lambda p:{'active':True})
    with pytest.raises(data.ResearchUnavailable):c.instruments()
    get.assert_not_called()
    monkeypatch.setattr(data,'provider_cooldown_active_live',lambda p:{'active':False})
    assert c.instruments()=={} and get.call_count==1


def test_rejected_expansion_can_recover_base_feed_without_resetting_error_counts():
    from kraken_reference_runtime import KrakenReferenceService
    service=KrakenReferenceService(connect_factory=Mock())
    service.symbols['SOL/USD']='SOL-USD'
    service.state.counts['subscription_error']=1
    service.restrict_base_coverage()
    assert service.symbols=={'BTC/USD':'BTC-USD','ETH/USD':'ETH-USD'}
    assert service.state.counts['subscription_error']==1 and not service.state.connected
    assert set(service.reference_history)=={'BTC-USD','ETH-USD'}


def test_cycle_uses_labeled_coinbase_backup_without_touching_broker(monkeypatch):
    import kraken_reference_runtime as runtime
    import forecasting
    h=history();h.attrs['provider']='Coinbase Exchange'
    q=quote();q.update(provider='Coinbase Exchange public reference',checksum_status='NOT_PROVIDED')
    # Freeze both sample and receipt clocks; this is explicitly a mocked backup.
    class Clock:
        @staticmethod
        def now(tz):return NOW
    monkeypatch.setattr(research,'datetime',Clock)
    monkeypatch.setattr(runtime,'read_kraken_reference_state',lambda:{'subscribed_symbols':['BTC-USD'],'quotes':{}})
    monkeypatch.setattr(research,'coinbase_history',lambda *a:h)
    coinbase=Mock(return_value=q);monkeypatch.setattr(research,'coinbase_quote',coinbase)
    monkeypatch.setattr(forecasting,'forecast_price',prediction)
    persist=Mock(return_value=7);monkeypatch.setattr(research,'persist_evidence',persist)
    conn=Mock();conn.execute.return_value.fetchone.return_value={'highest_id':0}
    runner=research.PrimaryResearch(connect_factory=Mock(),data=Mock())
    runner.cycle(conn)
    payload=json.loads(conn.execute.call_args.args[1][1])
    assert payload['symbols']['BTC-USD']['data_role']=='COINBASE_RESEARCH_BACKUP'
    assert payload['symbols']['BTC-USD']['evidence_id']==7
    assert payload['symbols']['BTC-USD']['primary_quote']['execution_quote_eligible'] is False
    persist.assert_called_once()
    # The fallback quote is necessary; another Coinbase read cannot compare
    # venues when Kraken has no reference, even if the first read is cached.
    coinbase.assert_called_once_with(runner.data,'BTC-USD')
    assert payload['symbols']['BTC-USD']['independent_comparison']['reason']=='REFERENCE_UNAVAILABLE'


def test_cycle_compares_coinbase_when_fresh_kraken_reference_exists(monkeypatch):
    import kraken_reference_runtime as runtime
    import forecasting
    class Clock:
        @staticmethod
        def now(tz):return NOW
    monkeypatch.setattr(research,'datetime',Clock)
    reference=quote()
    monkeypatch.setattr(runtime,'read_kraken_reference_state',lambda:{
        'subscribed_symbols':['BTC-USD'],'quotes':{'BTC-USD':reference}})
    independent=dict(reference,provider='Coinbase Exchange public reference',checksum_status='NOT_PROVIDED')
    coinbase=Mock(return_value=independent);monkeypatch.setattr(research,'coinbase_quote',coinbase)
    backup_history=Mock();monkeypatch.setattr(research,'coinbase_history',backup_history)
    monkeypatch.setattr(forecasting,'forecast_price',prediction)
    persist=Mock(return_value=8);monkeypatch.setattr(research,'persist_evidence',persist)
    conn=Mock();conn.execute.return_value.fetchone.return_value={'highest_id':0}
    client=Mock();client.history.return_value=history()
    runner=research.PrimaryResearch(connect_factory=Mock(),data=client)
    runner.cycle(conn)
    result=json.loads(conn.execute.call_args.args[1][1])['symbols']['BTC-USD']
    assert result['data_role']=='KRAKEN_PRIMARY' and result['evidence_id']==8
    assert result['independent_comparison']['status']=='ALIGNED_VENUE_COMPARISON'
    assert result['independent_comparison']['execution_impact']=='NONE'
    assert result['execution_authority']=='NONE'
    assert result['primary_quote']['execution_quote_eligible'] is False
    coinbase.assert_called_once_with(client,'BTC-USD')
    backup_history.assert_not_called()
    persist.assert_called_once()


def test_standby_research_process_performs_no_http_or_sampling(monkeypatch):
    from contextlib import contextmanager
    conn=Mock();conn.execute.return_value.fetchone.return_value={'owned':False}
    @contextmanager
    def connect():yield conn
    runner=research.PrimaryResearch(connect_factory=connect,data=Mock())
    monkeypatch.setattr(research,'enabled',lambda:True)
    runner.cycle=Mock()
    monkeypatch.setattr(runner.stop,'wait',lambda seconds:runner.stop.set())
    runner.run()
    runner.cycle.assert_not_called()
    runner.data.history.assert_not_called()


def test_coinbase_350_row_response_is_bounded_to_300_closed_bars(monkeypatch):
    times=pd.date_range(end=NOW,periods=350,freq='5min')
    r=[[int(t.timestamp()),99,101,100,100,2] for t in reversed(times)]
    c,get,_=request_client(monkeypatch,[response(r)])
    h=data.coinbase_history(c,'BTC-USD',NOW)
    assert len(h)==300 and h.index[-1]==pd.Timestamp(NOW)-pd.Timedelta(minutes=5)
    assert h.attrs['provider']=='Coinbase Exchange' and h.attrs['quote_verified'] is False
    assert h.index.is_monotonic_increasing


@pytest.mark.parametrize('metadata',[[],[{'relation':None}]])
def test_disabled_reader_does_not_query_optional_missing_table(metadata):
    fetch=Mock(return_value=metadata)
    assert research.read_research(fetch,NOW)['status']=='UNAVAILABLE'
    fetch.assert_called_once_with("SELECT to_regclass('kraken_research_state') AS relation")


def test_live_books_expire_independently_of_history_and_receipt_is_not_renewed(monkeypatch):
    c,get,_=request_client(monkeypatch,[response({'time':'original'}),response({'time':'new'})])
    clock=[0.0];monkeypatch.setattr(data.time,'monotonic',lambda:clock[0])
    first=c.request('Coinbase Public Research','products/BTC-USD/book',{'level':1});receipt=c.last_receipt
    clock[0]=1
    assert c.request('Coinbase Public Research','products/BTC-USD/book',{'level':1}) is first
    assert c.last_receipt==receipt
    clock[0]=3
    assert c.request('Coinbase Public Research','products/BTC-USD/book',{'level':1})['time']=='new'
    assert get.call_count==2
