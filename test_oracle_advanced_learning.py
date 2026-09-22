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
