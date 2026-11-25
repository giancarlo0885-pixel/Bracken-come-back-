"""Installed paper pricing must not wait for diagnostic PostgreSQL commits."""
from contextlib import contextmanager
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import Mock
import threading

import pytest

import coinbase_paper_pricing as pricing
import database
from test_coinbase_paper_pricing import provider as coinbase_provider
from test_kraken_paper_pricing import provider as kraken_provider
from test_kraken_reference_book import NOW


def install(monkeypatch, provider):
    import oracle_bot, market_data, market_worker, paper_execution_reality
    # Capture all global bindings changed by the actual installer for teardown.
    for module, names in ((oracle_bot, ('_execution_quote_guard', '_verified_quote_for', 'simulate_fill')),
            (market_data, ('get_live_snapshot',)),
            (paper_execution_reality, ('simulate_fill', 'paper_fill_for_accounting'))):
        for name in names:
            monkeypatch.setattr(module, name, getattr(module, name, None), raising=False)
    worker = SimpleNamespace(get_history=Mock(), _execution_quote_payload_from_history=Mock(),
        _v39_signal_opportunity=Mock(return_value={'qualified_for_capital': True}),
        _attach_execution_metadata=market_worker._attach_execution_metadata,
        get_many_snapshots=Mock(), _quote_payload_from_history=Mock(return_value={}),
        stop_event=threading.Event())
    assert pricing.install_paper_pricing(worker, provider)
    return worker


@pytest.mark.parametrize('venue', ('kraken', 'coinbase'))
def test_installed_quote_returns_before_blocked_diagnostic_commit(monkeypatch, venue):
    if venue == 'kraken':
        provider, state = kraken_provider(monkeypatch)
    else:
        provider, state = coinbase_provider(monkeypatch), None
    provider.publish = True
    entered, release, returned = threading.Event(), threading.Event(), threading.Event()
    writes, result, errors = [], [], []

    class Connection:
        def execute(self, statement, params):
            assert 'coinbase_paper_price_state' in statement
            writes.append(params[0])

    @contextmanager
    def blocked_commit():
        yield Connection()
        entered.set()
        assert release.wait(5), 'test must release diagnostic commit'

    monkeypatch.setattr(database, 'connect', blocked_commit)
    worker = install(monkeypatch, provider)

    def request():
        try:
            result.append(provider.quote('BTC-USD'))
        except Exception as exc:
            errors.append(exc)
        finally:
            returned.set()

    thread = threading.Thread(target=request)
    thread.start()
    try:
        assert entered.wait(2)
        assert returned.wait(1), 'diagnostic commit must not block quote handoff'
        assert not errors
        quote = result[0]
        assert quote and provider.authentic(quote, 'BTC-USD')
        assert quote['quote_timestamp'] == NOW.isoformat()
        assert quote['execution_authority'] == 'PAPER_SIMULATION_ONLY'
        assert quote['provider_quote_verified'] is False
        # Diagnostics cannot turn an expired or disconnected receipt into a fill.
        provider.clock = lambda: NOW + timedelta(seconds=6)
        assert not provider.authentic(quote, 'BTC-USD')
        provider.clock = lambda: NOW
        if state is not None:
            state['connected'] = False
            assert not provider.authentic(quote, 'BTC-USD')
            assert state['quotes']['BTC-USD']['execution_authority'] == 'NONE'
        assert len(writes) == 1
    finally:
        worker.stop_event.set()
        release.set()
        thread.join(timeout=2)
        publisher = getattr(provider, '_health_publisher', None)
        if publisher is not None:
            publisher.stop()
    assert not thread.is_alive()
