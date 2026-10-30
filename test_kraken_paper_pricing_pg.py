"""Real local PostgreSQL transaction checks; synthetic fills, never production."""
from datetime import datetime, timezone
from pathlib import Path
import uuid
import pytest
from test_paper_fill_provenance_pg import fill_evidence_db


@pytest.mark.parametrize('side',['BUY','SELL'])
def test_kraken_depth_receipt_and_canonical_fill_commit_or_rollback_together(fill_evidence_db,monkeypatch,side):
    import paper_execution_reality as reality
    import test_kraken_paper_pricing as fixtures
    from paper_execution_accounting import _record_order_and_fill
    from paper_pricing_receipt_audit import pricing_receipt_evidence
    from kraken_paper_pricing import SOURCE, MODEL
    clock=datetime.now(timezone.utc)
    monkeypatch.setattr(fixtures,'NOW',clock)
    p,q,_,_,_=fixtures.install(monkeypatch)
    adjusted,fill=reality._execution_quote(market='crypto',side=side,reference_price=q['price'],quote=q,order_value=150)
    assert p.authentic(adjusted,'BTC-USD')
    connection,reader=fill_evidence_db
    with connection() as conn:
        conn.execute((Path(__file__).parent/'migrations/20261019_coinbase_paper_price_evidence.sql').read_text())

    def write(conn,*,fail=False):
        value=150;price=fill.fill_price;quantity=value/price;fee=value*.006
        lot='kraken-lot:'+uuid.uuid4().hex;ledger='kraken-ledger:'+uuid.uuid4().hex
        at=datetime.now(timezone.utc).isoformat()
        conn.execute("INSERT INTO position_lots VALUES(%s,'crypto','BTC-USD',%s,%s,%s,%s,%s,'PAPER','PAPER')",
            (lot,at,price,fee,quantity,quantity))
        conn.execute("""INSERT INTO trade_ledger VALUES(%s,'crypto','BTC-USD',%s,%s,%s,NULL,%s,NULL,
            0,%s,%s,'PAPER','PAPER','s','d','f',%s)""",(ledger,side,quantity,at,price,-fee,fee,q['quote_id']))
        trade=conn.execute("""INSERT INTO trades(market,symbol,side,quantity,price,value,realized_pnl,created_at)
            VALUES('crypto','BTC-USD',%s,%s,%s,150,0,%s) RETURNING id""",(side,quantity,price,at)).fetchone()['id']
        _record_order_and_fill(conn,execution_trade_id=trade,market='crypto',symbol='BTC-USD',side=side,
            quantity=quantity,price=price,reference_price=q['price'],fee_amount=fee,requested_notional=150,
            lot_links=[(lot,ledger,quantity)],quote=adjusted,reason='disposable Kraken adapter test')
        if fail:raise RuntimeError('rollback after receipt insert')
        return trade
    with connection() as conn:trade=write(conn)
    with reader() as conn:
        record=conn.execute('SELECT * FROM paper_fill_price_evidence').fetchone()
        stored=conn.execute('SELECT * FROM paper_fills').fetchone()
        canonical=conn.execute('SELECT * FROM trades WHERE id=%s',(trade,)).fetchone()
        assert record['model_version']==MODEL and stored['quote_provider']==SOURCE
        assert stored['canonical_trade_id']==trade and stored['fee_amount']==pytest.approx(.9)
        assert canonical['fees']==pytest.approx(.9)
        assert record['evidence']['source_quote']['original_reference']['execution_authority']=='NONE'
        audit=pricing_receipt_evidence(stored,[record],execution_time=canonical['created_at'])
        assert audit['status']=='CONSISTENT_ORIGINAL_PAPER_RECEIPT',audit
        assert len(str(record['evidence']))<32768
    with pytest.raises(RuntimeError,match='rollback after receipt'):
        with connection() as conn:write(conn,fail=True)
    with reader() as conn:
        for table in ('trades','position_lots','trade_ledger','paper_orders','paper_fills','paper_fill_lot_links','paper_fill_price_evidence'):
            assert conn.execute('SELECT count(*) AS n FROM '+table).fetchone()['n']==1
