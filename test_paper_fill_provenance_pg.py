"""Actual PostgreSQL fill/lot graph, commit boundaries and split-cost evidence.

Only disposable UUID schemas on an explicitly local test database are writable.
The reader uses a second, READ ONLY connection; no production data is modified.
"""
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import os
from pathlib import Path
import uuid

import pytest

from paper_fill_provenance import PROVENANCE_VERSION, attach_exact_fill_lot_evidence, load_fill_lot_allocations


@pytest.fixture
def fill_evidence_db():
    import psycopg
    from psycopg import sql
    from psycopg.conninfo import conninfo_to_dict
    from psycopg.rows import dict_row

    dsn = os.getenv("DATABASE_URL", "")
    if not dsn:
        pytest.skip("PostgreSQL integration only")
    settings = conninfo_to_dict(dsn)
    name = settings.get("dbname", "")
    if (settings.get("host") not in {"localhost", "127.0.0.1", "::1"}
            or not (name.endswith("_test") or name.startswith("test_"))):
        pytest.skip("Fill fixtures require a local, explicitly named test database")
    schema = "fill_evidence_" + uuid.uuid4().hex
    with psycopg.connect(dsn, autocommit=True) as admin:
        admin.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
    options = "-c search_path=" + schema + ",pg_catalog"
    try:
        with psycopg.connect(dsn, options=options) as setup:
            setup.execute("""CREATE TABLE trades(
                id bigserial PRIMARY KEY,market text,symbol text,side text,
                quantity float8,price float8,value float8,realized_pnl float8,created_at text)""")
            setup.execute("""CREATE TABLE position_lots(
                lot_id text PRIMARY KEY,market text,symbol text,opened_at text,
                entry_price float8,entry_fees float8,quantity_opened float8,
                quantity_remaining float8,broker_mode text,account_environment text)""")
            setup.execute("""CREATE TABLE trade_ledger(
                trade_id text PRIMARY KEY,market text,symbol text,side text,quantity float8,
                entry_time text,exit_time text,entry_price float8,exit_price float8,
                gross_pnl float8,net_pnl float8,fees float8,broker_mode text,account_environment text,
                entry_signal_id text,entry_decision_id text,entry_forecast_id text,entry_quote_id text)""")
            root = Path(__file__).parent / "migrations"
            setup.execute((root / "20260830_paper_execution_accounting.sql").read_text())
            setup.execute((root / "20261014_atomic_paper_fill_execution_links.sql").read_text())

        def connection():
            return psycopg.connect(dsn, options=options, row_factory=dict_row)

        @contextmanager
        def reader():
            with connection() as conn:
                conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
                yield conn

        yield connection, reader
    finally:
        with psycopg.connect(dsn, autocommit=True) as cleanup:
            cleanup.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema)))


def _seed_graph(conn):
    """Use the production atomic writer; no reader-specific fabricated links."""
    from paper_execution_accounting import _record_order_and_fill
    entry = datetime.now(timezone.utc) - timedelta(hours=2)
    exit_time = entry + timedelta(hours=1)

    def ledger(trade_id, lot_id, side, q, price, entry_price, fees, event):
        gross = 0 if side == "BUY" else q*(price-entry_price)
        conn.execute("""INSERT INTO trade_ledger VALUES(
            %s,'crypto','TEST-USD',%s,%s,%s,%s,%s,%s,%s,%s,%s,'PAPER','PAPER',
            %s,%s,%s,%s)""",
            (trade_id, side, q, entry.isoformat(), event.isoformat() if side == "SELL" else None,
             entry_price, price if side == "SELL" else None, gross, gross-fees, fees,
             "signal:"+lot_id, "decision:"+lot_id, "forecast:"+lot_id, "quote:"+lot_id))

    def execution(side, q, price, fee, gross, event, links):
        trade_id = conn.execute("""INSERT INTO trades(market,symbol,side,quantity,price,value,realized_pnl,created_at)
            VALUES('crypto','TEST-USD',%s,%s,%s,%s,%s,%s) RETURNING id""",
            (side, q, price, q*price, gross, event.isoformat())).fetchone()["id"]
        _record_order_and_fill(conn, execution_trade_id=trade_id, market="crypto", symbol="TEST-USD",
            side=side, quantity=q, price=price, reference_price=price, fee_amount=fee,
            requested_notional=q*price, lot_links=links, quote={"provider":"test"}, reason="test")
        return trade_id

    for lot_id, q, price, fee in (("lot-a", 2, 100, .2), ("lot-b", 3, 101, .6)):
        conn.execute("""INSERT INTO position_lots VALUES(
            %s,'crypto','TEST-USD',%s,%s,%s,%s,0,'PAPER','PAPER')""",
            (lot_id, entry.isoformat(), price, fee, q))
        ledger("buy:"+lot_id, lot_id, "BUY", q, price, price, fee, entry)
        execution("BUY", q, price, fee, 0, entry, [(lot_id,"buy:"+lot_id,q)])
    ledger("sell:a", "lot-a", "SELL", 2, 103, 100, .6, exit_time)
    ledger("sell:b:first", "lot-b", "SELL", 1, 103, 101, .3, exit_time)
    execution("SELL", 3, 103, .9, 8, exit_time,
              [("lot-a","sell:a",2), ("lot-b","sell:b:first",1)])
    final_exit = exit_time + timedelta(minutes=1)
    ledger("sell:b:second", "lot-b", "SELL", 2, 102, 101, .4, final_exit)
    execution("SELL", 2, 102, .4, 2, final_exit, [("lot-b","sell:b:second",2)])


