from pathlib import Path

import oracle_learning_validation as v


def test_validation_layer_has_no_execution_authority():
    source=Path("oracle_learning_validation.py").read_text(encoding="utf-8").lower()
    for forbidden in ("submit_order(","place_order(","broker_submission=true","live_trading_armed=true"):
        assert forbidden not in source
    migration=Path("migrations/20260922_oracle_learning_validation.sql").read_text(encoding="utf-8")
    assert migration.count("execution_impact TEXT NOT NULL DEFAULT 'NONE'") == 4
    assert migration.count("CHECK (execution_impact='NONE')") == 4


def test_deep_number_normalizes_nested_evidence():
    payload={"decision":{"features":{"probability_of_profit":72,"expected_edge_pct":0.42}}}
    assert v._deep_number(payload,("probability_of_profit",)) == 72.0
    assert v._deep_number(payload,("expected_edge_pct",)) == 0.42


class Result:
    def __init__(self,one=None,rows=None,rowcount=1):
        self.one=one
        self.rows=rows or []
        self.rowcount=rowcount
    def fetchone(self): return self.one
    def fetchall(self): return self.rows


class CounterfactualConn:
    def __init__(self): self.insert=None
    def execute(self,sql,params=()):
        compact=" ".join(sql.split())
        if "FROM oracle_decision_audit d" in compact:
            return Result(rows=[{"id":7,"market":"crypto","symbol":"BTC-USD","payload":{"price":100.0},"created_at":"2026-09-22T10:00:00+00:00"}])
        if "FROM signals" in compact and "created_at::timestamptz >=" in compact:
            return Result(one={"id":9,"price":102.0,"created_at":"2026-09-22T11:00:00+00:00"})
        if "INSERT INTO oracle_counterfactual_outcomes" in compact:
            self.insert=params
            return Result()
        raise AssertionError(compact)


def test_rejected_decision_that_rises_is_missed_winner():
    conn=CounterfactualConn()
    result=v.sync_counterfactuals(conn)
    assert result["evaluated"] == 1
    assert result["missed_winners"] == 1
    assert result["avoided_losses"] == 0
    assert conn.insert[8] == "missed_winner"
    assert round(conn.insert[7], 6) == 2.0


def test_promotion_evidence_is_research_only():
    source=Path("oracle_learning_validation.py").read_text(encoding="utf-8")
    assert "PROMOTION_MIN_SAMPLES" in source
    assert "PROMOTION_MIN_PROFIT_FACTOR" in source
    assert "PROMOTION_MAX_CALIBRATION_ERROR" in source
    assert "PROMOTION_MAX_DRAWDOWN_PCT" in source
    assert "execution_impact" in source


class ValidationConn:
    def __init__(self, *, stats=None, rows=None):
        self.stats = stats or {}
        self.rows = rows or []
        self.queries = []
        self.promotion = None

    def execute(self, sql, params=()):
        self.queries.append((sql, params))
        if "INSERT INTO oracle_paper_promotion_evidence" in sql:
            self.promotion = params
            return Result()
        if "AVG(net_pnl) AS expectancy" in sql:
            return Result(one=self.stats)
        if "SELECT market,feature_snapshot,return_pct" in sql:
            return Result(rows=self.rows)
        if "SELECT exit_time,net_pnl FROM oracle_brain_episodes" in sql:
            return Result(rows=[{"net_pnl": x["net_pnl"], "exit_time": x.get("exit_time")} for x in self.rows])
        return Result()


def test_all_quantitative_validation_reads_require_complete_round_trip_costs():
    conn = ValidationConn()
    v.refresh_calibration(conn, market="crypto")
    v.refresh_weaknesses(conn, market="crypto")
    v.evaluate_promotion_evidence(conn, "crypto")
    reads = [sql for sql, _ in conn.queries if "FROM oracle_brain_episodes" in sql]
    assert len(reads) == 5
    for sql in reads:
        assert "provenance_status='exact'" in sql
        assert "outcome_snapshot->>'accounting_version'='exact_round_trip_v1'" in sql
        assert "outcome_snapshot->>'cost_provenance'='exact_lot'" in sql
        assert "fees >= 0" in sql
        assert "net_pnl < 'Infinity'::double precision" in sql
        assert "entry_time <= exit_time AND exit_time <= NOW()" in sql


def test_missing_post_cost_evidence_is_unavailable_and_cannot_qualify():
    conn = ValidationConn()
    result = v.evaluate_promotion_evidence(conn, "crypto")
    assert result["samples"] == 0
    assert result["eligible"] is False
    for field in ("expectancy", "profit_factor", "calibration_error", "max_drawdown"):
        assert result[field] is None
    assert "post_cost_expectancy_unavailable" in result["reasons"]
    assert "calibration_unavailable" in result["reasons"]
    assert conn.promotion[3:7] == (None, None, None, None)
    assert result["execution_impact"] == "NONE"


def test_promotion_calibration_uses_current_eligible_outcomes_not_legacy_cache():
    rows = [{"market": "crypto", "feature_snapshot": {"probability": 0.6},
             "return_pct": 1 if i < 12 else -1, "net_pnl": 1 if i < 12 else -1,
             "exit_time": "2026-10-01T00:00:00+00:00"}
            for i in range(20)]
    conn = ValidationConn(stats={"samples": 20, "expectancy": 0.2,
                                "gross_win": 12, "gross_loss": 8}, rows=rows)
    result = v.evaluate_promotion_evidence(conn, "crypto")
    assert abs(result["calibration_error"]) < 1e-12
    assert result["profit_factor"] == 1.5
    assert not any("FROM oracle_calibration_buckets" in sql for sql, _ in conn.queries)
    assert result["eligible"] is False


def test_calibration_does_not_convert_unknown_or_nonfinite_returns_to_losses():
    rows = [{"market": "crypto", "feature_snapshot": {"probability": 0.6},
             "return_pct": value} for value in (None, float("nan"), float("inf"), "invalid")]
    assert v._calibration_buckets(rows) == {}


def test_mature_flat_cohort_keeps_weakness_veto_with_unavailable_profit_factor():
    class FlatConn:
        def __init__(self):
            self.insert = None

        def execute(self, sql, params=()):
            if "FROM oracle_brain_episodes" in sql:
                return Result(rows=[{"market": "crypto", "strategy": "flat", "regime": "flat",
                                     "samples": v.WEAKNESS_MIN_SAMPLES, "expectancy": 0,
                                     "gross_win": 0, "gross_loss": 0}])
            self.insert = params
            return Result()

    conn = FlatConn()
    assert v.refresh_weaknesses(conn, market="crypto")["negative_mature"] == 1
    assert conn.insert[6] is None
    assert conn.insert[9] == "negative"


def test_chronological_drawdown_settles_equal_exit_timestamps_together():
    rows = [{"exit_time": "2026-10-01T00:00:00Z", "net_pnl": 3},
            {"exit_time": "2026-10-01T00:00:00+00:00", "net_pnl": -5},
            {"exit_time": "2026-10-02T00:00:00+00:00", "net_pnl": 1}]
    assert v._chronological_drawdown(rows) == 2
    assert v._chronological_drawdown(list(reversed(rows))) == 2
    assert v._chronological_drawdown([]) is None
