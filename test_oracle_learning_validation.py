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
