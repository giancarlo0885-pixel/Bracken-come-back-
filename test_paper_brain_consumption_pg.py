"""Synthetic forward decisions on guarded disposable local PostgreSQL schemas."""
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, asdict
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

import paper_brain_consumption as provenance
from test_paper_fill_provenance_pg import fill_evidence_db

MIGRATION=Path(__file__).parent/'migrations/20261020_paper_brain_consumption.sql'


@pytest.fixture
def brain_db(fill_evidence_db,monkeypatch):
    monkeypatch.setenv('EXECUTION_MODE','paper')
    monkeypatch.setenv('ENABLE_BROKER_SUBMISSION','false')
    monkeypatch.setenv('LIVE_TRADING_ARMED','false')
    connection,_=fill_evidence_db
    with connection() as conn:
        conn.execute('CREATE TABLE signals(id BIGINT PRIMARY KEY,market TEXT,symbol TEXT,created_at TEXT)')
        conn.execute('''CREATE TABLE forecasts(id BIGSERIAL PRIMARY KEY,signal_id BIGINT,market TEXT,symbol TEXT,
            forecast_id TEXT,model TEXT,model_version TEXT,created_at TEXT,source_quote_timestamp TEXT)''')
        conn.execute("INSERT INTO signals VALUES(1,'crypto','BTC-USD',clock_timestamp()::text)")
        conn.execute("""INSERT INTO forecasts(signal_id,market,symbol,forecast_id,model,model_version,created_at,source_quote_timestamp)
            VALUES(1,'crypto','BTC-USD','f1','model','version',clock_timestamp()::text,
                   (clock_timestamp()-INTERVAL '1 second')::text)""")
        conn.execute(MIGRATION.read_text())
    return connection


def snapshot(connection):
    with connection() as conn:
        return provenance.persist_snapshot(conn,market='crypto',symbol='BTC-USD',
            signal={'signal_id':1,'forecast_id':'f1'},brain={'summary':'original','trade_memory':[]})


def consumption(conn,identity):
    from market_memory import feature_vector
    return provenance.consume_snapshot(conn,identity,
        decision_input={'signal':{'signal_id':1,'forecast_id':'f1'},'parameters':{},'features':feature_vector({})},
        feature_version='test-features-v1',model_version='test-decision-model-v1')


def verify(conn,did='d1'):
    return provenance.verify_decision(conn,did,market='crypto',symbol='BTC-USD',signal_id=1,forecast_id='f1')


def test_original_snapshot_commits_before_consumption_and_decision_receipt_commit_together(brain_db):
    identity=snapshot(brain_db)
    with brain_db() as writer,brain_db() as reader:
        c=consumption(writer,identity)
        assert c['snapshot']['brain']['summary']=='original'
        provenance.persist_decision(writer,c,{'action':'HOLD'},decision_id='d1')
        assert not verify(reader)
        writer.commit()
    with brain_db() as reader:
        assert verify(reader)
        assert not provenance.verify_decision(reader,'d1',market='cash',symbol='BTC-USD',signal_id=1,forecast_id='f1')
        assert not provenance.verify_decision(reader,'d1',market='crypto',symbol='ETH-USD',signal_id=1,forecast_id='f1')


def test_migration_is_repeatable_without_rewriting_original_evidence(brain_db):
    identity=snapshot(brain_db)
    with brain_db() as conn:conn.execute(MIGRATION.read_text())
    with brain_db() as conn:
        assert conn.execute('SELECT COUNT(*) AS n FROM paper_brain_snapshots').fetchone()['n']==1
        assert consumption(conn,identity)['snapshot']['brain']['summary']=='original'


