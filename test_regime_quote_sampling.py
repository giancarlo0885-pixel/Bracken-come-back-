"""MFE/MAE sampling must use original validated quotes, never entry-price fallbacks."""
from contextlib import contextmanager
from datetime import datetime
from types import SimpleNamespace
import json
from unittest.mock import Mock
from copy import deepcopy
import pytest
import market_worker
import paper_regime_economics_shadow as regime
from test_coinbase_paper_pricing import provider, NOW
from test_kraken_reference_pg import pg


@pytest.fixture(autouse=True)
def isolated_observations(monkeypatch):
    monkeypatch.setattr(regime,'_QUOTE_OBSERVATIONS',{})


@pytest.mark.parametrize('available',[False,True])
def test_sampler_never_treats_entry_or_average_price_as_current(monkeypatch,available):
    import database
    writes=[]
    monkeypatch.setattr(regime,'active',lambda:True)
    monkeypatch.setattr(market_worker,'_coinbase_paper_pricing',None,raising=False)
    @contextmanager
    def connect():
        def execute(sql,params):
            if 'INSERT' in sql:writes.append(params)
            return SimpleNamespace(fetchall=lambda:[{'symbol':'BTC-USD','current_price':100 if available else None,'average_price':99,'entry_price':98}])
        yield SimpleNamespace(execute=execute)
    monkeypatch.setattr(database,'connect',connect)
    assert regime.sample_open_positions('crypto')==0
    assert writes==[]


def test_pg_verified_quote_sampling_preserves_source_time_and_deduplicates(pg,monkeypatch):
    import database
    pricing=provider(monkeypatch);q=pricing.quote('BTC-USD')
    assert q
    pricing.data.request=Mock(side_effect=AssertionError('Sampling must not issue HTTP'))
    monkeypatch.setattr(market_worker,'_coinbase_paper_pricing',pricing,raising=False)
    monkeypatch.setattr(regime,'active',lambda:True)
    class Clock(datetime):
        @classmethod
        def now(cls,tz):return NOW
    monkeypatch.setattr(regime,'datetime',Clock)
    @contextmanager
    def transaction():
        with pg() as connection:
            with connection.transaction():yield connection
    monkeypatch.setattr(database,'connect',transaction)
    original=deepcopy(q)
    assert regime.observe_paper_quote(pricing,q)
    assert not regime.observe_paper_quote(pricing,q)
    assert q==original
    with pg() as conn:
        conn.execute('CREATE TABLE positions(market text,symbol text,quantity double precision,current_price double precision,average_price double precision,entry_price double precision)')
        conn.execute("INSERT INTO positions VALUES('crypto','BTC-USD',1,1,2,3)")
        conn.execute('CREATE TABLE paper_regime_price_samples(id bigserial PRIMARY KEY,market text,symbol text,observed_at timestamptz,price double precision,source text,schema_version text)')
        from concurrent.futures import ThreadPoolExecutor
        with ThreadPoolExecutor(max_workers=2) as executor:
            counts=list(executor.map(lambda _:regime.sample_open_positions('crypto'),range(2)))
        assert sum(counts)==1
        assert regime.sample_open_positions('crypto')==0
        rows=conn.execute('SELECT * FROM paper_regime_price_samples').fetchall()
        assert len(rows)==1 and rows[0]['price']==q['price']
        assert rows[0]['observed_at']==datetime.fromisoformat(q['quote_timestamp'])
        assert rows[0]['source'].startswith('verified_quote_v1:')
        source=json.loads(rows[0]['source'].split('|',1)[1])
        assert source['quote_id']==q['quote_id'] and source['provider']==q['provider']
        assert source['received_at']==q['received_at']
        assert source['execution_authority']=='NONE'
        # Another collector can receive the same exchange event later. Receipt
        # capture time must not turn that event into another persisted sample.
        from datetime import timedelta
        monkeypatch.setattr(regime,'_QUOTE_OBSERVATIONS',{})
        class RestartClock(datetime):
            @classmethod
            def now(cls,tz):return NOW+timedelta(seconds=1)
        monkeypatch.setattr(regime,'datetime',RestartClock)
        assert regime.observe_paper_quote(pricing,q)
        assert regime.sample_open_positions('crypto')==0
        # Original quotes remain unchanged; expiry does not get a new timestamp.
        pricing.clock=lambda:NOW+timedelta(seconds=6)
        assert not regime.observe_paper_quote(pricing,q)
        assert regime.sample_open_positions('crypto')==0
        assert conn.execute('SELECT count(*) AS n FROM paper_regime_price_samples').fetchone()['n']==1
        pricing.data.request.assert_not_called()


