from types import SimpleNamespace
from unittest.mock import Mock,MagicMock
import logging
import pytest
import requests
import runtime_provider_reliability as reliability
import stock_transient_quarantine_repair as transient


@pytest.mark.parametrize('status,classification',[(429,'rate_limited'),(401,'authentication_failed'),
    (403,'access_denied'),(404,'http_not_found'),(503,'outage')])
def test_yahoo_logs_status_without_swallowing_typed_failures_or_exposing_urls(monkeypatch,caplog,status,classification):
    error=requests.HTTPError('https://key:secret@provider.test/history?apikey=secret')
    error.response=SimpleNamespace(status_code=status)
    market_data=Mock();market_data.yf.Ticker.return_value.history.side_effect=error
    monkeypatch.setattr(reliability,'_cooldown_frame',lambda key:None)
    marker=Mock();monkeypatch.setattr(reliability,'_mark_unavailable',marker)
    with caplog.at_level(logging.INFO,logger='runtime-provider-reliability'):
        with pytest.raises(requests.HTTPError) as raised:
            reliability.yahoo_reference_history(market_data,'MSFT','1d','1m')
    assert raised.value is error
    assert f'http_status={status}' in caplog.text
    assert f'failure_class={classification}' in caplog.text
    assert 'coverage=UNVERIFIED' in caplog.text
    assert 'secret' not in caplog.text and 'provider.test' not in caplog.text
    marker.assert_called_once()


def install(monkeypatch):
    import database
    connection=MagicMock();connection.__enter__.return_value.execute.return_value.rowcount=0
    monkeypatch.setattr(database,'connect',lambda:connection)
    worker=SimpleNamespace(_v39_quarantine_symbol=Mock(spec=[]))
    original=worker._v39_quarantine_symbol
    transient.install_stock_transient_quarantine_repair(worker)
    return worker,original


def test_transient_log_throttle_expires_and_preserves_durable_failure_handoff(monkeypatch,caplog):
    worker,original=install(monkeypatch)
    now=[100.0];monkeypatch.setattr(transient.time,'monotonic',lambda:now[0])
    with caplog.at_level(logging.INFO,logger='stock-transient-quarantine'):
        for at in (100,101,399,400):
            now[0]=at;worker._v39_quarantine_symbol('MSFT','market_data','empty_fast_history')
    assert caplog.text.count('STOCK TRANSIENT DATA COOLDOWN')==2
    original.assert_not_called()
    worker._v39_quarantine_symbol('MSFT','market_data','identity_mismatch')
    original.assert_called_once_with('MSFT','market_data','identity_mismatch')


def test_log_key_storage_is_bounded_and_does_not_mix_failure_identities(monkeypatch,caplog):
    worker,original=install(monkeypatch)
    monkeypatch.setattr(transient,'_MAX_TRANSIENT_LOG_KEYS',2)
    monkeypatch.setattr(transient.time,'monotonic',lambda:100.0)
    with caplog.at_level(logging.INFO,logger='stock-transient-quarantine'):
        for symbol in ('MSFT','MSFT','GOOG','XLK','MSFT'):
            worker._v39_quarantine_symbol(symbol,'market_data','empty_fast_history')
    assert caplog.text.count('STOCK TRANSIENT DATA COOLDOWN')==4
    original.assert_not_called()
