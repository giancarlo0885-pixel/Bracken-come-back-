"""PostgreSQL proof of atomic paper trade-to-fill-to-FIFO-lot linkage.

Runs only against a known test database. No production DB or external broker.
Every fixture mutation is rolled back, including the successful test execution.
"""
from __future__ import annotations

from datetime import datetime, timezone
import os
import uuid

import pytest


@pytest.mark.skipif(not os.getenv("DATABASE_URL"), reason="PostgreSQL CI only")
def test_exact_execution_fill_link_and_failed_atomic_rollback():
    import psycopg
    from database import connect
    from migrations import run_migrations
    from paper_execution_accounting import _record_order_and_fill

    # Never permit integration fixtures on Railway production or an unknown DB.
    from psycopg.conninfo import conninfo_to_dict
    settings = conninfo_to_dict(os.environ["DATABASE_URL"])
    dbname = settings.get("dbname", "")
    host = settings.get("host", "")
    if host not in {"localhost", "127.0.0.1", "::1"} or "test" not in dbname.lower():
        pytest.skip("exact-fill fixtures require a local test database")
    run_migrations()

    symbol = "AUDIT-" + uuid.uuid4().hex[:12].upper() + "-USD"
    now = datetime.now(timezone.utc).isoformat()

    def seed(conn, suffix):
        lot_id = f"lot:atomic:{suffix}:{uuid.uuid4()}"
        ledger_id = f"ledger:atomic:{suffix}:{uuid.uuid4()}"
        trade_id = conn.execute(
            """INSERT INTO trades(market,symbol,side,quantity,price,value,
                   realized_pnl,score,reason,created_at)
               VALUES('crypto',%s,'BUY',2,100,200,0,NULL,'test',%s)
               RETURNING id""", (symbol, now),
        ).fetchone()["id"]
        conn.execute(
            """INSERT INTO position_lots(
                   lot_id,market,symbol,bucket,strategy,opened_at,
                   quantity_opened,quantity_remaining,entry_price,entry_fees,
                   broker_mode,account_environment,created_at)
               VALUES (%s,'crypto',%s,'Tactical','test',%s,2,2,100,0.2,'PAPER','PAPER',%s)""",
            (lot_id, symbol, now, now),
        )
        conn.execute(
            """INSERT INTO trade_ledger(
                   trade_id,market,symbol,bucket,strategy,side,quantity,
                   entry_time,entry_price,gross_pnl,fees,net_pnl,return_pct,
                   broker_mode,account_environment,status,created_at,updated_at)
               VALUES (%s,'crypto',%s,'Tactical','test','BUY',2,%s,100,
                       0,0.2,-0.2,0,'PAPER','PAPER','OPEN',%s,%s)""",
            (ledger_id, symbol, now, now, now),
        )
        return trade_id, lot_id, ledger_id

    with connect() as conn:
        assert "test" in conn.info.dbname.lower()
        with conn.transaction(force_rollback=True):
            trade_id, lot_id, ledger_id = seed(conn, "committed-within-txn")
            _record_order_and_fill(
                conn, execution_trade_id=trade_id, market="crypto",
                symbol=symbol, side="BUY", quantity=2, price=100,
                reference_price=100, fee_amount=0.2, requested_notional=200,
                lot_links=[(lot_id, ledger_id, 2)],
                quote={"provider": "test-exact-quote"}, reason="entry",
            )
            row = conn.execute(
                """SELECT f.canonical_trade_id, f.quantity, f.fee_amount,
                          links.lot_id, links.ledger_trade_id, t.fees
                   FROM paper_fills f
                   JOIN paper_fill_lot_links links ON links.fill_id=f.fill_id
                   JOIN trades t ON t.id=f.canonical_trade_id
                   WHERE f.canonical_trade_id=%s""",
                (trade_id,),
            ).fetchone()
            assert row["canonical_trade_id"] == trade_id
            assert row["lot_id"] == lot_id
            assert row["ledger_trade_id"] == ledger_id
            assert row["quantity"] == 2
            assert row["fee_amount"] == pytest.approx(0.2)
            assert row["fees"] == pytest.approx(0.2)

            rolled_back_trade_id = None
            with pytest.raises(psycopg.errors.ForeignKeyViolation):
                with conn.transaction():
                    rolled_back_trade_id, lot2, ledger2 = seed(conn, "failed")
                    _record_order_and_fill(
                        conn, execution_trade_id=rolled_back_trade_id,
                        market="crypto", symbol=symbol, side="BUY",
                        quantity=2, price=100, reference_price=100,
                        fee_amount=0.2, requested_notional=200,
                        lot_links=[("lot:missing:never-existed", ledger2, 2)],
                        quote={}, reason="entry",
                    )
            assert rolled_back_trade_id is not None
            assert conn.execute(
                "SELECT 1 FROM trades WHERE id=%s", (rolled_back_trade_id,),
            ).fetchone() is None
            assert conn.execute(
                "SELECT COUNT(*) AS n FROM paper_fills WHERE canonical_trade_id=%s",
                (rolled_back_trade_id,),
            ).fetchone()["n"] == 0
