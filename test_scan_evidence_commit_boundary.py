"""Exercise both production scan paths through the downstream handoff."""
from types import SimpleNamespace
from unittest.mock import Mock
import pytest
import market_worker as worker


@pytest.fixture
def scan(monkeypatch):
    signal = SimpleNamespace(symbol='BTC-USD', price=100., score=90., confidence=.9,
                             action='BUY', reason='test', volume_ratio=1.)
    forecast = SimpleNamespace(forecast_id='original-forecast', expected_move_pct=.1)
    monkeypatch.setattr(worker, 'WATCHLISTS', {'crypto': {'BTC-USD': 'Bitcoin'}})
    monkeypatch.setattr(worker, '_fast_candidate_batch', lambda _: [('BTC-USD', 'Bitcoin')])
    monkeypatch.setattr(worker, '_fast_discover_symbol', lambda *_: (signal, object()))
    monkeypatch.setattr(worker, '_discover_symbol', lambda *_: (signal, 'Bitcoin', object()))
    monkeypatch.setattr(worker, '_active_quarantined_symbols', lambda: set())
    monkeypatch.setattr(worker, '_held_symbols', lambda _: set())
    monkeypatch.setattr(worker, 'analyze_market', lambda *_: signal)
    monkeypatch.setattr(worker, 'get_news_sentiment', lambda *a, **k: SimpleNamespace(headlines=[], sentiment=0, source='test'))
    monkeypatch.setattr(worker, 'deliberate', lambda *_: dict(score=90., action='BUY', confidence=.9, explanation='test'))
    monkeypatch.setattr(worker, '_attach_execution_metadata', lambda *_: {})
    monkeypatch.setattr(worker, '_execution_quote_payload_from_history', lambda *a, **k: {'price': 100.})
    monkeypatch.setattr(worker, 'forecast_price', lambda *a, **k: forecast)
    monkeypatch.setattr(worker, 'save_json_signal', lambda *a, **k: 42)
    monkeypatch.setattr(worker, 'save_forecast', lambda *a, **k: None)
    monkeypatch.setattr(worker, 'rank_opportunities', lambda *a, **k: [])
    monkeypatch.setattr(worker, '_execution_enabled', lambda market, kind: kind == 'entry')
    handoff = Mock(return_value=[])
    monkeypatch.setattr(worker, '_v39_execute_iterative', handoff)
    monkeypatch.setattr(worker, 'snapshot', lambda *_: None)
    monkeypatch.setattr(worker, '_v39_record_actions', lambda *_: None)
    return signal, forecast, handoff


@pytest.mark.parametrize('name', ['fast_scan_market', 'scan_market'])
@pytest.mark.parametrize('failure', ['forecast_calculation', 'signal_insert', 'signal_missing_id', 'forecast_commit'])
def test_failed_evidence_never_reaches_trade_candidates(monkeypatch, scan, name, failure):
    def fail(*a, **k):
        raise RuntimeError('simulated insert or commit failure')
    if failure == 'forecast_calculation':
        monkeypatch.setattr(worker, 'forecast_price', fail)
    elif failure == 'signal_missing_id':
        monkeypatch.setattr(worker, 'save_json_signal', lambda *a, **k: None)
    else:
        monkeypatch.setattr(worker, 'save_json_signal' if failure == 'signal_insert' else 'save_forecast', fail)
    getattr(worker, name)('crypto')
    assert scan[2].called
    assert scan[2].call_args.args[1] == []


@pytest.mark.parametrize('name', ['fast_scan_market', 'scan_market'])
def test_handoff_occurs_after_evidence_commit(monkeypatch, scan, name):
    committed = []
    def persist(*args, **kwargs):
        assert kwargs['signal_id'] == 42
        assert not scan[2].called
        committed.append(args[2].forecast_id)
    monkeypatch.setattr(worker, 'save_forecast', persist)
    getattr(worker, name)('crypto')
    assert committed == ['original-forecast']
    assert scan[2].call_args.args[1] == [scan[0]]
    assert scan[0].signal_id == 42 and scan[0].forecast_id == 'original-forecast'


@pytest.mark.parametrize('name', ['fast_scan_market', 'scan_market'])
def test_intentional_abstention_is_persisted_without_inventing_forecast(monkeypatch, scan, name):
    def abstain(*a, **k):
        k['diagnostics']['unavailable_reason'] = 'causal_model_abstention'
        return None
    signal_write = Mock(return_value=42)
    forecast_write = Mock()
    monkeypatch.setattr(worker, 'forecast_price', abstain)
    monkeypatch.setattr(worker, 'save_json_signal', signal_write)
    monkeypatch.setattr(worker, 'save_forecast', forecast_write)
    getattr(worker, name)('crypto')
    assert signal_write.call_args.args[6]['forecast_unavailable_reason'] == 'causal_model_abstention'
    forecast_write.assert_not_called()
    assert scan[2].call_args.args[1] == [scan[0]]
    assert getattr(scan[0], 'forecast_id', None) is None


@pytest.mark.parametrize('name', ['fast_scan_market', 'scan_market'])
def test_failed_candidate_still_allows_position_monitoring(monkeypatch, scan, name):
    def fail(*a, **k):
        raise RuntimeError('commit failure')
    monkeypatch.setattr(worker, 'save_forecast', fail)
    monkeypatch.setattr(worker, '_execution_enabled', lambda *_: True)
    marks, exits = Mock(), Mock(return_value=[])
    monkeypatch.setattr(worker, 'update_prices', marks)
    monkeypatch.setattr(worker, 'risk_exits', exits)
    getattr(worker, name)('crypto')
    assert marks.call_args.args == ('crypto', {'BTC-USD': {'price': 100.}})
    assert exits.call_args.args == marks.call_args.args
    assert scan[2].call_args.args[1] == []
