"""Smoke-test real PostgreSQL Kelly schema and forward-only source query."""
import os
import pytest


@pytest.mark.skipif(not os.getenv("DATABASE_URL"), reason="PostgreSQL CI service required")
def test_postgres_kelly_schema_and_immutable_forward_epoch(monkeypatch):
    monkeypatch.setenv("PAPER_AUTONOMOUS_LEARNING", "true")
    monkeypatch.setenv("EXECUTION_MODE", "paper")
    monkeypatch.setenv("ENABLE_BROKER_SUBMISSION", "false")
    monkeypatch.setenv("LIVE_TRADING_ARMED", "false")

    from database import connect
    from paper_aeve_generation_controller import ensure_schema as ensure_aeve_schema
    from paper_kelly_challenger_shadow import _VERSION, ensure_schema, evaluate_new_closes

    ensure_aeve_schema()
    ensure_schema()
    with connect() as conn:
        first = conn.execute(
            "SELECT started_at FROM paper_kelly_shadow_epoch WHERE version=%s",
            (_VERSION,),
        ).fetchone()
        assert first and first["started_at"]
        relation = conn.execute(
            "SELECT to_regclass('paper_kelly_shadow_results') AS name"
        ).fetchone()
        assert relation["name"] is not None
        assert conn.execute("SELECT to_regclass('paper_kelly_shadow_exclusions') AS name").fetchone()["name"] is not None

    # Calling schema setup again must never reset the forward enrollment epoch.
    ensure_schema()
    with connect() as conn:
        second = conn.execute(
            "SELECT started_at FROM paper_kelly_shadow_epoch WHERE version=%s",
            (_VERSION,),
        ).fetchone()
    assert second["started_at"] == first["started_at"]
    assert isinstance(evaluate_new_closes(limit=1), int)
