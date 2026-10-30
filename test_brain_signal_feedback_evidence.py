"""Brain ranking input must survive real dataclass signal persistence."""
from copy import deepcopy
import json
import pytest
import market_worker


def Signal():
    from engine import OracleSignal
    return OracleSignal(symbol='BTC-USD',price=100,score=60,confidence=.6,action='BUY',
        momentum_5d=0,momentum_20d=0,rsi_14=50,volatility_20d=.01,trend_strength=.5,
        volume_ratio=1,news_sentiment=0,macd_hist=0,atr_pct=.02,bollinger_position=.5,
        regime='range',reason='synthetic regression')


def signal():
    s=Signal()
    s.brain_outcome_adjustment=-1.25
    s.brain_outcome_context={'status':'ok','samples':40,'scope':'symbol_strategy_regime',
        'polarity':'negative','ranking_adjustment':-1.25,'execution_impact':'BOUNDED_RANKING_ONLY'}
    return s


def test_dataclass_signal_retains_actual_brain_ranking_input_without_claiming_approval():
    s=signal();before=deepcopy(vars(s))
    payload=market_worker._signal_payload(s,{},'fast')
    assert payload['brain_outcome_adjustment']==-1.25
    assert payload['brain_outcome_context']==s.brain_outcome_context
    assert payload['brain_feedback_provenance']['immutable_consumption_proof'] is False
    assert payload['brain_feedback_provenance']['execution_authority']=='NONE'
    assert payload['action']=='BUY' and payload['score']==60
    s.brain_outcome_context['samples']=999
    assert payload['brain_outcome_context']['samples']==40
    assert before['brain_outcome_context']['samples']==40


def test_missing_brain_feedback_is_not_invented():
    payload=market_worker._signal_payload(Signal(),{},'fast')
    assert 'brain_feedback_provenance' not in payload


@pytest.mark.parametrize('value',[float('nan'),{'oversize':'x'*20000}])
def test_invalid_or_oversized_context_is_explicitly_unavailable_without_changing_decision(value):
    s=signal();s.brain_outcome_context=value
    payload=market_worker._signal_payload(s,{},'fast')
    assert payload['brain_outcome_context']['status']=='UNAVAILABLE'
    assert payload['brain_outcome_adjustment']==-1.25 and s.score==60
    json.dumps(payload,allow_nan=False)


def test_adjustment_is_not_recomputed_or_fabricated_during_serialization():
    s=signal();s.brain_outcome_adjustment=float('nan')
    payload=market_worker._signal_payload(s,{},'fast')
    assert payload['brain_outcome_adjustment'] is None
    assert s.brain_outcome_context['ranking_adjustment']==-1.25


def test_pg_original_brain_assessment_commits_with_signal_and_rolls_back(pg,monkeypatch):
    from contextlib import contextmanager
    import database
    with pg() as conn:
        conn.execute('CREATE TABLE signals(id bigserial PRIMARY KEY,market text,symbol text,price float8,score float8,action text,confidence float8,details text,created_at text)')
    fail=[False]
    @contextmanager
    def connection():
        with pg() as conn:
            with conn.transaction():
                yield conn
                if fail[0]:raise RuntimeError('synthetic commit failure')
    monkeypatch.setattr(database,'connect',connection)
    s=signal();payload=market_worker._signal_payload(s,{},'fast')
    def write():return database.save_json_signal('crypto',s.symbol,s.price,s.score,s.action,s.confidence,payload)
    sid=write()
    with pg() as reader:
        row=reader.execute('SELECT * FROM signals WHERE id=%s',(sid,)).fetchone()
        details=json.loads(row['details'])
        assert details['brain_outcome_context']['samples']==40
        assert details['brain_outcome_adjustment']==-1.25
        assert details['brain_feedback_provenance']['immutable_consumption_proof'] is False
        assert row['action']=='BUY' and row['score']==60
    fail[0]=True
    with pytest.raises(RuntimeError,match='commit failure'):write()
    with pg() as reader:
        assert reader.execute('SELECT count(*) AS n FROM signals').fetchone()['n']==1


from test_kraken_reference_pg import pg
