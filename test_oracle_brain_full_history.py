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
    sql, params = calls[-1]
    assert "LIMIT" not in sql.upper()
    assert params == ("crypto",)


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
    sql, params = calls[-1]
    assert "LIMIT %s" in sql
    assert params == ("crypto", 500)
