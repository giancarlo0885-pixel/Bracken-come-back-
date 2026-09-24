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
    encoded=__import__("json").dumps(snapshot,sort_keys=True,separators=(",",":"))
    assert "decision_payload" not in snapshot
    assert "observations" not in snapshot
    assert snapshot["decision_ref"]["decision_id"] == 42
    assert snapshot["decision_ref"]["payload_sha256"]
    assert snapshot["observation_refs"][0]["event_key"] == "obs-1"
    assert snapshot["observation_refs"][0]["payload_sha256"]
    assert "blob" not in encoded
    assert len(encoded) < 5000
    assert len(digest) == 64


def test_replay_source_still_uses_canonical_audit_and_observation_tables():
    source=Path("oracle_advanced_learning.py").read_text(encoding="utf-8")
    assert "FROM oracle_decision_audit d" in source
    assert "FROM oracle_brain_observations" in source
    assert "payload_sha256" in source
