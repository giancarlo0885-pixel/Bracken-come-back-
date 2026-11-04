"""Absolute paper price anchors must agree with the original preforecast quote."""
from copy import deepcopy
from dataclasses import asdict, replace
from datetime import timedelta
import json
from unittest.mock import Mock

import pytest

from forecasting import Forecast
from paper_forecast_reference import bind_paper_forecast_reference, VERSION
from test_kraken_paper_pricing import provider
from test_kraken_reference_book import NOW
from test_scan_evidence_commit_boundary import scan
import market_worker as worker


def original(quote):
    return Forecast(symbol='BTC-USD',market='crypto',asset_class='crypto',
        requested_symbol='BTC-USD',provider_symbol='BTC-USD',source_interval='5m',
        forecast_id='original-model-forecast',source_quote_timestamp=quote['quote_timestamp'],
        generated_at=(NOW+timedelta(milliseconds=1)).isoformat(),spot_price=90.,
        target_price=92.7,low_price=85.5,high_price=99.,expected_move_pct=3.,
        probability_up=.7,model='unchanged-model',model_version='unchanged-version')


def test_original_model_return_binds_current_quote_without_mutating_any_input(monkeypatch):
    pricing,_=provider(monkeypatch)
    quote=pricing.quote('BTC-USD');before_quote=deepcopy(quote)
    forecast=original(quote);before_forecast=asdict(forecast)
    bound=bind_paper_forecast_reference(forecast,quote,'BTC-USD',authentic=pricing.authentic)
    assert bound is not forecast and asdict(forecast)==before_forecast and quote==before_quote
    assert bound.spot_price==quote['price']
    assert bound.target_price/bound.spot_price==pytest.approx(forecast.target_price/forecast.spot_price)
    assert bound.low_price/bound.spot_price==pytest.approx(.95)
    assert bound.high_price/bound.spot_price==pytest.approx(1.1)
    assert bound.expected_move_pct==3. and bound.probability_up==.7
    assert (bound.model,bound.model_version)==(forecast.model,forecast.model_version)
    assert bound.forecast_id!=forecast.forecast_id
    assert bound.reference['version']==VERSION
    assert bound.reference['original']['forecast_id']==forecast.forecast_id
    assert bound.reference['original']['spot_price']==90.
    assert bound.reference['bound_forecast_id']==bound.forecast_id
    assert bound.reference['quote_id']==quote['quote_id']
    assert bound.reference['execution_authority']=='NONE'
    with pytest.raises(ValueError,match='already been bound'):
        bind_paper_forecast_reference(bound,quote,'BTC-USD',authentic=pricing.authentic)
    with pytest.raises(ValueError,match='already been bound'):
        bind_paper_forecast_reference(replace(bound,target_price=bound.target_price*1.01),quote,'BTC-USD',authentic=pricing.authentic)
    json.dumps(asdict(bound),allow_nan=False)


def test_binding_never_fetches_or_refreshes_a_quote(monkeypatch):
    pricing,_=provider(monkeypatch)
    quote=pricing.quote('BTC-USD');forecast=original(quote)
    pricing.quote=Mock(side_effect=AssertionError('binding cannot fetch a later quote'))
    assert bind_paper_forecast_reference(forecast,quote,'BTC-USD',authentic=pricing.authentic).spot_price==quote['price']
    pricing.quote.assert_not_called()


@pytest.mark.parametrize('kind',['stale','raw_reference','other_timestamp','wrong_forecast_symbol','invalid_spot'])
def test_missing_or_conflicting_original_evidence_cannot_bind(monkeypatch,kind):
    pricing,state=provider(monkeypatch)
    quote=pricing.quote('BTC-USD');forecast=original(quote)
    if kind=='stale':pricing.clock=lambda:NOW+timedelta(seconds=6)
    elif kind=='raw_reference':quote=state['quotes']['BTC-USD']
    elif kind=='other_timestamp':forecast=replace(forecast,source_quote_timestamp=(NOW-timedelta(seconds=1)).isoformat())
    elif kind=='wrong_forecast_symbol':forecast=replace(forecast,symbol='ETH-USD')
    elif kind=='invalid_spot':forecast=replace(forecast,spot_price=float('nan'))
    with pytest.raises(ValueError):
        bind_paper_forecast_reference(forecast,quote,'BTC-USD',authentic=pricing.authentic)


@pytest.mark.parametrize('name',['fast_scan_market','scan_market'])
def test_both_scan_paths_persist_bound_reference_before_handoff(monkeypatch,scan,name):
    pricing,_=provider(monkeypatch);quote=pricing.quote('BTC-USD');forecast=original(quote)
    monkeypatch.setattr(worker,'forecast_price',lambda *a,**k:forecast)
    monkeypatch.setattr(worker,'_execution_quote_payload_from_history',lambda *a,**k:quote)
    monkeypatch.setattr(worker,'_bind_paper_forecast_reference',lambda f,q,s:bind_paper_forecast_reference(f,q,s,authentic=pricing.authentic))
    writes=Mock(return_value=42);saved=Mock()
    monkeypatch.setattr(worker,'save_json_signal',writes)
    monkeypatch.setattr(worker,'save_forecast',saved)
    getattr(worker,name)('crypto')
    payload=writes.call_args.args[6]
    bound=saved.call_args.args[2]
    assert payload['forecast_reference']==bound.reference
    assert payload['forecast_reference']['original']['spot_price']==90.
    assert payload['forecast_reference']['quote_id']==quote['quote_id']
    assert payload['forecast_id']==bound.forecast_id!=forecast.forecast_id
    assert bound.spot_price==quote['price']
    assert scan[2].call_args.args[1][0].forecast_id==bound.forecast_id


