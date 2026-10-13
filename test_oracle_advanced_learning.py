from datetime import datetime, timedelta, timezone
from pathlib import Path
import oracle_advanced_learning as a


def test_adaptive_weight_decays_with_age_and_drift():
    fresh=a.adaptive_evidence_weight(100,0,0)
    old=a.adaptive_evidence_weight(100,60,0)
    drifted=a.adaptive_evidence_weight(100,0,0.8)
    assert fresh > old
    assert fresh > drifted
    assert 0 <= old <= 1


def test_replay_is_strictly_point_in_time():
    source=Path("oracle_advanced_learning.py").read_text(encoding="utf-8")
    assert "event_time::timestamptz <= %s::timestamptz" in source
    assert "lineage_hash" in source
    assert "sha256" in source


def test_walk_forward_is_time_ordered_and_research_only():
    source=Path("oracle_advanced_learning.py").read_text(encoding="utf-8")
    assert "ORDER BY exit_time ASC" in source
    assert "split\":\"70/30_time_ordered" in source
    assert 'state="shadow"' in source
    lower=source.lower()
    for forbidden in ("submit_order(","place_order(","live_trading_armed=true","enable_broker_submission=true"):
        assert forbidden not in lower



def _episode_rows(episodes: int, trades_per_episode: int) -> list[dict]:
    base=datetime(2026,10,1,tzinfo=timezone.utc)
    out=[]
    for episode in range(episodes):
        start=base+timedelta(hours=episode*2)
        for trade in range(trades_per_episode):
            out.append({
                "entry_time":start,
                "exit_time":start+timedelta(minutes=20+trade),
                "net_pnl":1.0,
                "return_pct":0.10,
            })
    return out


class _RowsResult:
    def __init__(self, rows):
        self._rows=rows
    def fetchall(self):
        return self._rows


class _ChallengerConn:
    def __init__(self, rows):
        self.rows=rows
        self.insert_params=[]
    def execute(self, query, params=None):
        if "SELECT DISTINCT strategy,regime" in query:
            return _RowsResult([{"strategy":"test_strategy","regime":"test_regime"}])
        if "SELECT entry_time,exit_time,net_pnl,return_pct" in query:
            return _RowsResult(self.rows)
        if "INSERT INTO oracle_challenger_validation" in query:
            self.insert_params.append(params)
            return _RowsResult([])
        raise AssertionError(query)


def test_cluster_bootstrap_counts_independent_market_episodes():
    metrics=a._cluster_validation_metrics(
        _episode_rows(30,2),
        market="crypto",
        candidate_key="test:positive",
        bootstrap_draws=200,
    )
    assert metrics["trades"] == 60
    assert metrics["episodes"] == 30
    assert metrics["bootstrap_lower_expectancy"] > 0
    assert metrics["episode_drawdown_pct"] == 0
    assert metrics["tail_episode_return_pct"] > 0
    assert metrics["risk_metrics_ready"] is True


def test_correlated_trade_burst_cannot_qualify_as_many_independent_confirmations():
    conn=_ChallengerConn(_episode_rows(34,6))
    result=a.walk_forward_challengers(conn,"crypto")
    assert result["shadow_qualified"] == 0
    assert conn.insert_params
    params=conn.insert_params[0]
    assert params[8] == "research"
    evidence=__import__("json").loads(params[9])
    assert evidence["test_samples"] >= a.MIN_CLUSTER_TRADES
    assert evidence["episode_count"] < a.MIN_CLUSTER_EPISODES
    assert evidence["gates"]["min_trades"] is True
    assert evidence["gates"]["min_episodes"] is False


def test_positive_evidence_across_enough_episodes_can_reach_shadow_only():
    conn=_ChallengerConn(_episode_rows(100,2))
    result=a.walk_forward_challengers(conn,"crypto")
    assert result["shadow_qualified"] == 1
    params=conn.insert_params[0]
    assert params[8] == "shadow"
    evidence=__import__("json").loads(params[9])
    assert evidence["episode_count"] >= a.MIN_CLUSTER_EPISODES
    assert all(evidence["gates"].values())
    assert evidence["split"] == "70/30_time_ordered_clustered"
    assert params[6] is None  # No arbitrary 999 profit-factor claim without losses.
    assert evidence["profit_factor_status"] == "no_losses"
    assert evidence["accounting_version"] == "exact_round_trip_v1"
    assert evidence["cost_provenance"] == "exact_lot"


def test_walk_forward_splits_whole_episodes_before_train_test_partition():
    conn = _ChallengerConn(_episode_rows(2, 30))
    result = a.walk_forward_challengers(conn, "crypto")
    assert result["shadow_qualified"] == 0
    params = conn.insert_params[0]
    evidence = __import__("json").loads(params[9])
    assert evidence["train_samples"] == 30
    assert evidence["test_samples"] == 30
    assert evidence["train_episode_count"] == 1
    assert evidence["episode_count"] == 1
    assert params[3] > params[2] + timedelta(seconds=a.CLUSTER_GAP_SECONDS)


