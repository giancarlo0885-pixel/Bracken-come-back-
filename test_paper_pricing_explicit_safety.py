"""Missing execution controls must never register public paper pricing receipts."""
from copy import deepcopy
from unittest.mock import Mock
import pytest
import coinbase_paper_pricing as shared
import kraken_paper_pricing as kraken
from test_kraken_paper_pricing import provider

SAFETY={'EXECUTION_MODE':'paper','ENABLE_BROKER_SUBMISSION':'false','LIVE_TRADING_ARMED':'false'}

@pytest.mark.parametrize('missing',[('EXECUTION_MODE',),('ENABLE_BROKER_SUBMISSION',),('LIVE_TRADING_ARMED',),tuple(SAFETY)])
def test_missing_safety_stops_context_install_and_actual_kraken_receipts(monkeypatch,missing):
    p,state=provider(monkeypatch)
    original=deepcopy(state)
    monkeypatch.setenv('COINBASE_PAPER_PRICING_ENABLED','true')
    assert shared.paper_context_enabled() and kraken.enabled()
    for key in missing:monkeypatch.delenv(key,raising=False)
    assert shared.paper_context_enabled() is False
    assert shared.enabled() is False and kraken.enabled() is False
    worker=Mock()
    assert shared.install_paper_pricing(worker,p) is False
    assert p.quote('BTC-USD') is None and not p.receipts
    assert state==original
    worker._execution_quote_payload_from_history.assert_not_called()

@pytest.mark.parametrize('key,value',[('EXECUTION_MODE',''),('EXECUTION_MODE','live'),
    ('ENABLE_BROKER_SUBMISSION',''),('ENABLE_BROKER_SUBMISSION','true'),
    ('LIVE_TRADING_ARMED',''),('LIVE_TRADING_ARMED','true')])
def test_empty_or_contradictory_values_do_not_enable_pricing(monkeypatch,key,value):
    p,_=provider(monkeypatch);monkeypatch.setenv(key,value)
    assert not shared.paper_context_enabled() and not p.available()
    assert p.quote('BTC-USD') is None

def test_explicit_paper_settings_keep_original_receipt_authority(monkeypatch):
    p,_=provider(monkeypatch)
    assert p.available()
    quote=p.quote('BTC-USD')
    assert quote and p.authentic(quote,'BTC-USD')
    assert quote['execution_authority']=='PAPER_SIMULATION_ONLY'
    assert quote['provider_quote_verified'] is False
