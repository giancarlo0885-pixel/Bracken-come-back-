"""Capacity backpressure preserves evidence instead of spending unusable requests."""
from contextlib import contextmanager
import json
from unittest.mock import Mock

import pytest
import kraken_brain_evidence as brain
import kraken_primary_research as research
import kraken_reference_runtime as runtime
from test_kraken_primary_research import history, quote


class Connection:
    def __init__(self, highest):
        self.highest = highest
        self.states = []
        self.queries = []

    def execute(self, sql, params=()):
        self.queries.append(sql)
        if 'overflow_present' in sql:
            return Mock(fetchone=lambda: {'overflow_present':False})
        if 'MAX(id)' in sql:
            if isinstance(self.highest, Exception):
                raise self.highest
            return Mock(fetchone=lambda: {'highest_id':self.highest})
        assert 'INSERT INTO kraken_research_state' in sql
        self.states.append(json.loads(params[1]))
        return Mock()

    @contextmanager
    def transaction(self):
        yield self


def setup(monkeypatch, highest, symbols=('BTC-USD','ETH-USD')):
    conn = Connection(highest)
    client = Mock()
    client.history.return_value = history()
    state = {'subscribed_symbols':list(symbols),'quotes':{s:{**quote(),'symbol':s} for s in symbols}}
    monkeypatch.setattr(runtime,'read_kraken_reference_state',lambda:state)
    forecast = Mock(return_value={'status':'ABSTAINED','symbol':'BTC-USD','forecast':None})
    persist = Mock(return_value=1)
    backup = Mock()
    independent = Mock()
    monkeypatch.setattr(research,'research_forecast',forecast)
    monkeypatch.setattr(research,'persist_evidence',persist)
    monkeypatch.setattr(research,'coinbase_history',backup)
    monkeypatch.setattr(research,'coinbase_quote',independent)
    sync = Mock(return_value={'status':'SYNCED'})
    receipt = Mock(return_value={'expected':5000,'matched':5000,'unmatched':0})
    monkeypatch.setattr(brain,'sync_research_sources',sync)
    monkeypatch.setattr(brain,'committed_receipt',receipt)
    return conn,client,research.PrimaryResearch(data=client),forecast,persist,backup,independent,sync,receipt


@pytest.mark.parametrize('highest,reason',[(5000,'EVIDENCE_CAPACITY_REACHED'),
    (None,'EVIDENCE_CAPACITY_UNAVAILABLE'),(5001,'EVIDENCE_CAPACITY_UNAVAILABLE'),
    (RuntimeError('private connection detail'),'EVIDENCE_CAPACITY_UNAVAILABLE')])
def test_blocked_capacity_never_spends_providers_or_fabricates_learning(monkeypatch,highest,reason):
    conn,client,runner,forecast,persist,backup,independent,sync,receipt=setup(monkeypatch,highest)
    runner.cycle(conn)
    client.history.assert_not_called();backup.assert_not_called();independent.assert_not_called()
    forecast.assert_not_called();persist.assert_not_called()
    sync.assert_called_once_with(conn);receipt.assert_called_once_with(conn)
    state=conn.states[-1]
    assert state['status']==reason
    assert all(r['reason']==reason and r['execution_authority']=='NONE' for r in state['symbols'].values())
    assert state['execution_authority']=='NONE' and state['broker_submission']=='NONE'
    assert 'private connection detail' not in json.dumps(state)
    assert all('DELETE' not in sql and 'UPDATE kraken_research_evidence' not in sql for sql in conn.queries)


def test_capacity_read_recovers_without_restart_or_backfill(monkeypatch):
    conn,client,runner,forecast,persist,backup,independent,sync,receipt=setup(monkeypatch,None,symbols=('BTC-USD',))
    runner.cycle(conn)
    assert client.history.call_count==0
    conn.highest=0
    runner.cycle(conn)
    assert client.history.call_count==1 and persist.call_count==1
    assert conn.states[-1]['status']=='ACTIVE'
    assert sync.call_count==2


def test_last_slot_stops_remaining_symbols_in_same_cycle(monkeypatch):
    conn,client,runner,forecast,persist,backup,independent,sync,receipt=setup(monkeypatch,4999)
    persist.return_value=5000
    runner.cycle(conn)
    assert client.history.call_count==1 and persist.call_count==1
    assert conn.states[-1]['symbols']['ETH-USD']['reason']=='EVIDENCE_CAPACITY_REACHED'
    assert conn.states[-1]['status']=='EVIDENCE_CAPACITY_REACHED'


@pytest.mark.parametrize('highest,new_id,status',[(3999,4000,'CAPACITY_WARNING'),(4749,4750,'CAPACITY_CRITICAL')])
def test_capacity_threshold_status_tracks_slot_written_in_this_cycle(monkeypatch,highest,new_id,status):
    conn,client,runner,forecast,persist,backup,independent,sync,receipt=setup(monkeypatch,highest,symbols=('BTC-USD',))
    persist.return_value=new_id
    runner.cycle(conn)
    capacity=conn.states[-1]['evidence_capacity']
    assert capacity['status']==status and capacity['highest_id']==new_id
    assert capacity['remaining_id_slots']==5000-new_id


from test_kraken_reference_pg import pg


@pytest.mark.parametrize('table_present',[True,False])
def test_postgres_capacity_blocks_requests_and_preserves_original_evidence(pg,monkeypatch,table_present):
    """Actual SQL in the existing guarded disposable UUID schema."""
    _,client,runner,forecast,persist,backup,independent,_,_=setup(monkeypatch,None)
    with pg() as conn:
        conn.execute(research.DDL)
        before=[]
        if table_present:
            conn.execute(research.EVIDENCE_DDL)
            conn.execute("INSERT INTO kraken_research_evidence VALUES (5000,%s,NOW(),'{}'::jsonb,%s)",
                         ('capacity-test-fixture',b'preserve-original-bytes'))
            before=conn.execute('SELECT * FROM kraken_research_evidence').fetchall()
        runner.cycle(conn)
        stored=conn.execute('SELECT payload FROM kraken_research_state WHERE id=1').fetchone()['payload']
        assert stored['status']==('EVIDENCE_CAPACITY_REACHED' if table_present else 'EVIDENCE_CAPACITY_UNAVAILABLE')
        assert stored['execution_authority']=='NONE' and stored['broker_submission']=='NONE'
        if table_present:
            assert conn.execute('SELECT * FROM kraken_research_evidence').fetchall()==before
    client.history.assert_not_called();backup.assert_not_called();independent.assert_not_called()
    forecast.assert_not_called();persist.assert_not_called()
