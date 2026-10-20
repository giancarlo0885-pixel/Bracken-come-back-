"""Missing current signal is never replaced with fabricated entry-time evidence."""
from contextlib import contextmanager

import pytest

import database
import paper_shadow_exit_challenger as shadow


class _Result:
    def __init__(self, rows):
        self.rows = rows

    def fetchall(self):
        return self.rows


class _Connection:
    def __init__(self, lots=(), *, unavailable=False):
        self.lots = list(lots)
        self.unavailable = unavailable
        self.queries = []

    def execute(self, sql, params):
        self.queries.append((sql, params))
        if "FROM position_lots" in sql:
            if self.unavailable:
                raise RuntimeError("simulated read-only ledger failure")
            return _Result(self.lots[:9])
        if "FROM positions" in sql:
            return _Result([{"market": "crypto", "symbol": "PAXG-USD",
                             "quantity": 1, "opened_at": "historical"}])
        raise AssertionError("unexpected SQL")


@pytest.mark.parametrize("lots,status,present,forecasts", [
    ([], "NO_OPEN_LOT_ROWS", 0, 0),
    ([{"entry_signal_id": None, "entry_forecast_id": None}],
     "OPEN_LOTS_WITHOUT_ENTRY_SIGNAL_IDS", 0, 0),
    ([{"entry_signal_id": "entry-1", "entry_forecast_id": None}],
     "ENTRY_SIGNAL_IDS_PRESENT_BUT_CURRENT_SIGNAL_ABSENT", 1, 0),
    ([{"entry_signal_id": "entry-1", "entry_forecast_id": "forecast-1"},
      {"entry_signal_id": None, "entry_forecast_id": None}],
     "MIXED_ENTRY_SIGNAL_ID_COVERAGE", 1, 1),
])
def test_missing_signal_lot_diagnostic_classifies_without_repair(
    lots, status, present, forecasts
):
    conn = _Connection(lots)
    info = shadow._missing_signal_open_lot_diagnostic(conn, "crypto", "PAXG-USD")
    assert info == {
        "status": status, "sampled_open_lots": len(lots), "truncated": False,
        "entry_signal_ids_present": present,
        "entry_forecast_ids_present": forecasts,
    }
    assert len(conn.queries) == 1
    sql, args = conn.queries[0]
    assert sql.lstrip().startswith("SELECT")
    assert "LIMIT 9" in sql and "quantity_remaining" in sql
    assert args == ("crypto", "PAXG-USD")


def test_bounded_lot_sample_never_claims_exact_count():
    lots = [{"entry_signal_id": "id", "entry_forecast_id": None}] * 11
    info = shadow._missing_signal_open_lot_diagnostic(
        _Connection(lots), "crypto", "PAXG-USD")
    assert info["sampled_open_lots"] == 8
    assert info["truncated"] is True
    assert info["entry_signal_ids_present"] == 8


def test_unavailable_lot_read_is_ambiguous_and_non_qualifying():
    info = shadow._missing_signal_open_lot_diagnostic(
        _Connection(unavailable=True), "crypto", "PAXG-USD")
    assert info["status"] == "LOT_EVIDENCE_UNAVAILABLE"
    assert info["entry_signal_ids_present"] is None


def test_sampler_logs_absent_current_signal_without_mutation_or_eligibility(monkeypatch, caplog):
    conn = _Connection([{"entry_signal_id": None, "entry_forecast_id": None}])

    @contextmanager
    def connect():
        yield conn

    monkeypatch.setattr(database, "connect", connect)
    monkeypatch.setattr(shadow, "active", lambda: True)
    monkeypatch.setattr(shadow, "_latest_signal", lambda *args: None)
    with caplog.at_level("WARNING"):
        assert shadow.sample_open_positions("crypto") == 0
    assert "SHADOW_MISSING_CURRENT_SIGNAL" in caplog.text
    assert "symbol=PAXG-USD" in caplog.text
    assert "OPEN_LOTS_WITHOUT_ENTRY_SIGNAL_IDS" in caplog.text
    assert "eligible_for_shadow_exit=false" in caplog.text
    assert all(sql.lstrip().startswith("SELECT") for sql, _ in conn.queries)
    assert not any("UPDATE " in sql or "INSERT " in sql or "DELETE " in sql
                   for sql, _ in conn.queries)