def _read(conn, ids=None):
    allocations = load_fill_lot_allocations(conn, trade_ids=ids, market="crypto")
    rows = list(conn.execute("SELECT * FROM trade_ledger WHERE side='SELL' ORDER BY trade_id").fetchall())
    if ids is not None:
        rows = [row for row in rows if row["trade_id"] in ids]
    return attach_exact_fill_lot_evidence(rows, allocations)


def test_postgres_atomic_split_and_partial_fills_reconcile_original_fees(fill_evidence_db):
    connection, reader = fill_evidence_db
    with connection() as writer:
        _seed_graph(writer)
    with reader() as conn:
        assert conn.execute("SHOW transaction_read_only").fetchone()["transaction_read_only"] == "on"
        rows = _read(conn)
        assert len(rows) == 3
        assert all(row.get("_fill_accounting_provenance") == PROVENANCE_VERSION for row in rows)
        by_id = {row["trade_id"]:row for row in rows}
        assert Decimal(by_id["sell:a"]["_canonical_fill_fee"]) == Decimal(".6")
        assert Decimal(by_id["sell:b:first"]["_canonical_fill_fee"]) == Decimal(".3")
        assert by_id["sell:a"]["_canonical_fill_id"] == by_id["sell:b:first"]["_canonical_fill_id"]
        assert by_id["sell:b:first"]["_canonical_entry_fill_id"] == by_id["sell:b:second"]["_canonical_entry_fill_id"]
        net = Decimal(0)
        allocated_entry_fees = Decimal(0)
        for row in rows:
            entry_fee = (Decimal(row["_canonical_entry_fill_fee"])*Decimal(str(row["quantity"]))
                         /Decimal(row["_canonical_entry_quantity"]))
            allocated_entry_fees += entry_fee
            net += Decimal(str(row["gross_pnl"]))-Decimal(row["_canonical_fill_fee"])-entry_fee
        assert allocated_entry_fees == Decimal(".8")
        assert net == Decimal("7.9")
        assert conn.execute("SELECT COUNT(*) n FROM paper_fills").fetchone()["n"] == 4


def test_postgres_uncommitted_and_rolled_back_graphs_never_become_evidence(fill_evidence_db):
    connection, reader = fill_evidence_db
    with connection() as writer:
        _seed_graph(writer)
        with reader() as conn:
            assert load_fill_lot_allocations(conn, trade_ids=["sell:a"], market="crypto") == []
        writer.rollback()
    with reader() as conn:
        assert load_fill_lot_allocations(conn, market="crypto") == []
        assert conn.execute("SELECT COUNT(*) n FROM trades").fetchone()["n"] == 0
    with connection() as writer:
        _seed_graph(writer)
    with reader() as conn:
        assert _read(conn, ["sell:a"])[0]["_fill_accounting_provenance"] == PROVENANCE_VERSION


