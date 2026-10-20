"""Suppression of repeated unavailable-state publications cannot suppress recovery."""
from contextlib import contextmanager

import database
import coinbase_paper_pricing as pricing
from kraken_research_data import ResearchUnavailable
from test_coinbase_paper_pricing import provider, book


class Recorder:
    def __init__(self):
        self.writes = []

    @contextmanager
    def connect(self):
        yield self

    def execute(self, statement, params):
        assert "coinbase_paper_price_state" in statement
        assert "INSERT INTO" in statement
        assert len(params) == 1
        self.writes.append(params[0])


def fail_pricing(monkeypatch, recorder):
    p = provider(monkeypatch)
    p.publish = True
    ticks = [0.0]
    monkeypatch.setattr(pricing.time, "monotonic", lambda: ticks[0])
    monkeypatch.setattr(database, "connect", recorder.connect)
    p.data.request.side_effect = ResearchUnavailable("PROVIDER_BUDGET_BLOCKED")
    return p, ticks


def test_repeated_unavailable_quotes_do_not_write_identical_status_on_every_scan(monkeypatch, caplog):
    recorder = Recorder()
    p, ticks = fail_pricing(monkeypatch, recorder)
    with caplog.at_level("INFO", logger="coinbase-paper-pricing"):
        for t in range(0, 60, 3):
            ticks[0] = t
            assert p.quote("BTC-USD") is None
        assert len(recorder.writes) == 1
        assert caplog.text.count("COINBASE PAPER PRICING |") == 1

        ticks[0] = 60
        assert p.quote("BTC-USD") is None
        assert len(recorder.writes) == 2
        assert caplog.text.count("COINBASE PAPER PRICING |") == 2

    # No extra HTTP requests, lease reservations or freshness shortcuts:
    assert p.data.request.call_count == 21
    assert p.last_health["BTC-USD"] == {
        "status": "UNAVAILABLE", "reason": "PROVIDER_BUDGET_BLOCKED",
    }


def test_failure_classification_changes_publish_immediately(monkeypatch):
    recorder = Recorder()
    p, ticks = fail_pricing(monkeypatch, recorder)
    assert p.quote("BTC-USD") is None
    assert len(recorder.writes) == 1

    ticks[0] = 1
    p.data.request.side_effect = ResearchUnavailable("PROVIDER_LEDGER_UNAVAILABLE")
    assert p.quote("BTC-USD") is None
    assert len(recorder.writes) == 2
    assert "PROVIDER_LEDGER_UNAVAILABLE" in recorder.writes[-1]

    ticks[0] = 2
    assert p.quote("BTC-USD") is None
    assert len(recorder.writes) == 2

    # A new blocked symbol is a meaningful change even while throttled.
    ticks[0] = 3
    assert p.quote("ETH-USD") is None
    assert len(recorder.writes) == 3


def test_valid_quote_recovery_is_immediate_and_every_actual_valid_quote_is_published(monkeypatch):
    recorder = Recorder()
    p, ticks = fail_pricing(monkeypatch, recorder)
    assert p.quote("BTC-USD") is None
    assert len(recorder.writes) == 1

    product = {
        "id": "BTC-USD", "base_currency": "BTC", "quote_currency": "USD",
        "status": "online", "trading_disabled": False, "cancel_only": False,
        "limit_only": False, "post_only": False, "auction_mode": False,
        "base_increment": "0.00000001", "quote_increment": "0.01",
        "min_market_funds": "1",
    }
    p.data.request.side_effect = lambda _, path, *args: (
        book() if path.endswith("/book") else product
    )

    ticks[0] = 2
    first = p.quote("BTC-USD")
    assert first is not None and p.authentic(first, "BTC-USD")
    assert len(recorder.writes) == 2
    assert "VALID_PAPER_PRICE" in recorder.writes[-1]

    ticks[0] = 3
    second = p.quote("BTC-USD")
    assert second is not None and p.authentic(second, "BTC-USD")
    assert len(recorder.writes) == 3

    # A valid quote is still governed by its original exchange timestamp.
    assert first["quote_timestamp"] == second["quote_timestamp"]
    assert first["quote_id"] == second["quote_id"]


def test_missing_reference_never_results_in_network_call_or_valid_quote(monkeypatch):
    recorder = Recorder()
    p, ticks = fail_pricing(monkeypatch, recorder)
    p.reference = lambda: {"quotes": {}}
    for t in range(30):
        ticks[0] = float(t)
        assert p.quote("BTC-USD") is None
    assert p.data.request.call_count == 0
    assert len(recorder.writes) == 1
    assert "NO_FRESH_INDEPENDENT_REFERENCE" in recorder.writes[0]