def test_migration_lock_timeout_rolls_back_partial_schema_and_allows_retry(fill_evidence_db):
    """A late FK lock conflict must undo even DDL installed before that FK."""
    import psycopg
    connection, _ = fill_evidence_db
    with connection() as setup:
        setup.execute('CREATE TABLE signals(id BIGINT PRIMARY KEY)')
        setup.execute('CREATE TABLE forecasts(id BIGINT PRIMARY KEY)')
    with connection() as blocker:
        blocker.execute('LOCK TABLE paper_fills IN ACCESS EXCLUSIVE MODE')
        with pytest.raises(psycopg.errors.LockNotAvailable):
            with connection() as installer:
                installer.execute(MIGRATION.read_text())
    with connection() as reader:
        for table in ('paper_brain_snapshots', 'paper_brain_decisions',
                      'paper_brain_consumption_receipts', 'paper_brain_fill_links'):
            assert reader.execute('SELECT to_regclass(%s) AS relation', (table,)).fetchone()['relation'] is None
        assert reader.execute("SELECT to_regprocedure('paper_brain_fill_link_valid()') AS fn").fetchone()['fn'] is None
        assert reader.execute('SELECT count(*) AS n FROM paper_fills').fetchone()['n'] == 0
        before = reader.execute('SHOW lock_timeout').fetchone()['lock_timeout']
        reader.execute(MIGRATION.read_text())
        assert reader.execute('SHOW lock_timeout').fetchone()['lock_timeout'] == '5s'
        reader.commit()
        assert reader.execute('SHOW lock_timeout').fetchone()['lock_timeout'] == before
        assert reader.execute("SELECT to_regclass('paper_brain_fill_links') AS relation").fetchone()['relation']


@pytest.mark.parametrize('runner', ['legacy', 'installed_runtime_wrapper'])
def test_runner_bounds_advisory_lock_before_schema_repair(fill_evidence_db, monkeypatch, runner):
    import psycopg
    import migrations
    connection, _ = fill_evidence_db
    repaired = []
    monkeypatch.setattr(migrations, 'connect', connection)
    monkeypatch.setattr(migrations, '_repair_database', lambda conn: repaired.append(True))
    if runner == 'installed_runtime_wrapper':
        import capital_readiness_runtime
        import migration_runtime_safety
        # Preserve all installation bindings for the next test/worker fixture.
        monkeypatch.setattr(migration_runtime_safety, '_INSTALLED', False)
        monkeypatch.setattr(migration_runtime_safety, '_ORIGINAL', None)
        monkeypatch.setattr(migrations, 'run_migrations', migrations.run_migrations)
        monkeypatch.setattr(capital_readiness_runtime, 'run_migrations', capital_readiness_runtime.run_migrations)
        migration_runtime_safety.install_migration_runtime_safety()
        assert migrations.run_migrations is migration_runtime_safety.safe_run_migrations
        assert capital_readiness_runtime.run_migrations is migration_runtime_safety.safe_run_migrations
    with connection() as blocker:
        blocker.execute('SELECT pg_advisory_xact_lock(%s)', (migrations.MIGRATION_LOCK_ID,))
        with pytest.raises(psycopg.errors.LockNotAvailable):
            migrations.run_migrations()
    assert repaired == []
    with connection() as reader:
        assert reader.execute('SHOW lock_timeout').fetchone()['lock_timeout'] == '0'


def test_bootstrap_lock_is_bounded_before_initialization(fill_evidence_db, monkeypatch):
    import database
    import psycopg
    connection, _ = fill_evidence_db
    initialized = []
    monkeypatch.setattr(database, 'connect', connection)
    monkeypatch.setattr(database, 'initialize_database', lambda: initialized.append(True))
    with connection() as blocker:
        blocker.execute('SELECT pg_advisory_xact_lock(hashtext(%s))', (database.DATABASE_BOOTSTRAP_LOCK_NAME,))
        with pytest.raises(psycopg.errors.LockNotAvailable):
            database.bootstrap_database_with_lock(lambda: initialized.append(True))
    assert initialized == []