def test_cash_mark_is_unavailable_without_original_quote_receipt(monkeypatch):
    monkeypatch.setattr(market_worker,'_coinbase_paper_pricing',None,raising=False)
    assert regime._cached_observation('cash','AAPL') is None


def test_quote_capture_hook_preserves_original_and_never_grants_execution(monkeypatch):
    pricing=provider(monkeypatch)
    monkeypatch.setattr(regime,'active',lambda:True)
    class Clock(datetime):
        @classmethod
        def now(cls,tz):return NOW
    monkeypatch.setattr(regime,'datetime',Clock)
    q=pricing.quote('BTC-USD')
    observation=regime._cached_observation('crypto','BTC-USD')
    assert observation and observation['price']==q['price']
    assert json.loads(observation['source'].split('|',1)[1])['execution_authority']=='NONE'
    assert q['execution_authority']=='PAPER_SIMULATION_ONLY'
    assert pricing.authentic(q,'BTC-USD')


def test_pg_excursions_exclude_legacy_marks_and_keep_original_ready_version(pg,monkeypatch):
    from datetime import timedelta
    from test_paper_regime_economics_shadow import _FinalizeConn, _linked_close, _finalize_connection
    trade,allocation=_linked_close()
    with pg() as actual:
        actual.execute('CREATE TABLE paper_regime_price_samples(market text,symbol text,observed_at timestamptz,price double precision,source text)')
        moment=trade['entry_time']+timedelta(minutes=1)
        actual.execute('INSERT INTO paper_regime_price_samples VALUES(%s,%s,%s,999,%s)',('crypto',trade['symbol'],moment,'canonical_position'))
        actual.execute('INSERT INTO paper_regime_price_samples VALUES(%s,%s,%s,105,%s)',('crypto',trade['symbol'],moment,'verified_quote_v1:disposable-fixture|'+json.dumps({'quote_id':'disposable-fixture'})))
        class Connection(_FinalizeConn):
            def execute(self,sql,params=None):
                if 'SELECT observed_at,price' in sql:return actual.execute(sql,params)
                return super().execute(sql,params)
        fixture=Connection([trade])
        _finalize_connection(monkeypatch,fixture,[allocation])
        assert regime.finalize_closed_trades()==1
        params=fixture.writes[0][1]
        assert params[12]==pytest.approx(5)
        assert params[13]==0 and params[14]==1
        assert params[15]==regime._ENTRY_READY_VERSION
        assert actual.execute('SELECT count(*) AS n FROM paper_regime_price_samples').fetchone()['n']==2


@pytest.mark.parametrize('kind',['reference_only','stale','future','disabled'])
def test_capture_rejects_unqualified_or_mistimed_quotes(monkeypatch,kind):
    from datetime import timedelta
    pricing=provider(monkeypatch)
    q=pricing.quote('BTC-USD')
    monkeypatch.setattr(regime,'_QUOTE_OBSERVATIONS',{})
    monkeypatch.setattr(regime,'active',lambda:kind!='disabled')
    class Clock(datetime):
        @classmethod
        def now(cls,tz):return NOW-timedelta(seconds=1) if kind=='future' else NOW
    monkeypatch.setattr(regime,'datetime',Clock)
    if kind=='reference_only':q={**q,'provider':'Kraken WebSocket v2 reference','execution_authority':'NONE'}
    if kind=='stale':pricing.clock=lambda:NOW+timedelta(seconds=6)
    if kind=='future':pricing.clock=lambda:NOW-timedelta(seconds=1)
    assert not regime.observe_paper_quote(pricing,q)
    assert regime._cached_observation('crypto','BTC-USD') is None


def test_observation_cache_is_bounded(monkeypatch):
    pricing=provider(monkeypatch);q=pricing.quote('BTC-USD')
    monkeypatch.setattr(regime,'_QUOTE_OBSERVATIONS',{})
    monkeypatch.setattr(regime,'active',lambda:True)
    monkeypatch.setattr(pricing,'authentic',lambda quote,symbol:True)  # Simulated validated source boundary.
    class Clock(datetime):
        @classmethod
        def now(cls,tz):return NOW
    monkeypatch.setattr(regime,'datetime',Clock)
    for i in range(80):
        symbol='FIXTURE'+str(i)+'-USD'
        assert regime.observe_paper_quote(pricing,{**q,'symbol':symbol,'requested_symbol':symbol,'provider_symbol':symbol})
    assert len(regime._QUOTE_OBSERVATIONS)==64