def test_postgres_missing_or_ambiguous_buy_link_never_infers_a_round_trip(fill_evidence_db):
    connection, reader = fill_evidence_db
    with connection() as writer:
        _seed_graph(writer)
        # Test-only removal of a fixture link leaves its historical fill and
        # canonical rows intact: the reader must not infer the missing edge.
        writer.execute("DELETE FROM paper_fill_lot_links WHERE ledger_trade_id='buy:lot-a'")
        original = writer.execute("SELECT * FROM trade_ledger WHERE trade_id='buy:lot-b'").fetchone()
        writer.execute("""INSERT INTO trade_ledger SELECT 'ambiguous-buy',market,symbol,side,quantity,
            entry_time,exit_time,entry_price,exit_price,gross_pnl,net_pnl,fees,broker_mode,
            account_environment,entry_signal_id,entry_decision_id,entry_forecast_id,entry_quote_id
            FROM trade_ledger WHERE trade_id='buy:lot-b'""")
        writer.execute("""INSERT INTO paper_fill_lot_links SELECT fill_id,lot_id,'ambiguous-buy',quantity
            FROM paper_fill_lot_links WHERE ledger_trade_id='buy:lot-b'""")
        assert original is not None
    with reader() as conn:
        rows = _read(conn)
        assert all("_fill_accounting_provenance" not in row for row in rows)
        assert conn.execute("SELECT COUNT(*) n FROM paper_fills").fetchone()["n"] == 4
        assert conn.execute("SELECT COUNT(*) n FROM trade_ledger").fetchone()["n"] == 6


def test_postgres_reader_is_read_only_and_does_not_persist_inferred_markers(fill_evidence_db):
    import psycopg
    connection, reader = fill_evidence_db
    with connection() as writer:
        _seed_graph(writer)
    with reader() as conn:
        before = conn.execute("SELECT COUNT(*) n FROM paper_fill_lot_links").fetchone()["n"]
        assert all(row.get("_fill_accounting_provenance") for row in _read(conn))
        assert conn.execute("SELECT COUNT(*) n FROM paper_fill_lot_links").fetchone()["n"] == before
        with pytest.raises(psycopg.errors.ReadOnlySqlTransaction):
            conn.execute("UPDATE paper_fills SET fee_amount=0")
        conn.rollback()
    with reader() as conn:
        columns = {row["column_name"] for row in conn.execute("""SELECT column_name FROM information_schema.columns
            WHERE table_schema=current_schema() AND table_name='paper_fills'""").fetchall()}
        assert "canonical_trade_id" in columns
        assert not columns.intersection({"fee_contract", "execution_trade_id"})
        assert len(_read(conn)) == 3


def test_postgres_verifier_reports_actual_committed_split_graph_and_unlinked_history(fill_evidence_db):
    from trade_evidence_verification import verify_committed_trade_evidence
    connection, _ = fill_evidence_db
    ids = ["sell:a", "sell:b:first", "sell:b:second"]
    with connection() as writer:
        _seed_graph(writer)
        pending = verify_committed_trade_evidence(ids, connect_factory=connection)
        assert pending["transaction_read_only"] is True
        assert all(row["ledger_status"] == "MISSING" for row in pending["records"])
        assert all(row["canonical_fill_linkage"] == "UNAVAILABLE" for row in pending["records"])
    committed = verify_committed_trade_evidence(ids, connect_factory=connection)
    assert all(row["ledger_status"] == "COMMITTED" for row in committed["records"])
    assert all(row["canonical_fill_linkage"] == "EXACT_LINKED" for row in committed["records"])
    assert sum(row["linked_round_trip_fees"] for row in committed["records"]) == pytest.approx(2.1)
    assert sum(row["linked_round_trip_net_pnl"] for row in committed["records"]) == pytest.approx(7.9)
    assert committed["records"][0]["canonical_lot_id"] == "lot-a"
    assert committed["records"][0]["entry_ledger_trade_id"] == "buy:lot-a"
    assert committed["records"][1]["canonical_lot_id"] == "lot-b"
    assert committed["records"][0]["exit_fill_id"] == committed["records"][1]["exit_fill_id"]
    assert committed["records"][1]["entry_fill_id"] == committed["records"][2]["entry_fill_id"]
    assert all(row["brain_status"] == "UNAVAILABLE" for row in committed["records"])
    assert all(row["ranking_decision_consumption"] == "UNAVAILABLE" for row in committed["records"])
    # A similar retained historical row is a committed ledger row, but never
    # receives an execution/fill identity from the nearby canonical graph.
    with connection() as writer:
        writer.execute("""INSERT INTO trade_ledger SELECT 'legacy-unlinked',market,symbol,side,quantity,
            entry_time,exit_time,entry_price,exit_price,gross_pnl,net_pnl,fees,broker_mode,
            account_environment,entry_signal_id,entry_decision_id,entry_forecast_id,entry_quote_id
            FROM trade_ledger WHERE trade_id='sell:a'""")
    legacy = verify_committed_trade_evidence(["legacy-unlinked"], connect_factory=connection)["records"][0]
    assert legacy["ledger_status"] == "COMMITTED"
    assert legacy["canonical_fill_linkage"] == "UNAVAILABLE"
    assert "linked_round_trip_net_pnl" not in legacy
    with connection() as check:
        assert check.execute("SELECT COUNT(*) n FROM trade_ledger").fetchone()["n"] == 6
        assert check.execute("SELECT COUNT(*) n FROM paper_fills").fetchone()["n"] == 4