def test_legacy_retention_fk_failure_preserves_evidence_and_new_worker_can_continue(brain_db):
    """Old retention lacks the snapshot exclusion: safe rollback, not seamless compatibility."""
    import database
    import psycopg
    identity = snapshot(brain_db)
    with brain_db() as setup:
        setup.execute('ALTER TABLE position_lots ADD COLUMN entry_forecast_id TEXT')
    # This is the pre-393 eligibility predicate and bounded DELETE shape. It
    # intentionally omits snapshot parents, as an already-running old worker does.
    legacy_eligible = database._entry_evidence_retention_sql('forecasts', brain_snapshots_available=False)
    with pytest.raises(psycopg.errors.ForeignKeyViolation):
        with brain_db() as old_worker:
            old_worker.execute(f'''WITH doomed AS (
                SELECT id FROM forecasts WHERE {legacy_eligible} ORDER BY id LIMIT 1)
                DELETE FROM forecasts WHERE id IN (SELECT id FROM doomed) AND {legacy_eligible}''')
    with brain_db() as new_worker:
        assert new_worker.execute("SELECT count(*) AS n FROM forecasts WHERE forecast_id='f1'").fetchone()['n'] == 1
        provenance.persist_decision(new_worker, consumption(new_worker, identity), {'action':'HOLD'}, decision_id='after-old-retention')
    with brain_db() as reader:
        assert verify(reader, 'after-old-retention')


@pytest.mark.parametrize('reuse_snapshot', [True, False])
def test_storage_measurement_is_read_only_and_reports_synthetic_workload(brain_db, reuse_snapshot):
    import json
    with brain_db() as conn:
        with pytest.raises(RuntimeError, match='read-only'):
            provenance.storage_diagnostics(conn)
    def measure():
        with brain_db() as reader:
            reader.execute('SET TRANSACTION READ ONLY')
            return provenance.storage_diagnostics(reader)
    before = measure()
    for i in range(100):
        with brain_db() as writer:
            identity = provenance.persist_snapshot(writer, market='crypto', symbol='BTC-USD',
                signal={'signal_id':1, 'forecast_id':'f1'},
                brain={'summary':'synthetic storage fixture', 'revision':0 if reuse_snapshot else i})
        with brain_db() as writer:
            provenance.persist_decision(writer, consumption(writer, identity),
                {'action':'HOLD'}, decision_id=f'storage-{i}')
    report = measure()
    assert report['tables']['paper_brain_decisions']['sampled_rows'] == 100
    assert report['tables']['paper_brain_snapshots']['sampled_rows'] == (1 if reuse_snapshot else 100)
    delta = report['total_relation_bytes'] - before['total_relation_bytes']
    assert delta > 0
    print('SYNTHETIC_STORAGE ' + json.dumps({'decisions':100, 'reuse_snapshot':reuse_snapshot,
        'incremental_relation_bytes':delta, 'incremental_bytes_per_decision':delta/100,
        'report':report}, sort_keys=True))


def test_identical_snapshots_are_reused_without_rewriting_timestamps(brain_db):
    identity=snapshot(brain_db)
    with brain_db() as conn:before=conn.execute('SELECT * FROM paper_brain_snapshots').fetchone()
    assert snapshot(brain_db)==identity
    with brain_db() as conn:
        assert conn.execute('SELECT * FROM paper_brain_snapshots').fetchone()==before
        assert conn.execute('SELECT COUNT(*) AS n FROM paper_brain_snapshots').fetchone()['n']==1


def test_retention_preserves_original_sources_but_still_bounds_unlinked_rows(brain_db):
    import database
    snapshot(brain_db)
    with brain_db() as writer:
        writer.execute('ALTER TABLE position_lots ADD COLUMN entry_signal_id TEXT,ADD COLUMN entry_forecast_id TEXT')
        for i in range(10,20):
            writer.execute("INSERT INTO signals VALUES(%s,'crypto','UNLINKED','2026-01-01T00:00:00Z')",(i,))
            writer.execute('''INSERT INTO forecasts(signal_id,market,symbol,forecast_id,model,model_version,created_at,source_quote_timestamp)
                VALUES(%s,'crypto','UNLINKED',%s,'model','version','2026-01-01T00:00:00Z','2026-01-01T00:00:00Z')''',(i,str(i)))
    with brain_db() as writer:
        assert database._apply_retention_policy(writer,'forecasts',{'keep_rows':2,'batch_size':2},max_batches=10)==8
        assert writer.execute("SELECT COUNT(*) AS n FROM forecasts WHERE forecast_id='f1'").fetchone()['n']==1
        assert writer.execute('SELECT COUNT(*) AS n FROM forecasts').fetchone()['n']==3
        assert database._apply_retention_policy(writer,'signals',{'keep_rows':2,'batch_size':2},max_batches=10)>0
        assert writer.execute('SELECT COUNT(*) AS n FROM signals WHERE id=1').fetchone()['n']==1


