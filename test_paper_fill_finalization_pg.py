"""A valid simulated fill is immutable evidence; database latency is not a new fill."""
from datetime import datetime, timezone, timedelta
from pathlib import Path
import uuid

import pytest
from test_paper_fill_provenance_pg import fill_evidence_db


def setup_fill(monkeypatch, *, side='BUY', notional=150):
    import test_kraken_paper_pricing as fixtures
    import paper_execution_reality as reality
    clock = datetime.now(timezone.utc)
    monkeypatch.setattr(fixtures, 'NOW', clock)
    provider, quote, _, _, _ = fixtures.install(monkeypatch)
    adjusted, fill = reality._execution_quote(market='crypto', side=side,
        reference_price=quote['price'], quote=quote, order_value=notional)
    assert provider.authentic(adjusted, 'BTC-USD')
    return provider, adjusted, fill, clock


def record(conn, quote, fill, *, notional=150, fail=False):
    from paper_execution_accounting import _record_order_and_fill
    side=quote['paper_depth_side'];quantity=notional/fill.fill_price
    lot='finalization-lot:'+uuid.uuid4().hex; ledger='finalization-ledger:'+uuid.uuid4().hex
    at=datetime.now(timezone.utc).isoformat()
    conn.execute("INSERT INTO position_lots VALUES(%s,'crypto','BTC-USD',%s,%s,%s,%s,%s,'PAPER','PAPER')",
        (lot,at,fill.fill_price,notional*.006,quantity,quantity))
    conn.execute("""INSERT INTO trade_ledger VALUES(%s,'crypto','BTC-USD',%s,%s,%s,NULL,%s,NULL,
        0,%s,%s,'PAPER','PAPER','s','d','f',%s)""",
        (ledger,side,quantity,at,fill.fill_price,-notional*.006,notional*.006,quote['quote_id']))
    trade=conn.execute("""INSERT INTO trades(market,symbol,side,quantity,price,value,realized_pnl,created_at)
        VALUES('crypto','BTC-USD',%s,%s,%s,%s,0,%s) RETURNING id""",
        (side,quantity,fill.fill_price,notional,at)).fetchone()['id']
    _record_order_and_fill(conn, execution_trade_id=trade, market='crypto', symbol='BTC-USD',side=side,
        quantity=quantity, price=fill.fill_price, reference_price=fill.reference_price,
        fee_amount=notional*.006, requested_notional=notional,
        lot_links=[(lot,ledger,quantity)], quote=quote,reason='disposable finalization fixture')
    if fail:raise RuntimeError('after receipt insert')
    return trade


def schema(connection):
    with connection() as conn:
        conn.execute((Path(__file__).parent/'migrations/20261019_coinbase_paper_price_evidence.sql').read_text())


@pytest.mark.parametrize('side',['BUY','SELL'])
def test_valid_fill_survives_only_post_fill_database_delay(fill_evidence_db, monkeypatch, side):
    import paper_execution_reality as reality
    provider,quote,fill,clock=setup_fill(monkeypatch,side=side)
    provider.clock=lambda:clock+timedelta(seconds=6)
    assert not provider.authentic(quote,'BTC-USD')
    # A stale packet still cannot authorize another simulation or entry.
    with pytest.raises(ValueError,match='PAPER_PRICE_UNVERIFIED_OR_STALE'):
        reality.simulate_fill(side=side,market='crypto',reference_price=fill.reference_price,
                             quote=quote,order_value=150)
    connection,reader=fill_evidence_db;schema(connection)
    with connection() as conn:trade=record(conn,quote,fill)
    with reader() as conn:
        row=conn.execute('SELECT * FROM paper_fills WHERE canonical_trade_id=%s',(trade,)).fetchone()
        assert row['fill_price']==fill.fill_price
        assert row['fee_amount']==pytest.approx(.9)
        assert row['quote_timestamp']==quote['quote_timestamp']