def _seed_overclosed_lot(conn):
    from paper_execution_accounting import _record_order_and_fill
    entry = datetime.now(timezone.utc)-timedelta(hours=3)
    conn.execute("""INSERT INTO position_lots VALUES('overclosed-lot','crypto','TEST-USD',
        %s,100,1,10,0,'PAPER','PAPER')""", (entry.isoformat(),))
    fills = []
    for i, side, q, price, fee in ((0,"BUY",10,100,1), (1,"SELL",6,101,.6), (2,"SELL",6,101,.6)):
        event = entry+timedelta(hours=i)
        gross = 0 if side == "BUY" else 6
        ledger_id = "overclose:"+str(i)
        conn.execute("""INSERT INTO trade_ledger VALUES(%s,'crypto','TEST-USD',%s,%s,%s,%s,
            100,%s,%s,%s,%s,'PAPER','PAPER','signal:x','decision:x','forecast:x','quote:x')""",
            (ledger_id, side, q, entry.isoformat(), event.isoformat() if i else None,
             price if i else None, gross, gross-fee, fee))
        execution_id = conn.execute("""INSERT INTO trades(market,symbol,side,quantity,price,value,realized_pnl,created_at)
            VALUES('crypto','TEST-USD',%s,%s,%s,%s,%s,%s) RETURNING id""",
            (side,q,price,q*price,gross,event.isoformat())).fetchone()["id"]
        _record_order_and_fill(conn, execution_trade_id=execution_id, market="crypto", symbol="TEST-USD",
            side=side, quantity=q, price=price, reference_price=price, fee_amount=fee,
            requested_notional=q*price, lot_links=[("overclosed-lot",ledger_id,q)], quote={}, reason="test")
        fills.append(conn.execute("SELECT fill_id FROM paper_fills WHERE canonical_trade_id=%s",
                                  (execution_id,)).fetchone()["fill_id"])
    return fills


def test_postgres_cumulative_overclose_is_rejected_even_outside_requested_ids(fill_evidence_db):
    connection, reader = fill_evidence_db
    with connection() as writer:
        _seed_overclosed_lot(writer)
    with reader() as conn:
        allocations = load_fill_lot_allocations(conn, trade_ids=["overclose:1"], market="crypto")
        assert len(allocations) == 1
        assert Decimal(allocations[0]["lot_closed_quantity"]) == 12
        assert "_fill_accounting_provenance" not in _read(conn, ["overclose:1"])[0]
        assert all("_fill_accounting_provenance" not in row for row in _read(conn))
        assert conn.execute("SELECT COUNT(*) n FROM trade_ledger").fetchone()["n"] == 3


