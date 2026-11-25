"""An actual local PostgreSQL diagnostic row lock cannot hold up a paper quote."""
from contextlib import contextmanager
import threading

import database
from test_kraken_reference_pg import pg
from test_kraken_paper_pricing import provider
from test_paper_quote_diagnostic_handoff import install


def test_diagnostic_row_lock_leaves_quote_available_and_publishes_after_release(pg, monkeypatch):
    pricing, source = provider(monkeypatch)
    pricing.publish = True
    attempted, committed, returned = threading.Event(), threading.Event(), threading.Event()
    values = []

    @contextmanager
    def diagnostic_connection():
        with pg() as conn:
            class Connection:
                def execute(self, statement, params):
                    attempted.set()
                    result = conn.execute(statement, params)
                    committed.set()
                    return result
            yield Connection()

    with pg() as locker, pg() as reader:
        locker.execute('''CREATE TABLE coinbase_paper_price_state (
            id SMALLINT PRIMARY KEY CHECK(id=1),updated_at TIMESTAMPTZ NOT NULL,payload JSONB NOT NULL)''')
        locker.execute("INSERT INTO coinbase_paper_price_state VALUES(1,NOW(),'{}')")
        locker.execute('BEGIN')
        locker.execute('SELECT * FROM coinbase_paper_price_state WHERE id=1 FOR UPDATE')
        monkeypatch.setattr(database, 'connect', diagnostic_connection)
        worker = install(monkeypatch, pricing)

        def request():
            try:
                values.append(pricing.quote('BTC-USD'))
            finally:
                returned.set()

        request_thread = threading.Thread(target=request)
        request_thread.start()
        try:
            assert attempted.wait(2)
            assert returned.wait(1), 'actual PostgreSQL diagnostic lock blocked quote'
            assert not committed.is_set()
            assert pricing.authentic(values[0], 'BTC-USD')
            assert reader.execute('SELECT payload FROM coinbase_paper_price_state').fetchone()['payload'] == {}
            locker.execute('COMMIT')
            assert committed.wait(2)
            published = reader.execute('SELECT payload FROM coinbase_paper_price_state').fetchone()['payload']
            assert published['symbols']['BTC-USD']['quote_id'] == values[0]['quote_id']
            assert published['symbols']['BTC-USD']['quote_timestamp'] == source['quotes']['BTC-USD']['quote_timestamp']
            assert published['broker_submission'] == 'NONE'
        finally:
            locker.execute('ROLLBACK')
            worker.stop_event.set()
            request_thread.join(timeout=2)
            pricing._health_publisher.stop()
        assert not request_thread.is_alive()