@pytest.mark.parametrize('name',['fast_scan_market','scan_market'])
def test_unbound_original_quote_cannot_create_a_forecast_identity(monkeypatch,scan,name):
    pricing,_=provider(monkeypatch);quote=pricing.quote('BTC-USD');forecast=original(quote)
    monkeypatch.setattr(worker,'forecast_price',lambda *a,**k:forecast)
    monkeypatch.setattr(worker,'_execution_quote_payload_from_history',lambda *a,**k:quote)
    monkeypatch.setattr(worker,'_bind_paper_forecast_reference',lambda *_:(_ for _ in ()).throw(ValueError('stale')))
    writes=Mock(return_value=42);saved=Mock()
    monkeypatch.setattr(worker,'save_json_signal',writes);monkeypatch.setattr(worker,'save_forecast',saved)
    getattr(worker,name)('crypto')
    assert writes.call_args.args[6]['forecast_unavailable_reason']=='paper_forecast_reference_unavailable'
    assert 'forecast_reference' not in writes.call_args.args[6]
    assert getattr(scan[0],'forecast_id',None) is None
    saved.assert_not_called()


def test_cash_legacy_and_model_abstention_stay_unchanged(monkeypatch):
    callback=Mock(side_effect=AssertionError('must not bind'))
    monkeypatch.setattr(worker,'_bind_paper_forecast_reference',callback)
    forecast=Forecast();diagnostics={'unavailable_reason':'causal_model_abstention'}
    assert worker._bind_forecast_reference('cash',forecast,{},'MSFT',{}) is forecast
    assert worker._bind_forecast_reference('crypto',None,{},'BTC-USD',diagnostics) is None
    assert diagnostics['unavailable_reason']=='causal_model_abstention'
    callback.assert_not_called()
    monkeypatch.setattr(worker,'_bind_paper_forecast_reference',None)
    assert worker._bind_forecast_reference('crypto',forecast,{},'BTC-USD',{}) is forecast


@pytest.mark.parametrize('name',['fast_scan_market','scan_market'])
def test_pg_original_and_bound_evidence_join_exact_forecast_and_signal(pg,monkeypatch,scan,name):
    from contextlib import contextmanager
    import database
    pricing,_=provider(monkeypatch);quote=pricing.quote('BTC-USD');forecast=original(quote)
    with pg() as conn:
        conn.execute('CREATE TABLE signals(id bigserial PRIMARY KEY,market text,symbol text,price float8,score float8,action text,confidence float8,details text,created_at text)')
        conn.execute("""CREATE TABLE forecasts(market text,symbol text,requested_symbol text,provider_symbol text,
            source_interval text,source_quote_timestamp text,scan_type text,model_version text,expected_move_pct float8,
            signal_id bigint,signal_created_at text,data_quality_score float8,forecast_id text,horizon_days float8,
            horizon_bars int,horizon_minutes float8,target_price float8,low_price float8,high_price float8,
            probability_up float8,model text,created_at text)""")
    @contextmanager
    def connection():
        with pg() as conn:
            with conn.transaction():yield conn
    monkeypatch.setattr(database,'connect',connection)
    monkeypatch.setattr(worker,'save_json_signal',database.save_json_signal)
    monkeypatch.setattr(worker,'save_forecast',database.save_forecast)
    monkeypatch.setattr(worker,'forecast_price',lambda *a,**k:forecast)
    monkeypatch.setattr(worker,'_execution_quote_payload_from_history',lambda *a,**k:quote)
    monkeypatch.setattr(worker,'_bind_paper_forecast_reference',lambda f,q,s:bind_paper_forecast_reference(f,q,s,authentic=pricing.authentic))
    getattr(worker,name)('crypto')
    with pg() as conn:
        rows=conn.execute('SELECT f.*,s.details FROM forecasts f JOIN signals s ON s.id=f.signal_id AND s.symbol=f.symbol AND s.market=f.market').fetchall()
    assert len(rows)==1
    row=rows[0];details=json.loads(row['details']);reference=details['forecast_reference']
    assert row['forecast_id']==details['forecast_id']==reference['bound_forecast_id']
    assert row['forecast_id']!=reference['original']['forecast_id']==forecast.forecast_id
    assert reference['original']['spot_price']==90.
    assert reference['anchored']['spot_price']==quote['price']
    assert row['target_price']==reference['anchored']['target_price']
    assert row['low_price']==reference['anchored']['low_price']
    assert row['high_price']==reference['anchored']['high_price']
    assert row['expected_move_pct']==forecast.expected_move_pct==reference['original']['expected_move_pct']
    assert row['probability_up']==forecast.probability_up
    assert row['model_version']==forecast.model_version
    assert reference['quote_id']==quote['quote_id']
    assert scan[2].call_args.args[1][0].forecast_id==row['forecast_id']


from test_kraken_reference_pg import pg