def test_decision_and_receipt_roll_back_together(brain_db):
    identity=snapshot(brain_db)
    with brain_db() as conn:
        provenance.persist_decision(conn,consumption(conn,identity),{'action':'HOLD'},decision_id='d1')
        conn.rollback()
    with brain_db() as reader:
        assert not verify(reader)
        assert reader.execute('SELECT COUNT(*) AS n FROM paper_brain_decisions').fetchone()['n']==0
        assert reader.execute('SELECT COUNT(*) AS n FROM paper_brain_consumption_receipts').fetchone()['n']==0


@pytest.mark.parametrize('field,value',[('brain_snapshot_hash','forged'),('decision_input_hash','forged'),
    ('feature_version','another-version'),('model_version','another-model')])
def test_conflicting_receipt_rolls_back_decision(brain_db,field,value):
    import psycopg
    identity=snapshot(brain_db)
    with pytest.raises(psycopg.Error):
        with brain_db() as writer:
            c=consumption(writer,identity);c[field]=value
            provenance.persist_decision(writer,c,{'action':'HOLD'},decision_id='d1')
    with brain_db() as reader:assert not verify(reader)


def test_missing_snapshot_and_future_evidence_are_rejected(brain_db):
    import psycopg
    with brain_db() as conn:
        with pytest.raises(ValueError,match='missing'):consumption(conn,'not-present')
    identity=snapshot(brain_db)
    with pytest.raises(psycopg.Error):
        with brain_db() as conn:
            original=conn.execute('SELECT * FROM paper_brain_snapshots WHERE brain_snapshot_id=%s',(identity,)).fetchone()
            conn.execute('''INSERT INTO paper_brain_snapshots
                (brain_snapshot_id,brain_snapshot_hash,canonical_snapshot,source_forecast_row_id,source_signal_id,created_at)
                VALUES('future',%s,%s,1,1,clock_timestamp()+INTERVAL '1 day')''',
                (original['brain_snapshot_hash'],original['canonical_snapshot']))


def test_snapshot_created_in_same_transaction_cannot_claim_prior_commit(brain_db):
    with brain_db() as writer:
        identity=provenance.persist_snapshot(writer,market='crypto',symbol='BTC-USD',
            signal={'signal_id':1,'forecast_id':'f1'},brain={})
        with pytest.raises(ValueError,match='not committed'):consumption(writer,identity)


def test_receipt_cannot_commit_future_consumption_or_orphan_decision(brain_db):
    import psycopg
    identity=snapshot(brain_db)
    with pytest.raises(psycopg.Error):
        with brain_db() as writer:
            c=consumption(writer,identity);c['consumed_at']+=timedelta(days=1)
            provenance.persist_decision(writer,c,{},decision_id='d1')
    with pytest.raises(psycopg.errors.ForeignKeyViolation):
        with brain_db() as writer:
            c=consumption(writer,identity)
            writer.execute('''INSERT INTO paper_brain_decisions
                (decision_id,market,symbol,signal_id,forecast_id,canonical_input,decision_input_hash,canonical_output)
                VALUES('orphan','crypto','BTC-USD','1','f1',%s,%s,'{}')''',(c['canonical_input'],c['decision_input_hash']))