@pytest.mark.parametrize('tamper',['original','price','order','quantity','fees','missing'])
def test_mutated_or_missing_fill_receipt_rolls_back_every_row(fill_evidence_db,monkeypatch,tamper):
    from copy import deepcopy
    provider,quote,fill,clock=setup_fill(monkeypatch)
    quote=deepcopy(quote)
    provider.clock=lambda:clock+timedelta(seconds=6)
    notional=150
    if tamper=='original':quote['paper_original_price_quote']['checksum']=0
    if tamper=='price':quote['price']+=.1
    if tamper=='order':quote['paper_depth_order_value']=151
    if tamper=='quantity':notional=151
    if tamper=='fees':quote['estimated_fees_pct']=0
    if tamper=='missing':provider.fill_receipts.clear()
    connection,reader=fill_evidence_db;schema(connection)
    with pytest.raises(ValueError,match='PAPER_FILL_RECEIPT'):
        with connection() as conn:record(conn,quote,fill,notional=notional)
    with reader() as conn:
        for table in ('trades','position_lots','trade_ledger','paper_orders','paper_fills','paper_fill_lot_links','paper_fill_price_evidence'):
            assert conn.execute('SELECT count(*) AS n FROM '+table).fetchone()['n']==0


def test_finalized_receipt_still_rolls_back_atomically_on_later_failure(fill_evidence_db,monkeypatch):
    provider,quote,fill,clock=setup_fill(monkeypatch)
    provider.clock=lambda:clock+timedelta(seconds=6)
    connection,reader=fill_evidence_db;schema(connection)
    with pytest.raises(RuntimeError,match='after receipt insert'):
        with connection() as conn:record(conn,quote,fill,fail=True)
    with reader() as conn:
        for table in ('trades','position_lots','trade_ledger','paper_orders','paper_fills','paper_fill_lot_links','paper_fill_price_evidence'):
            assert conn.execute('SELECT count(*) AS n FROM '+table).fetchone()['n']==0


def test_concurrent_fills_keep_their_original_depth_and_fee_receipts(fill_evidence_db,monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    import paper_execution_reality as reality
    provider,quote,first,clock=setup_fill(monkeypatch,notional=150)
    # A second order on the same authenticated book has a different depth VWAP.
    base=quote['paper_original_price_quote']
    with ThreadPoolExecutor(max_workers=2) as executor:
        futures=[executor.submit(reality._execution_quote,market='crypto',side='BUY',
            reference_price=base['price'],quote=base,order_value=value) for value in (50,150)]
        fills=[f.result() for f in futures]
    assert fills[0][1].fill_price!=fills[1][1].fill_price
    provider.clock=lambda:clock+timedelta(seconds=6)
    connection,reader=fill_evidence_db;schema(connection)
    def write(pair,notional):
        with connection() as conn:return record(conn,*pair,notional=notional)
    with ThreadPoolExecutor(max_workers=2) as executor:
        ids=list(executor.map(lambda args:write(*args),zip(fills,(50,150))))
    with reader() as conn:
        records=list(conn.execute('SELECT canonical_trade_id,notional,fee_amount,fill_price FROM paper_fills ORDER BY notional').fetchall())
        assert {r['canonical_trade_id'] for r in records}==set(ids)
        assert [r['notional'] for r in records]==pytest.approx([50,150])
        assert [r['fee_amount'] for r in records]==pytest.approx([.3,.9])
        assert [r['fill_price'] for r in records]==[f[1].fill_price for f in fills]


def test_book_invalidation_cannot_mint_new_fill_or_destroy_original_receipt(fill_evidence_db,monkeypatch):
    import paper_execution_reality as reality
    provider,quote,fill,_=setup_fill(monkeypatch)
    snapshot=provider.reference();snapshot['connected']=False
    provider.reference=lambda:snapshot
    assert not provider.authentic(quote,'BTC-USD')
    with pytest.raises(ValueError,match='PAPER_PRICE_UNVERIFIED_OR_STALE'):
        reality.simulate_fill(side='BUY',market='crypto',reference_price=fill.reference_price,
                             quote=quote,order_value=150)
    connection,reader=fill_evidence_db;schema(connection)
    with connection() as conn:trade=record(conn,quote,fill)
    with reader() as conn:
        assert conn.execute('SELECT count(*) AS n FROM paper_fills WHERE canonical_trade_id=%s',(trade,)).fetchone()['n']==1
