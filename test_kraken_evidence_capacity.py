"""Forward-only evidence capacity diagnostics; preserve every original row."""
import pytest

from kraken_brain_evidence import (
    RESEARCH_EVIDENCE_MAX_ID,
    committed_receipt,
    read_evidence_capacity,
)


class Result:
    def __init__(self, data):
        self.data = data

    def fetchone(self):
        return self.data

    def fetchall(self):
        return self.data


class Reader:
    def __init__(self, highest, *, fail=False):
        self.highest = highest
        self.fail = fail
        self.queries = []

    def execute(self, sql):
        self.queries.append(sql)
        if "overflow_present" in sql:
            return Result({"overflow_present":False})
        if "MAX(id)" in sql:
            if self.fail:
                raise RuntimeError("connection details must not be logged")
            return Result({"highest_id": self.highest})
        if "COUNT(*) AS matched" in sql:
            return Result({"matched": 1})
        if "COUNT(*) AS n" in sql:
            return Result({"n": 1})
        if "ORDER BY e.id DESC LIMIT 10" in sql:
            return Result([])
        raise AssertionError("unexpected query")


@pytest.mark.parametrize("highest,status,remaining", [
    (0, "CAPACITY_NORMAL", 5000),
    (1610, "CAPACITY_NORMAL", 3390),
    (3999, "CAPACITY_NORMAL", 1001),
    (4000, "CAPACITY_WARNING", 1000),
    (4749, "CAPACITY_WARNING", 251),
    (4750, "CAPACITY_CRITICAL", 250),
    (4999, "CAPACITY_CRITICAL", 1),
    (5000, "CAPACITY_REACHED", 0),
])
def test_immutable_evidence_id_headroom(highest, status, remaining):
    conn = Reader(highest)
    report = read_evidence_capacity(conn)
    assert report == {
        "status": status, "highest_id": highest, "max_id": RESEARCH_EVIDENCE_MAX_ID,
        "remaining_id_slots": remaining, "execution_impact": "NONE",
    }
    assert len(conn.queries) == 2
    assert all(query.strip().startswith("SELECT") for query in conn.queries)
    assert "MAX(id)" in conn.queries[1]
    assert all("DELETE" not in query and "UPDATE" not in query for query in conn.queries)


@pytest.mark.parametrize("value", [None, -1, 5001, 0.1, "123", True])
def test_invalid_capacity_is_unavailable_not_inferred(value):
    result = read_evidence_capacity(Reader(value))
    assert result["status"] == "UNAVAILABLE"
    assert "remaining_id_slots" not in result


def test_ledger_read_failure_keeps_brain_receipt_usable_and_hides_error():
    conn = Reader(4000, fail=True)
    receipt = committed_receipt(conn)
    assert receipt["expected"] == 1 and receipt["matched"] == 1
    assert receipt["unmatched"] == 0
    assert receipt["evidence_capacity"] == {
        "status": "UNAVAILABLE", "reason_class": "RuntimeError",
        "execution_impact": "NONE",
    }
    assert "connection details" not in repr(receipt)


def test_current_brain_receipt_includes_capacity_without_trade_promotion():
    conn = Reader(1610)
    receipt = committed_receipt(conn)
    assert receipt["evidence_capacity"]["remaining_id_slots"] == 3390
    assert receipt["trade_outcomes"] == "NOT_INFERRED"
    assert receipt["profitability"] == "NOT_EVALUATED"
    assert receipt["execution_impact"] == "NONE"
    assert len(conn.queries) == 7
    assert all(sql.strip().startswith("SELECT") for sql in conn.queries)