@pytest.mark.parametrize('conflict',['none','late_decision','wrong_lot','changed_features'])
def test_entry_fill_link_requires_original_decision_and_exact_graph(brain_db,monkeypatch,conflict):
    import psycopg
    from test_paper_fill_provenance_pg import _seed_graph
    identity=snapshot(brain_db)
    with brain_db() as writer:provenance.persist_decision(writer,consumption(writer,identity),{},decision_id='d1')
    with brain_db() as writer:
        _seed_graph(writer)
        writer.execute('''ALTER TABLE position_lots ADD COLUMN entry_decision_id TEXT,
            ADD COLUMN entry_forecast_id TEXT, ADD COLUMN entry_signal_id TEXT''')
        for table in ('trades','position_lots','trade_ledger','paper_orders','paper_fills'):
            writer.execute('UPDATE '+table+" SET symbol='BTC-USD'")
        writer.execute("UPDATE position_lots SET entry_decision_id='d1',entry_forecast_id='f1',entry_signal_id='1'")
        writer.execute("UPDATE trade_ledger SET entry_decision_id='d1',entry_forecast_id='f1',entry_signal_id='1'")
        if conflict!='late_decision':
            writer.execute("UPDATE trade_ledger SET entry_time=clock_timestamp()::text WHERE side='BUY'")
            writer.execute("UPDATE position_lots l SET opened_at=t.entry_time FROM trade_ledger t WHERE t.side='BUY' AND t.trade_id='buy:'||l.lot_id")
        # Test fixture writer timestamps are made causal; actual entry linking
        # never updates these canonical records.
        writer.execute("UPDATE paper_fills SET created_at=clock_timestamp()::text")
        execution=writer.execute("SELECT canonical_trade_id FROM paper_fills WHERE side='BUY' ORDER BY canonical_trade_id LIMIT 1").fetchone()['canonical_trade_id']
    signal={'symbol':'BTC-USD','signal_id':1,'forecast_id':'f1','entry_decision_id':'d1','decision_receipt_id':'d1'}
    if conflict=='changed_features':signal['score']=90
    monkeypatch.setattr(provenance,'_INSTALLED',True)
    def link():
        with brain_db() as writer:provenance.link_entry_fill(writer,signal=signal,market='crypto',symbol='BTC-USD',
            lot_id='lot-b' if conflict=='wrong_lot' else 'lot-a',ledger_trade_id='buy:lot-a',execution_trade_id=execution)
    if conflict=='none':link()
    else:
        with pytest.raises((ValueError,psycopg.Error)):link()
    with brain_db() as reader:
        assert reader.execute('SELECT COUNT(*) AS n FROM paper_brain_fill_links').fetchone()['n']==int(conflict=='none')


@pytest.mark.parametrize('change',['missing','duplicate','future','wrong_market'])
def test_forecast_must_be_exact_original_and_causal(brain_db,change):
    with brain_db() as writer:
        if change=='missing':writer.execute("UPDATE forecasts SET forecast_id='unrelated'")
        elif change=='duplicate':writer.execute('''INSERT INTO forecasts(signal_id,market,symbol,forecast_id,model,model_version,created_at,source_quote_timestamp)
            SELECT signal_id,market,symbol,forecast_id,model,model_version,created_at,source_quote_timestamp FROM forecasts''')
        elif change=='future':writer.execute("UPDATE forecasts SET created_at=(clock_timestamp()+INTERVAL '1 day')::text")
        else:writer.execute("UPDATE forecasts SET market='cash'")
    with pytest.raises(ValueError):snapshot(brain_db)


@pytest.mark.parametrize('statement',[
    "UPDATE paper_brain_snapshots SET canonical_snapshot='{}'",
    'DELETE FROM paper_brain_snapshots','TRUNCATE paper_brain_snapshots CASCADE',
    'DELETE FROM paper_brain_decisions','DELETE FROM paper_brain_consumption_receipts'])
def test_append_only_evidence_rejects_tampering(brain_db,statement):
    import psycopg
    snapshot(brain_db)
    with pytest.raises(psycopg.Error):
        with brain_db() as conn:conn.execute(statement)


