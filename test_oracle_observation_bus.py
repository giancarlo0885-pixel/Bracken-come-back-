from pathlib import Path

import oracle_observation_bus as bus


class Result:
    def __init__(self, rows=None, rowcount=1):
        self.rows = rows or []
        self.rowcount = rowcount

    def fetchone(self):
        return self.rows[0] if self.rows else None

    def fetchall(self):
        return self.rows


class FakeConn:
    def __init__(self):
        self.inserts = []
        self.source_rows = {
            "signals": [{"id": 1, "market": "crypto", "symbol": "BTC-USD", "created_at": "2026-09-22T10:00:00+00:00", "details": {"price": 1}}],
            "oracle_decision_audit": [],
            "global_decision_events": [],
            "intelligence_events": [],
        }

    def execute(self, sql, params=()):
        normalized = " ".join(sql.split())
        if "COALESCE(MAX(source_id),0)" in normalized:
            return Result([{"last_id": 0}])
        if normalized.startswith("SELECT *,"):
            table = next(name for name in self.source_rows if f"FROM {name}" in normalized)
            rows = []
            for row in self.source_rows[table]:
                copy = dict(row)
                copy["_observation_event_time"] = row.get("event_time") or row.get("created_at")
                rows.append(copy)
            return Result(rows)
        if "INSERT INTO oracle_brain_observations" in normalized:
            self.inserts.append((normalized, params))
            return Result(rowcount=1)
        if "FROM oracle_brain_observations" in normalized:
            return Result([])
        raise AssertionError(normalized)


def test_observation_bus_is_append_only_research_evidence():
    conn = FakeConn()
    result = bus.sync_observations(conn)

    assert result["inserted"] == 1
    assert result["execution_impact"] == "NONE"
    sql, params = conn.inserts[0]
    assert "ON CONFLICT(event_key) DO NOTHING" in sql
    assert params[3] == "signal"
    assert params[4] == "crypto"
    assert params[5] == "BTC-USD"
    assert "NONE" in sql


def test_point_in_time_retrieval_excludes_future_observations():
    source = Path("oracle_observation_bus.py").read_text(encoding="utf-8")
    assert "event_time::timestamptz <= %s::timestamptz" in source
    assert "ORDER BY event_time::timestamptz DESC" in source


def test_observation_bus_has_no_execution_authority():
    source = Path("oracle_observation_bus.py").read_text(encoding="utf-8").lower()
    forbidden = ("submit_order(", "place_order(", "live_trading_armed", "enable_broker_submission")
    assert all(item not in source for item in forbidden)
    migration = Path("migrations/20260922_oracle_observation_bus.sql").read_text(encoding="utf-8")
    assert "CHECK (execution_impact = 'NONE')" in migration
