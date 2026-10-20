"""Display regressions; synthetic inputs never authorize or persist a fill."""
import ast
from contextlib import nullcontext
from datetime import datetime, timezone
import html
from pathlib import Path

import pytest

from dashboard_helpers import as_float, format_asset_price, live_data_status, plain_reason

NOW = datetime(2026, 10, 20, 4, 0, tzinfo=timezone.utc)
READY = {'symbol':'BTC-USD', 'market':'crypto', 'trade_eligible':True,
         'quote_verified':True, 'quote_age_seconds':1}


@pytest.mark.parametrize('estimate', ['-0.25%', '-.01%', '-1e-2%'])
def test_negative_modeled_ev_is_a_caution_despite_high_execution_score(estimate):
    supports, cautions = plain_reason(f'net EV {estimate}, execution 89', record=READY, now=NOW)
    assert supports == []
    assert any('negative' in message for message in cautions)
    assert not any('acceptable' in message or 'greater than' in message for message in supports+cautions)


@pytest.mark.parametrize('estimate', ['0%', '-0.00%', '+0%'])
def test_zero_ev_does_not_become_a_profitable_estimate(estimate):
    supports, cautions = plain_reason('net EV '+estimate, record=READY, now=NOW)
    assert not supports
    assert any('is zero' in message for message in cautions)


@pytest.mark.parametrize('raw', ['', 'execution 99', 'net EV unknown', 'net EV NaN%',
    'net EV inf%', 'net EV 2', 'net EV --2%', 'net EV 2%, net EV -1%',
    'net EV 2%, net EV unknown'])
def test_missing_malformed_or_conflicting_ev_stays_unavailable(raw):
    supports, cautions = plain_reason(raw, record=READY, now=NOW)
    assert not supports
    assert any('estimate is unavailable' in message for message in cautions)
    assert not any('models currently support' in message for message in supports+cautions)


def test_positive_modeled_ev_is_labeled_as_estimate_not_realized_profit():
    supports, cautions = plain_reason('net EV +0.25%, execution 89', record=READY, now=NOW)
    assert supports == ['Recorded modeled net EV: +0.25%; this is an estimate, not realized profit.']
    assert cautions == []


@pytest.mark.parametrize('veto', [{'trade_eligible':False}, {'quote_verified':False},
    {'stale':True}, {'reference_only':True}, {'execution_quote_eligible':False},
    {'bid':101, 'ask':100}, {'quote_age_seconds':999999},
    {'quote_timestamp':'2026-10-21T04:00:00Z', 'interval':'1m'}])
def test_stale_research_crossed_and_ineligible_quotes_cannot_gain_support_from_old_scores(veto):
    record = {**READY, **veto}
    before = dict(record)
    supports, cautions = plain_reason('net EV +1.0%, execution 99', record=record, now=NOW)
    assert supports == []
    assert 'Current trade eligibility is not established' in cautions[0]
    assert 'Recorded modeled net EV' in cautions[1]
    assert record == before


def test_no_record_never_defaults_to_trade_eligibility():
    supports, cautions = plain_reason('net EV 2%, execution 89', now=NOW)
    assert supports == []
    assert 'not established' in cautions[0]


def test_historical_setup_is_labeled_recorded_and_retains_negative_history():
    supports, cautions = plain_reason('momentum breakout; net EV -.2%; no completed historical analogs',
                                     record={**READY, 'stale':True}, now=NOW)
    assert supports == ['Recorded setup: momentum breakout.']
    assert any('negative' in message for message in cautions)
    assert any('not enough completed historical evidence' in message for message in cautions)


class Display:
    def __init__(self):
        self.text = []
        self.warnings = []
        self.successes = []
    def __enter__(self):return self
    def __exit__(self, *args):return False
    def container(self, **_):return nullcontext()
    def columns(self, _):return self, self
    def markdown(self, text, **_):self.text.append(text)
    def caption(self, text):self.text.append(text)
    def metric(self, *args, **kwargs):pass
    def warning(self, text):self.warnings.append(text)
    def success(self, text):self.successes.append(text)


def test_actual_decision_card_keeps_buy_blocked_and_does_not_print_false_profit_claims():
    # Execute the real renderer without executing app bootstrap, SQL or providers.
    source = ast.parse(Path('app.py').read_text())
    function = next(node for node in source.body if isinstance(node, ast.FunctionDef) and node.name=='decision_card')
    display = Display()
    namespace = {'Any':object, 'st':display, 'html':html, 'as_float':as_float,
                 'plain_reason':plain_reason, 'live_data_status':live_data_status,
                 'format_asset_price':format_asset_price}
    exec(compile(ast.Module(body=[function], type_ignores=[]), 'app.py', 'exec'), namespace)
    namespace['decision_card']({**READY, 'stale':True, 'action':'BUY',
        'reason':'net EV -0.25%, execution 89', 'price':100, 'target':102})
    assert any('WATCH' in text for text in display.text)
    assert any('Not trade-ready' in text for text in display.warnings)
    assert any('net EV is negative' in text for text in display.text)
    assert not display.successes
    assert not any('conditions are acceptable' in text or 'reward remains greater' in text for text in display.text)


@pytest.mark.parametrize('page_name,module_name,builder_name,renderer_name', [
    ('Oracle Brain','oracle_brain','build_oracle_brain_snapshot','render_oracle_brain_component'),
    ('Oracle City','oracle_city_model','build_oracle_city_snapshot','render_oracle_city_component'),
])
def test_visualization_shows_loading_before_collecting_evidence(monkeypatch,page_name,module_name,builder_name,renderer_name):
    from contextlib import contextmanager
    import importlib
    events = []
    class Finished(Exception):pass
    class LoadingDisplay:
        def subheader(self, text):pass
        @contextmanager
        def spinner(self, text):
            events.append(('loading', text))
            yield
            events.append(('loaded', text))
        def iframe(self, *args, **kwargs):events.append(('rendered', None))
        def stop(self):raise Finished()
    def builder(fetch):
        assert events and events[-1][0] == 'loading'
        events.append(('query', None))
        return {'warnings':[]}
    component_module = 'oracle_brain_component' if page_name=='Oracle Brain' else 'oracle_city_component'
    monkeypatch.setattr(importlib.import_module(module_name),builder_name,builder)
    monkeypatch.setattr(importlib.import_module(component_module),renderer_name,lambda data:'<html>fixture</html>')
    source = ast.parse(Path('app.py').read_text())
    block = next(node for node in source.body if isinstance(node,ast.If)
                 and isinstance(node.test,ast.Compare)
                 and any(isinstance(value,ast.Constant) and value.value==page_name for value in node.test.comparators))
    with pytest.raises(Finished):
        exec(compile(ast.Module(body=[block],type_ignores=[]),'app.py','exec'),
             {'page':page_name,'st':LoadingDisplay(),'rows':lambda *args:[]})
    assert [event[0] for event in events] == ['loading','query','loaded','rendered']
