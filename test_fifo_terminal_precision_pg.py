"""Original production writer statuses and exact fills in disposable PostgreSQL."""
from datetime import datetime, timedelta, timezone

import pytest

from paper_exit_research import reconcile_fifo
from paper_fee_policy import fee_aware_fifo_close_lots
from paper_fill_provenance import attach_exact_fill_lot_evidence, load_fill_lot_allocations
from paper_execution_accounting import _record_order_and_fill
from profit_attribution import PositionLot
from test_paper_fill_provenance_pg import fill_evidence_db


def seed_original_writer_graph(conn, later_exit):
    conn.execute('ALTER TABLE trade_ledger ADD COLUMN status TEXT')
    entry = datetime.now(timezone.utc)-timedelta(hours=2)
    lots = [PositionLot('lot:a','TEST-USD','crypto','Paper','fixture',entry,.3,.3,100,.03,
                       entry_signal_id='signal:a',entry_decision_id='decision:a',
                       entry_forecast_id='forecast:a',entry_quote_id='quote:a'),
            PositionLot('lot:b','TEST-USD','crypto','Paper','fixture',entry+timedelta(minutes=1),1,1,101,.01,
                       entry_signal_id='signal:b',entry_decision_id='decision:b',
                       entry_forecast_id='forecast:b',entry_quote_id='quote:b')]

    def persist(lot, identity, side, quantity, price, fee, event, status, gross):
        conn.execute('''INSERT INTO trade_ledger(
            trade_id,market,symbol,side,quantity,entry_time,exit_time,entry_price,exit_price,
            gross_pnl,net_pnl,fees,broker_mode,account_environment,entry_signal_id,
            entry_decision_id,entry_forecast_id,entry_quote_id,status)
            VALUES(%s,'crypto','TEST-USD',%s,%s,%s,%s,%s,%s,%s,%s,%s,'PAPER','PAPER',%s,%s,%s,%s,%s)''',
            (identity,side,quantity,lot.opened_at.isoformat(),event.isoformat() if side=='SELL' else None,
             lot.entry_price,price if side=='SELL' else None,gross,gross-fee,fee,
             lot.entry_signal_id,lot.entry_decision_id,lot.entry_forecast_id,lot.entry_quote_id,status))
        execution = conn.execute('''INSERT INTO trades(
            market,symbol,side,quantity,price,value,realized_pnl,created_at)
            VALUES('crypto','TEST-USD',%s,%s,%s,%s,%s,%s) RETURNING id''',
            (side,quantity,price,quantity*price,gross,event.isoformat())).fetchone()['id']
        _record_order_and_fill(conn,execution_trade_id=execution,market='crypto',symbol='TEST-USD',
            side=side,quantity=quantity,price=price,reference_price=price,fee_amount=fee,
            requested_notional=quantity*price,lot_links=[(lot.lot_id,identity,quantity)],
            quote={'provider':'synthetic original graph'},reason='disposable exact graph')

    for lot in lots:
        conn.execute('''INSERT INTO position_lots VALUES(
            %s,'crypto','TEST-USD',%s,%s,%s,%s,%s,'PAPER','PAPER')''',
            (lot.lot_id,lot.opened_at.isoformat(),lot.entry_price,lot.entry_fees,
             lot.quantity_opened,lot.quantity_remaining))
        persist(lot,'buy:'+lot.lot_id,'BUY',lot.quantity_opened,lot.entry_price,
                lot.entry_fees,lot.opened_at,'OPEN',0)
    closes = []
    for lot, quantity, price, fee, minute in ((lots[0],.2,110,.002,20),
            (lots[0],.3-.2,111,.001,21),(lots[1],1,later_exit,.01,30)):
        row = fee_aware_fifo_close_lots([lot],quantity=quantity,exit_price=price,
                                      exit_time=entry+timedelta(minutes=minute),fees=fee)[0]
        closes.append(row)
        persist(lot,row.trade_id,'SELL',row.quantity,row.exit_price,row.fees,
                row.exit_time,row.status,row.gross_pnl)
        conn.execute('UPDATE position_lots SET quantity_remaining=%s WHERE lot_id=%s',
                     (lot.quantity_remaining,lot.lot_id))
    assert [row.status for row in closes] == ['PARTIAL','CLOSED','CLOSED']
    assert closes[1].quantity == .09999999999999998
    return closes


@pytest.mark.parametrize('later_exit,later_net', [(102,.98),(100,-1.02),(101.02,0)])
def test_original_exact_writer_terminal_residual_preserves_later_winners_and_losses(
        fill_evidence_db,later_exit,later_net):
    connect, reader = fill_evidence_db
    with connect() as conn:
        closes = seed_original_writer_graph(conn,later_exit)
    with reader() as conn:
        tables = ('trade_ledger','trades','position_lots','paper_orders','paper_fills','paper_fill_lot_links')
        originals = {t:conn.execute('SELECT * FROM '+t+' ORDER BY 1').fetchall() for t in tables}
        ledger = [dict(r) for r in originals['trade_ledger']]
        evidence = load_fill_lot_allocations(conn,market='crypto')
        backed = attach_exact_fill_lot_evidence(ledger,evidence)
        assert all(row.get('_fill_accounting_provenance') == 'exact_fill_lot_v1' for row in backed)
        report = reconcile_fifo(backed,require_fill_fee_evidence=True)
        assert report['qualified_fifo_closes'] == 3
        assert report['net_pnl'] == pytest.approx(3.067+later_net)
        assert report['diagnostics'].get('normalized_terminal_lot_residual') == 1
        final = next(row for row in report['closes'] if row['trade_id']==closes[-1].trade_id)
        assert final['net_pnl'] == pytest.approx(later_net,abs=1e-12)
        assert final['entry_fees'] == pytest.approx(.01)
        assert len(final['members']) == 1
        assert final['members'][0]['buy_trade_id'] == 'buy:lot:b'
        assert final['members'][0]['quantity'] == 1
        assert originals == {t:conn.execute('SELECT * FROM '+t+' ORDER BY 1').fetchall() for t in tables}