def test_concurrent_decisions_cannot_overwrite_same_identity(brain_db):
    import psycopg
    identity=snapshot(brain_db)
    def write():
        try:
            with brain_db() as conn:
                provenance.persist_decision(conn,consumption(conn,identity),{'action':'HOLD'},decision_id='d1')
            return 'committed'
        except psycopg.errors.UniqueViolation:return 'conflict'
    with ThreadPoolExecutor(max_workers=2) as pool:assert sorted(pool.map(lambda _:write(),range(2)))==['committed','conflict']
    with brain_db() as conn:
        assert verify(conn)
        assert conn.execute('SELECT COUNT(*) AS n FROM paper_brain_consumption_receipts').fetchone()['n']==1


def test_concurrent_brain_refresh_cannot_replace_already_consumed_projection(brain_db):
    with brain_db() as writer:
        writer.execute('CREATE TABLE oracle_brain_sources(source_key TEXT PRIMARY KEY,metadata JSONB)')
        writer.execute('''INSERT INTO oracle_brain_sources VALUES('source','{"revision":1}')''')
        original=writer.execute('SELECT metadata FROM oracle_brain_sources').fetchone()['metadata']
        identity=provenance.persist_snapshot(writer,market='crypto',symbol='BTC-USD',
            signal={'signal_id':1,'forecast_id':'f1'},brain={'source_projection':original})
    with brain_db() as decision:
        c=consumption(decision,identity)
        def refresh():
            with brain_db() as updater:updater.execute('''UPDATE oracle_brain_sources SET metadata='{"revision":2}' ''')
        with ThreadPoolExecutor(max_workers=1) as pool:pool.submit(refresh).result(timeout=10)
        assert c['snapshot']['brain']['source_projection']=={'revision':1}
        assert decision.execute('SELECT metadata FROM oracle_brain_sources').fetchone()['metadata']=={'revision':2}
        provenance.persist_decision(decision,c,{'action':'HOLD'},decision_id='d1')
    with brain_db() as reader:
        assert verify(reader)
        assert consumption(reader,identity)['snapshot']['brain']['source_projection']=={'revision':1}


@pytest.mark.parametrize('fails',[False,True])
def test_runtime_consumes_committed_frozen_inputs_and_links_original_decision(brain_db,monkeypatch,fails):
    import oracle_intelligence,opportunity_engine,oracle_bot
    @dataclass(frozen=True)
    class Decision:
        action:str='HOLD'
        decision_receipt_id:str|None=None
        def to_dict(self):return asdict(self)
    original_brain={'ranking_adjustment':0}
    def evaluate(signal,*,market='crypto',historical_records=None,use_market_memory=True,_provenance_crypto_history=None):
        with brain_db() as reader:
            assert reader.execute('SELECT COUNT(*) AS n FROM paper_brain_snapshots').fetchone()['n']==1
        original_brain['ranking_adjustment']=99
        assert signal.brain_outcome_context['ranking_adjustment']==0
        assert historical_records==[]
        assert _provenance_crypto_history=={'context_only':True}
        if fails:raise RuntimeError('synthetic decision failure')
        return Decision()
    monkeypatch.setattr(provenance,'_INSTALLED',False)
    for module in (oracle_intelligence,opportunity_engine,oracle_bot):monkeypatch.setattr(module,'evaluate_opportunity',evaluate)
    monkeypatch.setattr(oracle_intelligence,'load_trade_memory',lambda *a:[])
    monkeypatch.setattr(oracle_intelligence,'crypto_history_context',lambda *a,**k:{'context_only':True})
    provenance.install_paper_brain_consumption(connect_factory=brain_db)
    signal=SimpleNamespace(symbol='BTC-USD',signal_id=1,forecast_id='f1',brain_outcome_context=original_brain)
    if fails:
        with pytest.raises(provenance.DecisionEvidenceUnavailable):oracle_intelligence.evaluate_opportunity(signal)
        assert not hasattr(signal,'decision_receipt_id')
    else:
        result=oracle_intelligence.evaluate_opportunity(signal)
        assert signal.entry_decision_id==result.decision_receipt_id
    with brain_db() as reader:
        if not fails:assert verify(reader,result.decision_receipt_id)
        else:assert reader.execute('SELECT COUNT(*) AS n FROM paper_brain_decisions').fetchone()['n']==0
        import json
        stored=json.loads(reader.execute('SELECT canonical_snapshot FROM paper_brain_snapshots').fetchone()['canonical_snapshot'])
        assert stored['brain']['signal_brain_inputs']['brain_outcome_context']['ranking_adjustment']==0