def test_missing_or_reversed_entry_intervals_cannot_count_as_independent_episodes():
    base = datetime(2026, 10, 1, tzinfo=timezone.utc)
    rows = [
        {"exit_time": base, "net_pnl": 1, "return_pct": 0.1},
        {"entry_time": base, "exit_time": base - timedelta(minutes=1),
         "net_pnl": 1, "return_pct": 0.1},
        {"entry_time": base, "exit_time": base + timedelta(minutes=1),
         "net_pnl": float("nan"), "return_pct": 0.1},
    ]
    metrics = a._cluster_validation_metrics(rows, market="crypto", candidate_key="invalid")
    assert metrics["episodes"] == metrics["trades"] == 0
    assert metrics["bootstrap_lower_expectancy"] is None
    assert metrics["risk_metrics_ready"] is False


class _EmptyConn:
    def __init__(self):
        self.queries = []

    def execute(self, query, params=()):
        self.queries.append(query)
        return self

    def fetchall(self):
        return []

    def fetchone(self):
        return {}


def test_all_advanced_outcome_readers_require_complete_round_trip_costs():
    conn = _EmptyConn()
    a.refresh_setup_attribution(conn, market="crypto")
    a.classify_failures(conn, market="crypto")
    a.exit_diagnostics(conn, market="crypto")
    concentration = a.portfolio_concentration(conn, market="crypto")
    a.walk_forward_challengers(conn, "crypto")
    reads = [sql for sql in conn.queries if "FROM oracle_brain_episodes" in sql]
    assert len(reads) == 5
    for sql in reads:
        assert "provenance_status='exact'" in sql
        assert "outcome_snapshot->>'accounting_version'='exact_round_trip_v1'" in sql
        assert "outcome_snapshot->>'cost_provenance'='exact_lot'" in sql
    assert concentration["top3_concentration"] is None


def test_walk_forward_cohort_and_sample_reads_both_require_complete_costs():
    class Capture(_ChallengerConn):
        def __init__(self, rows):
            super().__init__(rows)
            self.queries = []

        def execute(self, query, params=None):
            self.queries.append(query)
            return super().execute(query, params)

    conn = Capture(_episode_rows(30, 2))
    a.walk_forward_challengers(conn, "crypto")
    reads = [sql for sql in conn.queries if "FROM oracle_brain_episodes" in sql]
    assert len(reads) == 2
    assert all("outcome_snapshot->>'accounting_version'='exact_round_trip_v1'" in sql for sql in reads)
    assert all("outcome_snapshot->>'cost_provenance'='exact_lot'" in sql for sql in reads)


def test_walk_forward_drawdown_is_independent_of_simultaneous_exit_row_order():
    rows = _episode_rows(100, 2)
    for index, row in enumerate(rows):
        row["exit_time"] = row["entry_time"] + timedelta(minutes=20)
        row["net_pnl"] = 3 if index % 2 == 0 else -5
    forward = _ChallengerConn(rows)
    reversed_order = _ChallengerConn(list(reversed(rows)))
    a.walk_forward_challengers(forward, "crypto")
    a.walk_forward_challengers(reversed_order, "crypto")
    assert forward.insert_params[0][7] == reversed_order.insert_params[0][7] == 60

def test_advanced_schema_has_no_execution_authority():
    migration=Path("migrations/20260922_oracle_advanced_learning.sql").read_text(encoding="utf-8")
    assert migration.count("CHECK (execution_impact='NONE')") == 5


def test_compact_replay_snapshot_keeps_lineage_without_large_payload_copies():
    decision={"id":42,"approved":True,"payload":{"blob":"x"*500000}}
    observations=[
        {
            "event_key":"obs-1",
            "source_table":"signals",
            "observation_type":"signal",
            "event_time":"2026-09-24T00:00:00+00:00",
            "payload":{"blob":"y"*500000},
        }
    ]
    snapshot,digest=a._compact_replay_snapshot(decision,observations)
    json_mod=__import__("json")
    hashlib_mod=__import__("hashlib")
    encoded=json_mod.dumps(snapshot,sort_keys=True,separators=(",",":"))
    legacy={
        "decision_payload":decision["payload"],
        "approved":True,
        "observations":observations,
    }
    legacy_raw=json_mod.dumps(legacy,default=str,sort_keys=True,separators=(",",":"))
    assert "decision_payload" not in snapshot
    assert "observations" not in snapshot
    assert snapshot["decision_ref"]["decision_id"] == 42
    assert snapshot["decision_ref"]["payload_sha256"]
    assert snapshot["observation_refs"][0]["event_key"] == "obs-1"
    assert snapshot["observation_refs"][0]["payload_sha256"]
    assert "blob" not in encoded
    assert len(encoded) < 5000
    assert digest == hashlib_mod.sha256(legacy_raw.encode()).hexdigest()
    assert len(digest) == 64


def test_replay_source_still_uses_canonical_audit_and_observation_tables():
    source=Path("oracle_advanced_learning.py").read_text(encoding="utf-8")
    assert "FROM oracle_decision_audit d" in source
    assert "FROM oracle_brain_observations" in source
    assert "payload_sha256" in source
