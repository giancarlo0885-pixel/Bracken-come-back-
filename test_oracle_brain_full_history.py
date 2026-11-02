from oracle_brain_feedback import outcome_memory_for_signal


def test_outcome_memory_defaults_to_full_retained_history(monkeypatch):
    calls = []

    def fake_rows(sql, params):
        calls.append((sql, params))
        return []

    import database
    import oracle_brain

    monkeypatch.setattr(database, "rows", fake_rows)
    monkeypatch.setattr(oracle_brain, "runtime_safety_state", lambda: {"safe_research_boundary": True})
    outcome_memory_for_signal(
        {"symbol": "TEST-USD", "regime": "bull", "strategy": "green-core-full-history-test"},
        market="crypto",
    )
    episode_calls = [(sql, params) for sql, params in calls if "FROM oracle_brain_episodes" in sql]
    assert episode_calls
    sql, params = episode_calls[-1]
    assert "LIMIT" not in sql.upper()
    assert params[0] == "crypto"
    assert len(params) == 2
    assert params[1].tzinfo is not None
    assert "exit_time < %s" in sql


def test_outcome_memory_can_be_bounded_for_diagnostics(monkeypatch):
    calls = []

    def fake_rows(sql, params):
        calls.append((sql, params))
        return []

    import database
    import oracle_brain

    monkeypatch.setattr(database, "rows", fake_rows)
    monkeypatch.setattr(oracle_brain, "runtime_safety_state", lambda: {"safe_research_boundary": True})
    outcome_memory_for_signal(
        {"symbol": "BOUND-USD", "regime": "bull", "strategy": "green-core-bounded-history-test"},
        market="crypto",
        max_rows=500,
    )
    episode_calls = [(sql, params) for sql, params in calls if "FROM oracle_brain_episodes" in sql]
    assert episode_calls
    sql, params = episode_calls[-1]
    assert "LIMIT %s" in sql
    assert params[0] == "crypto"
    assert len(params) == 3
    assert params[1].tzinfo is not None
    assert params[2] == 500
    assert "exit_time < %s" in sql
