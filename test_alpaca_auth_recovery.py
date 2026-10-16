from datetime import datetime, timedelta, timezone

import pandas as pd
import pytest

import alpaca_auth_recovery as recovery


@pytest.mark.parametrize('name,value,status', [
    ('APCA_API_KEY_ID', '', 'NOT_CONFIGURED'),
    ('APCA_API_SECRET_KEY', '', 'NOT_CONFIGURED'),
    ('ALPACA_DATA_URL', 'https://paper-api.alpaca.markets', 'CONFIGURATION_BLOCKED'),
    ('ALPACA_DATA_FEED', 'sip', 'CONFIGURATION_BLOCKED'),
    ('EXECUTION_MODE', 'live', 'SAFETY_BLOCKED'),
    ('ENABLE_BROKER_SUBMISSION', 'true', 'SAFETY_BLOCKED'),
    ('LIVE_TRADING_ARMED', 'true', 'SAFETY_BLOCKED'),
])
def test_configuration_and_safety_fail_before_database_or_http(monkeypatch, name, value, status):
    for key, configured in {'APCA_API_KEY_ID': 'unit-key', 'APCA_API_SECRET_KEY': 'unit-secret',
                            'ALPACA_DATA_URL': 'https://data.alpaca.markets', 'ALPACA_DATA_FEED': 'iex',
                            'EXECUTION_MODE': 'paper', 'ENABLE_BROKER_SUBMISSION': 'false', 'LIVE_TRADING_ARMED': 'false'}.items():
        monkeypatch.setenv(key, configured)
    monkeypatch.setenv(name, value)
    result = recovery.recover_alpaca_authentication(
        connect_factory=lambda: pytest.fail('no database change'),
        fetch_history=lambda *a, **k: pytest.fail('no HTTP request'))
    assert result['status'] == status
    assert result['execution_impact'] == 'NONE'


def test_unavailable_database_does_not_probe_or_expose_error(monkeypatch, caplog):
    import provider_capabilities
    monkeypatch.setenv('APCA_API_KEY_ID', 'unit-key')
    monkeypatch.setenv('APCA_API_SECRET_KEY', 'unit-secret')
    monkeypatch.setattr(provider_capabilities, 'capability_available', lambda *a: True)
    def fail():
        raise RuntimeError('unit-secret')
    assert recovery.recover_alpaca_authentication(connect_factory=fail,
        fetch_history=lambda *a, **k: pytest.fail('no HTTP'))['status'] == 'UNAVAILABLE'
    assert 'unit-secret' not in caplog.text


@pytest.mark.parametrize('limit', [0, 6, True, 1.5])
def test_probe_cannot_increase_adapter_page_limit(monkeypatch, limit):
    import alpaca_iex_provider as alpaca
    monkeypatch.setenv('APCA_API_KEY_ID', 'unit-key')
    monkeypatch.setenv('APCA_API_SECRET_KEY', 'unit-secret')
    monkeypatch.setattr(alpaca.requests, 'get', lambda *a, **k: pytest.fail('invalid page limit must precede HTTP'))
    with pytest.raises(alpaca.AlpacaDataError, match='page limit'):
        alpaca.iex_stock_history('MSFT', '5d', '1d', max_pages=limit)


def test_recovery_page_limit_rejects_incomplete_response_after_one_request(monkeypatch):
    import alpaca_iex_provider as alpaca
    from types import SimpleNamespace
    monkeypatch.setenv('APCA_API_KEY_ID', 'unit-key')
    monkeypatch.setenv('APCA_API_SECRET_KEY', 'unit-secret')
    calls = []
    def get(*args, **kwargs):
        calls.append(args)
        return SimpleNamespace(status_code=200, json=lambda: {'bars': [], 'next_page_token': 'more'})
    monkeypatch.setattr(alpaca.requests, 'get', get)
    with pytest.raises(alpaca.AlpacaDataError, match='pagination limit'):
        alpaca.iex_stock_history('MSFT', '5d', '1d', max_pages=1)
    assert len(calls) == 1
