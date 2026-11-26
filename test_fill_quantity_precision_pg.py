"""Synthetic original graphs, written atomically into disposable PostgreSQL only."""
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import math

import pytest

from paper_execution_accounting import _record_order_and_fill
from paper_fill_provenance import PROVENANCE_VERSION, attach_exact_fill_lot_evidence, load_fill_lot_allocations
from test_paper_fill_provenance_pg import fill_evidence_db


def seed_writer_graph(conn, exit_price):
    """The actual writer accepts the one-ULP allocation without any later repair."""
    opened = datetime.now(timezone.utc) - timedelta(hours=2)
    exited = opened + timedelta(hours=1)
    allocated = math.nextafter(2.0, math.inf)
    conn.execute('''INSERT INTO position_lots VALUES(
        'precision-lot','crypto','TEST-USD',%s,100,.3,3,%s,'PAPER','PAPER')''',
        (opened.isoformat(), 3-allocated))

    def write(side, ledger_id, ledger_quantity, fill_quantity, price, fee, at):
        ledger_gross = 0 if side == 'BUY' else ledger_quantity*(price-100)
        conn.execute('''INSERT INTO trade_ledger VALUES(
            %s,'crypto','TEST-USD',%s,%s,%s,%s,100,%s,%s,%s,%s,'PAPER','PAPER',
            'original-signal','original-decision','original-forecast','original-quote')''',
            (ledger_id, side, ledger_quantity, opened.isoformat(),
             at.isoformat() if side == 'SELL' else None,
             price if side == 'SELL' else None, ledger_gross, ledger_gross-fee, fee))
        trade = conn.execute('''INSERT INTO trades(market,symbol,side,quantity,price,value,realized_pnl,created_at)
            VALUES('crypto','TEST-USD',%s,%s,%s,%s,%s,%s) RETURNING id''',
            (side, fill_quantity, price, fill_quantity*price,
             0 if side == 'BUY' else fill_quantity*(price-100), at.isoformat())).fetchone()['id']
        _record_order_and_fill(conn, execution_trade_id=trade, market='crypto', symbol='TEST-USD',
            side=side, quantity=fill_quantity, price=price, reference_price=price,
            fee_amount=fee, requested_notional=fill_quantity*price,
            lot_links=[('precision-lot',ledger_id,ledger_quantity)], quote={'provider':'synthetic'},
            reason='precision fixture; not market profitability evidence')

    write('BUY','precision-buy',3,3,100,.3,opened)
    write('SELL','precision-sell',allocated,2,exit_price,.2,exited)


@pytest.mark.parametrize('exit_price,expected_net', [(103, 5.6), (99, -2.4)])
def test_writer_accepted_quantity_roundoff_verifies_original_gain_and_loss_graphs(
        fill_evidence_db, exit_price, expected_net):
    connect, reader = fill_evidence_db
    with connect() as writer:
        seed_writer_graph(writer, exit_price)
    with reader() as conn:
        ledger = conn.execute('SELECT * FROM trade_ledger ORDER BY trade_id').fetchall()
        originals = {table: conn.execute('SELECT * FROM '+table).fetchall()
                     for table in ('trades','position_lots','trade_ledger','paper_orders','paper_fills','paper_fill_lot_links')}
        allocations = load_fill_lot_allocations(conn, market='crypto')
        rows = attach_exact_fill_lot_evidence(ledger, allocations)
        assert all(row.get('_fill_accounting_provenance') == PROVENANCE_VERSION for row in rows)
        sell = next(row for row in rows if row['side'] == 'SELL')
        assert sell['quantity'] == math.nextafter(2.0, math.inf)
        entry_fee = Decimal(sell['_canonical_entry_fill_fee']) * Decimal(str(sell['quantity'])) / Decimal(sell['_canonical_entry_quantity'])
        net = Decimal(str(sell['gross_pnl'])) - Decimal(sell['_canonical_fill_fee']) - entry_fee
        assert float(net) == pytest.approx(expected_net)
        assert float(entry_fee) == pytest.approx(.2)
        assert Decimal(sell['_canonical_fill_fee']) == pytest.approx(Decimal('.2'))
        assert all(conn.execute('SELECT * FROM '+table).fetchall() == original
                   for table, original in originals.items())
