import database


def test_remaining_ephemeral_tables_have_bounded_retention():
    expected = {
        "recommendations": 12000,
        "recommendation_evidence": 24000,
        "strategy_signals": 12000,
        "forecast_results": 12000,
    }
    for table, keep_rows in expected.items():
        policy = database.DATABASE_RETENTION_POLICIES[table]
        assert policy["keep_rows"] == keep_rows
        assert policy["batch_size"] == database.DATABASE_RETENTION_BATCH_SIZE
        assert "append-only analytical/ephemeral" in policy["classification"]
        assert table not in database.CANONICAL_PROTECTED_TABLES
        assert f"keep newest {keep_rows} rows" in database.DATABASE_TABLE_GROWTH_AUDIT[table]["retention"]


def test_ephemeral_retention_does_not_expand_into_brain_or_accounting_tables():
    protected = {
        "trades",
        "trade_ledger",
        "position_lots",
        "executions",
        "oracle_brain_entries",
        "oracle_brain_sources",
        "oracle_brain_episodes",
        "oracle_brain_links",
        "oracle_brain_learning_state",
        "oracle_decision_replays",
    }
    assert protected.isdisjoint(database.DATABASE_RETENTION_POLICIES)
