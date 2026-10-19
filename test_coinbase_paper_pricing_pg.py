"""PostgreSQL atomic Coinbase pricing receipt contract.

Only runs on localhost test DB. All fixture trades/receipts roll back.
"""
from datetime import datetime, timedelta, timezone
import os
import uuid

import pytest


@pytest.mark.skipif(not os.getenv("DATABASE_URL"), reason="PostgreSQL CI only")
def test_coinbase_book_commits_with_exact_fill_and_invalid_receipt_rolls_back():
    from psycopg.conninfo import conninfo_to_dict
    settings = conninfo_to_dict(os.environ["DATABASE_URL"])
    if settings.get("host") not in {"localhost", "127.0.0.1", "::1"} or "test" not in settings.get("dbname", "").lower():
        pytest.skip("localhost test database required")

    from database import connect
    from migrations import run_migrations
    from paper_execution_accounting import _record_order_and_fill
    from paper_coinbase_execution import BookRejected, validate_snapshot, price_paper_order

    run_migrations()
    observed = datetime.now(timezone.utc)
    symbol = "BTC-USD"
    book = {"product_id":symbol,"sequence":345,
            "bids":[["99.9","5",2]],"asks":[["100.1","5",2]]}
    ticker = {"product_id":symbol,"bid":"99.9","ask":"100.1","time":observed.isoformat()}
    snapshot = validate_snapshot(
        symbol,book,ticker,requested_at=observed-timedelta(milliseconds=200),
        received_at=observed,now=observed)
    receipt = price_paper_order(snapshot,side="BUY",quantity="2")
    price,fee = float(receipt["fill_price"]),float(receipt["fee_amount"])

    def seed(conn):
        lot_id = "lot:pricing:"+uuid.uuid4().hex
        ledger_id = "ledger:pricing:"+uuid.uuid4().hex
        now = datetime.now(timezone.utc).isoformat()
        trade_id = conn.execute(
            """INSERT INTO trades(market,symbol,side,quantity,price,value,
                realized_pnl,score,reason,created_at)
               VALUES('crypto',%s,'BUY',2,%s,%s,0,NULL,'test',%s) RETURNING id""",
            (symbol,price,price*2,now),
        ).fetchone()["id"]
        conn.execute(
            """INSERT INTO position_lots(lot_id,market,symbol,bucket,strategy,
                opened_at,quantity_opened,quantity_remaining,entry_price,entry_fees,
                broker_mode,account_environment,created_at)
               VALUES (%s,'crypto',%s,'Tactical','test',%s,2,2,%s,%s,'PAPER','PAPER',%s)""",
            (lot_id,symbol,now,price,fee,now),
        )
        conn.execute(
            """INSERT INTO trade_ledger(trade_id,market,symbol,bucket,strategy,side,quantity,
                entry_time,entry_price,gross_pnl,fees,net_pnl,return_pct,
                broker_mode,account_environment,status,created_at,updated_at)
               VALUES (%s,'crypto',%s,'Tactical','test','BUY',2,%s,%s,
                       0,%s,%s,0,'PAPER','PAPER','OPEN',%s,%s)""",
            (ledger_id,symbol,now,price,fee,-fee,now,now),
        )
        return trade_id,lot_id,ledger_id

    with connect() as conn:
        assert "test" in conn.info.dbname.lower()
        with conn.transaction(force_rollback=True):
            trade_id,lot_id,ledger_id=seed(conn)
            _record_order_and_fill(
                conn,execution_trade_id=trade_id,market="crypto",symbol=symbol,
                side="BUY",quantity=2,price=price,reference_price=price,
                fee_amount=fee,requested_notional=price*2,
                lot_links=[(lot_id,ledger_id,2)],
                quote={"provider":"Coinbase Exchange public depth",
                       "coinbase_paper_pricing_receipt":receipt},reason="test",
            )
            row = conn.execute(
                """SELECT b.book_sha256,b.canonical_trade_id,b.receipt,
                          f.quantity,f.fee_amount,l.lot_id
                   FROM paper_fill_pricing_books b
                   JOIN paper_fills f ON f.fill_id=b.fill_id
                   JOIN paper_fill_lot_links l ON l.fill_id=b.fill_id
                   WHERE b.canonical_trade_id=%s""",(trade_id,),
            ).fetchone()
            assert row["canonical_trade_id"] == trade_id
            assert row["book_sha256"].strip() == receipt["sha256"]
            assert row["receipt"]["book"]["sequence"] == 345
            assert row["quantity"] == 2
            assert row["lot_id"] == lot_id
            assert row["fee_amount"] == pytest.approx(fee)

            bad = {**receipt,"fee_amount":"0"}
            doomed = None
            with pytest.raises(BookRejected):
                with conn.transaction():
                    doomed,lot2,ledger2=seed(conn)
                    _record_order_and_fill(
                        conn,execution_trade_id=doomed,market="crypto",symbol=symbol,
                        side="BUY",quantity=2,price=price,reference_price=price,
                        fee_amount=fee,requested_notional=price*2,
                        lot_links=[(lot2,ledger2,2)],
                        quote={"coinbase_paper_pricing_receipt":bad},reason="test",
                    )
            assert doomed is not None
            assert conn.execute("SELECT 1 FROM trades WHERE id=%s",(doomed,)).fetchone() is None
            assert conn.execute("SELECT 1 FROM paper_fill_pricing_books WHERE canonical_trade_id=%s",(doomed,)).fetchone() is None