def test_receipt_instrumentation_preserves_actual_decision_result(brain_db,monkeypatch):
    import oracle_intelligence,opportunity_engine,oracle_bot
    original=oracle_intelligence.evaluate_opportunity
    signal={'symbol':'BTC-USD','signal_id':1,'forecast_id':'f1','score':.92,'confidence':.9,
        'momentum_5d':.05,'momentum_20d':.1,'trend_strength':.07,'volume_ratio':1.8,
        'volatility_20d':.22,'atr_pct':.018,'news_sentiment':.7,'relative_strength':.13,
        'spread_pct':.0005,'estimated_slippage_pct':.0004,'event_risk_score':10}
    kwargs={'market':'crypto','historical_records':[],'use_market_memory':False}
    baseline=original(signal,**kwargs).to_dict()
    monkeypatch.setattr(provenance,'_INSTALLED',False)
    for module in (oracle_intelligence,opportunity_engine,oracle_bot):monkeypatch.setattr(module,'evaluate_opportunity',original)
    assert provenance.install_paper_brain_consumption(connect_factory=brain_db)
    instrumented=oracle_intelligence.evaluate_opportunity(signal,**kwargs).to_dict()
    assert instrumented.pop('decision_receipt_id')
    baseline.pop('decision_receipt_id')
    assert instrumented==baseline


