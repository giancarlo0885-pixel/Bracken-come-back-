"""Slow public HTTP responses must not consume cache lifetime before receipt."""
from types import SimpleNamespace
from unittest.mock import Mock
import pytest
import kraken_research_data as data


def client(monkeypatch):
    clock=[100.0]
    monkeypatch.setattr(data.time,'monotonic',lambda:clock[0])
    monkeypatch.setattr(data,'provider_cooldown_active_live',lambda _: {'active':False})
    reserve=Mock(return_value={'reserved':True})
    monkeypatch.setattr(data,'reserve_provider_budget_live',reserve)
    def response(*a,**k):
        clock[0]+=2.5
        return SimpleNamespace(status_code=200,json=lambda:{'time':'2026-10-29T16:00:00Z','source':'original-provider-payload'})
    get=Mock(side_effect=response)
    return data.PublicResearchData(get),get,reserve,clock


def test_slow_http_response_keeps_two_seconds_of_cache_after_receipt(monkeypatch):
    c,get,reserve,clock=client(monkeypatch)
    payload=c.request('Coinbase Public Research','products/BTC-USD/book',{'level':2})
    receipt=c.last_receipt
    clock[0]+=1.9
    assert c.request('Coinbase Public Research','products/BTC-USD/book',{'level':2}) is payload
    assert c.last_receipt==receipt
    assert payload['time']=='2026-10-29T16:00:00Z'
    assert get.call_count==reserve.call_count==1
    clock[0]+=.2
    c.request('Coinbase Public Research','products/BTC-USD/book',{'level':2})
    assert get.call_count==reserve.call_count==2


def test_cache_growth_and_provider_budget_boundaries_unchanged(monkeypatch):
    c,get,reserve,clock=client(monkeypatch)
    for i in range(80):c.request('Coinbase Public Research','products/fixture-'+str(i))
    assert len(c.cache)<=12
    reserve.return_value={'reserved':False,'reason':'provider capability budget exhausted'}
    with pytest.raises(data.ResearchUnavailable,match='PROVIDER_BUDGET_EXHAUSTED'):
        c.request('Coinbase Public Research','products/not-cached')
    assert get.call_count==80