def test_postgres_future_close_does_not_taint_an_earlier_as_of_snapshot(fill_evidence_db):
    connection, reader = fill_evidence_db
    with connection() as writer:
        fills = _seed_overclosed_lot(writer)
        cutoff = datetime.now(timezone.utc)+timedelta(seconds=1)
        writer.execute("UPDATE paper_fills SET created_at=%s WHERE fill_id=%s",
                       ((cutoff+timedelta(days=1)).isoformat(),fills[-1]))
    with reader() as conn:
        allocations = load_fill_lot_allocations(conn, trade_ids=["overclose:1"], market="crypto", as_of=cutoff)
        assert Decimal(allocations[0]["lot_closed_quantity"]) == 6
        ledger = conn.execute("SELECT * FROM trade_ledger WHERE trade_id='overclose:1'").fetchone()
        assert attach_exact_fill_lot_evidence([ledger], allocations, as_of=cutoff)[0]["_fill_accounting_provenance"] == PROVENANCE_VERSION
        later = load_fill_lot_allocations(conn, trade_ids=["overclose:1"], market="crypto", as_of=cutoff+timedelta(days=2))
        assert Decimal(later[0]["lot_closed_quantity"]) == 12
        assert "_fill_accounting_provenance" not in attach_exact_fill_lot_evidence([ledger], later, as_of=cutoff+timedelta(days=2))[0]


@pytest.mark.parametrize("invalid_timestamp", ["not-a-date", "2026-10-15T12:00:00"])
def test_postgres_invalid_close_timestamp_fails_closed_for_that_lot_only(fill_evidence_db, invalid_timestamp):
    connection, reader = fill_evidence_db
    with connection() as writer:
        _seed_graph(writer)
        fills = _seed_overclosed_lot(writer)
        writer.execute("UPDATE paper_fills SET created_at=%s WHERE fill_id=%s",(invalid_timestamp,fills[-1]))
    with reader() as conn:
        allocations = load_fill_lot_allocations(conn, trade_ids=["overclose:1"], market="crypto")
        assert allocations[0]["lot_closed_quantity"] is None
        assert "_fill_accounting_provenance" not in _read(conn, ["overclose:1"])[0]
        assert _read(conn, ["sell:a"])[0]["_fill_accounting_provenance"] == PROVENANCE_VERSION


@pytest.fixture
def entry_audit_db(fill_evidence_db):
    connection, reader = fill_evidence_db
    with connection() as conn:
        _seed_graph(conn)
        conn.execute('ALTER TABLE trade_ledger ADD COLUMN id BIGSERIAL, ADD COLUMN decision_timestamp TEXT, ADD COLUMN feature_snapshot JSONB')
        conn.execute('''ALTER TABLE position_lots ADD COLUMN entry_signal_id TEXT,
            ADD COLUMN entry_decision_id TEXT, ADD COLUMN entry_forecast_id TEXT,
            ADD COLUMN entry_quote_id TEXT, ADD COLUMN decision_timestamp TEXT, ADD COLUMN feature_snapshot JSONB''')
        conn.execute('''UPDATE trade_ledger SET entry_signal_id=CASE WHEN entry_signal_id='signal:lot-a' THEN '1' ELSE '2' END,
            decision_timestamp=(entry_time::timestamptz-INTERVAL '1 second')::text,feature_snapshot='{"momentum":1}' ''')
        conn.execute('''UPDATE position_lots SET entry_signal_id=CASE WHEN lot_id='lot-a' THEN '1' ELSE '2' END,
            entry_decision_id='decision:'||lot_id,entry_forecast_id='forecast:'||lot_id,entry_quote_id='quote:'||lot_id,
            decision_timestamp=(opened_at::timestamptz-INTERVAL '1 second')::text,feature_snapshot='{"momentum":1}' ''')
        conn.execute('CREATE TABLE signals(id BIGINT PRIMARY KEY,market TEXT,symbol TEXT,created_at TEXT,details JSONB)')
        conn.execute('''CREATE TABLE forecasts(forecast_id TEXT,signal_id BIGINT,market TEXT,symbol TEXT,
            created_at TEXT,source_quote_timestamp TEXT)''')
        conn.execute('''INSERT INTO signals(id,market,symbol,created_at) SELECT entry_signal_id::bigint,market,symbol,decision_timestamp
            FROM trade_ledger WHERE side='BUY' ''')
        conn.execute('''INSERT INTO forecasts SELECT entry_forecast_id,entry_signal_id::bigint,market,symbol,
            decision_timestamp,(entry_time::timestamptz-INTERVAL '5 seconds')::text FROM trade_ledger WHERE side='BUY' ''')
    return connection


