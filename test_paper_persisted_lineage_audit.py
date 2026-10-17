"""Persisted provenance audit must not infer decision consumption."""
from unittest.mock import MagicMock
import pytest
from paper_persisted_lineage_audit import summarize_persisted_lineage


def test_persisted_audit_is_read_only_and_replays_are_not_consumption():
    conn = MagicMock()
    conn.execute.return_value.fetchone.side_effect = [
        {"ro": "on"},
        {"total": 5, "linked": 2, "isolated": 3, "invalid_fee": 1},
        {"total": 9},
        {"total": 4},
    ]
    result = summarize_persisted_lineage(conn)
    assert result["paper_fills"]["isolated"] == 3
    assert result["exact_brain_episodes"] == 4
    assert result["retrospective_decision_replays"] == 9
    assert result["verified_decision_consumption"] == "UNAVAILABLE"
    assert result["execution_impact"] == "NONE"
    assert result["verification_scope"] == "aggregate_counts_only"
    assert result["fill_link_scope"] == "recorded_canonical_trade_id_only"
    assert result["brain_episode_scope"] == "persisted_provenance_labels_only"
    sql = [call.args[0].strip().upper() for call in conn.execute.call_args_list]
    assert all(statement.startswith(("SELECT", "SET")) for statement in sql)
    assert all(not statement.startswith(("INSERT", "UPDATE", "DELETE")) for statement in sql)


def test_write_enabled_connection_fails_closed():
    conn = MagicMock()
    conn.execute.return_value.fetchone.return_value = {"ro": "off"}
    with pytest.raises(RuntimeError):
        summarize_persisted_lineage(conn)


def test_invalid_market_rejected_before_database_access():
    conn = MagicMock()
    with pytest.raises(ValueError):
        summarize_persisted_lineage(conn, market="live")
    conn.execute.assert_not_called()