@pytest.mark.parametrize('failure',['none','open','corrupt_pricing','missing_cost_policy','changed_forecast'])
def test_full_forward_chain_requires_finalized_costs_and_original_pricing(brain_db,monkeypatch,failure):
    """Production accounting math; deliberately loss-making synthetic round trip."""
    import json
    from copy import deepcopy
    import paper_execution_reality as reality
    from paper_execution_accounting import _record_order_and_fill,_simulate_fill_explicit_fee
    from coinbase_paper_pricing import MODEL,validate_book,depth_price
    from test_coinbase_paper_pricing import book,reference
    from market_memory import feature_vector
    with brain_db() as conn:
        now=conn.execute('SELECT clock_timestamp() AS now').fetchone()['now']
        conn.execute('''ALTER TABLE position_lots ADD COLUMN entry_signal_id TEXT,ADD COLUMN entry_decision_id TEXT,
            ADD COLUMN entry_forecast_id TEXT,ADD COLUMN entry_quote_id TEXT,ADD COLUMN decision_timestamp TEXT,ADD COLUMN feature_snapshot JSONB''')
        conn.execute('ALTER TABLE trade_ledger ADD COLUMN decision_timestamp TEXT,ADD COLUMN feature_snapshot JSONB')
        conn.execute((Path(__file__).parent/'migrations/20261019_coinbase_paper_price_evidence.sql').read_text())
    independent=reference()
    independent.update(quote_timestamp=now.isoformat(),received_at=now.isoformat(),bid='99.99',ask='100.01',price='100')
    q=validate_book('BTC-USD',book(time=now.isoformat(),bids=[['99.99','10',1]],asks=[['100.01','10',1]]),now,independent,now)
    q.update(paper_price_model=MODEL,estimated_slippage_pct=0)
    original=deepcopy(q)
    identity=snapshot(brain_db)
    with brain_db() as conn:decided=provenance.persist_decision(conn,consumption(conn,identity),{'action':'BUY'},decision_id='d1')
    monkeypatch.setattr(reality,'simulate_fill',lambda **kwargs:_simulate_fill_explicit_fee(**{
        **kwargs,'market_impact_pct':0,'slippage_pct':0,'latency_pct':0}))
    features=json.dumps(feature_vector({}))
    with brain_db() as conn:
        entry_time=conn.execute('SELECT clock_timestamp() AS now').fetchone()['now'].isoformat()
        entry_price=100.01;entry_fee=entry_price*.006
        conn.execute('''INSERT INTO position_lots
            (lot_id,market,symbol,opened_at,entry_price,entry_fees,quantity_opened,quantity_remaining,
             broker_mode,account_environment,entry_signal_id,entry_decision_id,entry_forecast_id,entry_quote_id,decision_timestamp,feature_snapshot)
            VALUES('lot','crypto','BTC-USD',%s,%s,%s,1,%s,'PAPER','PAPER','1','d1','f1',%s,%s,%s::jsonb)''',
            (entry_time,entry_price,entry_fee,int(failure=='open'),q['quote_id'],decided.isoformat(),features))
        for side,trade_id,price in [('BUY','buy',entry_price)]+([] if failure=='open' else [('SELL','sell',99.99)]):
            event=entry_time if side=='BUY' else conn.execute('SELECT clock_timestamp() AS now').fetchone()['now'].isoformat()
            fee=price*.006;gross=0 if side=='BUY' else price-entry_price
            conn.execute('''INSERT INTO trade_ledger
                (trade_id,market,symbol,side,quantity,entry_time,exit_time,entry_price,exit_price,gross_pnl,net_pnl,fees,
                 broker_mode,account_environment,entry_signal_id,entry_decision_id,entry_forecast_id,entry_quote_id,decision_timestamp,feature_snapshot)
                VALUES(%s,'crypto','BTC-USD',%s,1,%s,%s,%s,%s,%s,%s,%s,'PAPER','PAPER','1','d1','f1',%s,%s,%s::jsonb)''',
                (trade_id,side,entry_time,event if side=='SELL' else None,entry_price,price if side=='SELL' else None,
                 gross,gross-fee,fee,q['quote_id'],decided.isoformat(),features))
            tid=conn.execute('''INSERT INTO trades(market,symbol,side,quantity,price,value,realized_pnl,created_at)
                VALUES('crypto','BTC-USD',%s,1,%s,%s,%s,%s) RETURNING id''',(side,price,price,gross,event)).fetchone()['id']
            evidence=deepcopy(q)
            order_value=100.01 if side=='BUY' else 100
            evidence.update(paper_original_price_quote=original,paper_depth_vwap=float(depth_price(q,side,order_value)),paper_depth_order_value=order_value)
            _record_order_and_fill(conn,execution_trade_id=tid,market='crypto',symbol='BTC-USD',side=side,
                quantity=1,price=price,reference_price=100,fee_amount=fee,requested_notional=price,
                lot_links=[('lot',trade_id,1)],quote=evidence,reason='synthetic forward chain test')
            if side=='BUY':
                fill_id=conn.execute('SELECT fill_id FROM paper_fills WHERE canonical_trade_id=%s',(tid,)).fetchone()['fill_id']
                conn.execute("INSERT INTO paper_brain_fill_links VALUES(%s,'lot','buy','d1')",(fill_id,))
        if failure=='corrupt_pricing':conn.execute("UPDATE paper_fill_price_evidence SET quote_id='corrupt-test-only'")
        if failure=='missing_cost_policy':conn.execute("UPDATE paper_fill_price_evidence SET evidence=evidence #- '{execution,max_adverse_fill_pct}'")
        if failure=='changed_forecast':conn.execute("UPDATE forecasts SET model_version='changed-after-consumption'")
    with brain_db() as reader:
        reader.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY')
        result=provenance.audit_forward_trade(reader,entry_trade_id='buy',market='crypto')
        assert result['brain_consumption_verified']==(failure!='changed_forecast')
        assert result['fully_verified']==(failure=='none'),result
        if failure=='none':assert abs(float(result['modeled_paper_net_pnl'])-(-1.22))<1e-9
        assert result['strategy_promotion']=='NOT_EVALUATED'