def test_committed_entry_audit_resolves_actual_graph_and_is_read_only(entry_audit_db):
    from paper_trade_evidence_audit import summarize_trade_evidence
    with entry_audit_db() as conn:
        report=summarize_trade_evidence(conn,market='crypto')
        assert report['paper_fills']=={'total':4,'unlinked_legacy':0,'dangling_trade_id':0,'invalid_fee':0,'missing_lot_links':0}
        assert report['sample_rows']==report['sample_accounting_verified']==5
        assert report['sample_entry_lots']==report['sample_complete_entry_references']==2
        assert report['sample_entry_exclusions']=={}
        assert conn.execute('SHOW transaction_read_only').fetchone()['transaction_read_only']=='on'
        assert report['historical_repair']=='NONE' and report['profitability']=='NOT_EVALUATED'


def test_entry_audit_never_substitutes_wrong_forecast_or_lot_features(entry_audit_db):
    from paper_trade_evidence_audit import summarize_trade_evidence
    with entry_audit_db() as conn:
        conn.execute("UPDATE forecasts SET signal_id=2 WHERE forecast_id='forecast:lot-a'")
        conn.execute("UPDATE position_lots SET feature_snapshot='{}' WHERE lot_id='lot-a'")
    with entry_audit_db() as conn:
        report=summarize_trade_evidence(conn,market='crypto')
        assert report['sample_accounting_verified']==5  # Finance and decision-reference scopes differ.
        assert report['sample_complete_entry_references']==1
        assert report['sample_entry_exclusions']=={'lot_entry_snapshot_mismatch':1,'unresolved_or_ambiguous_entry_forecast':1}


def test_stock_zero_export_can_be_distinguished_from_existing_canonical_trades(entry_audit_db):
    from paper_trade_evidence_audit import summarize_trade_evidence
    with entry_audit_db() as conn:
        conn.execute("INSERT INTO trades(market,symbol,side,quantity,price,created_at) VALUES('cash','TEST','BUY',1,100,'2026-10-18T00:00:00Z')")
    with entry_audit_db() as conn:
        report=summarize_trade_evidence(conn,market='cash')
        assert report['canonical_trade_counts']==[{'side':'BUY','total':1}]
        assert report['ledger_mode_counts']==[] and report['sample_rows']==0
        assert report['sample_entry_lots']==0 and report['paper_fills']['total']==0


def test_entry_audit_distinguishes_abstention_and_missing_link_without_repair(entry_audit_db):
    from paper_trade_evidence_audit import summarize_trade_evidence
    with entry_audit_db() as conn:
        conn.execute("UPDATE trade_ledger SET entry_forecast_id=NULL")
        conn.execute("UPDATE position_lots SET entry_forecast_id=NULL")
        conn.execute("DELETE FROM forecasts WHERE signal_id=1")
        conn.execute('''UPDATE signals SET details='{"forecast_unavailable_reason":"stale_live_quote_timestamp"}' WHERE id=1''')
    with entry_audit_db() as conn:
        result=summarize_trade_evidence(conn,market='crypto')
        assert result['sample_forecast_generation_counts']=={
            'RECORDED_FORECAST_UNAVAILABLE':1,'PERSISTED_FORECAST_MISSING_ENTRY_LINK':1}
        assert result['sample_complete_entry_references']==0
        assert all(r['entry_forecast_id'] is None for r in result['sample_entry_references'])
    with entry_audit_db() as conn:
        assert conn.execute('SELECT COUNT(*) AS n FROM position_lots WHERE entry_forecast_id IS NOT NULL').fetchone()['n']==0


def test_entry_audit_wrong_scope_does_not_hide_original_signal_forecast(entry_audit_db):
    from paper_trade_evidence_audit import summarize_trade_evidence
    with entry_audit_db() as conn:
        conn.execute('''INSERT INTO forecasts SELECT 'unrelated',signal_id,'cash','OTHER',created_at,source_quote_timestamp
            FROM forecasts WHERE signal_id=1''')
    with entry_audit_db() as conn:
        report=summarize_trade_evidence(conn,market='crypto')
        assert report['sample_forecast_generation_counts']=={'PERSISTED_EXACT_ENTRY_LINK':2}
