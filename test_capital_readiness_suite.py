from __future__ import annotations

from datetime import datetime, timezone
import os

import numpy as np
import pandas as pd
import pytest

from accounting_invariants import equity_equation
from broker_order_journal import PersistentOrderJournal, normalize_remote_state
from broker_reconciliation import reconcile_persistent_journal
from capital_model_governance import assess_model_evidence
from oracle_readiness import determine_overall_status
from shadow_broker import record_shadow_order
from walk_forward_validation import evaluate_forecast_walk_forward, temporal_leakage_probe


def _history(length: int = 220) -> pd.DataFrame:
    rng = np.random.default_rng(42)
    returns = rng.normal(0.0007, 0.01, length)
    close = 100.0 * np.exp(np.cumsum(returns))
    open_ = np.r_[close[0], close[:-1]]
    high = np.maximum(open_, close) * 1.002
    low = np.minimum(open_, close) * 0.998
    volume = np.full(length, 1_000_000.0)
    return pd.DataFrame(
        {"Open": open_, "High": high, "Low": low, "Close": close, "Volume": volume},
        index=pd.date_range("2025-01-01", periods=length, freq="D", tz="UTC"),
    )


def test_future_mutation_cannot_change_past_forecast():
    history = _history()
    result = temporal_leakage_probe(history, decision_position=150, horizon_bars=5)
    assert result["ok"] is True
    assert all(value <= 1e-12 for value in result["differences"].values())


def test_walk_forward_is_strictly_chronological_and_has_baselines():
    result = evaluate_forecast_walk_forward("SPY", _history(), horizon_bars=5, minimum_history_bars=60)
    assert result.sample_count > 0
    assert result.leakage_checks["strict_ordering"] is True
    assert result.leakage_checks["future_mutation_probe"]["ok"] is True
    assert {"coin_flip", "base_rate", "previous_direction", "momentum_5"}.issubset(result.benchmarks)


def test_model_governance_requires_multi_symbol_passing_evidence(monkeypatch):
    monkeypatch.setenv("CAPITAL_MIN_MODEL_PASS_RUNS", "2")
    monkeypatch.setenv("CAPITAL_MIN_MODEL_SYMBOLS", "2")
    monkeypatch.setenv("CAPITAL_MIN_BRIER_SKILL", "0.02")
    monkeypatch.setenv("CAPITAL_MAX_ECE", "0.12")
    monkeypatch.setenv("CAPITAL_MIN_DIRECTIONAL_ACCURACY", "0.52")
    now = datetime.now(timezone.utc).isoformat()
    good_metrics = {
        "brier_skill_score": 0.10,
        "expected_calibration_error": 0.05,
        "directional_accuracy": 0.60,
        "beats_all_baselines": True,
    }
    leakage = {"strict_ordering": True, "future_mutation_probe": {"ok": True}}
    evidence = [
        {"run_id": "a", "model": "m", "model_version": "v", "symbol": "AAA", "status": "PASS", "metrics": good_metrics, "leakage_checks": leakage, "created_at": now},
        {"run_id": "b", "model": "m", "model_version": "v", "symbol": "BBB", "status": "PASS", "metrics": good_metrics, "leakage_checks": leakage, "created_at": now},
    ]
    assessment = assess_model_evidence("m", "v", evidence, current_status="shadow")
    assert assessment.eligible_for_approval is True
    assert assessment.recommended_status == "approved"


def test_model_governance_does_not_promote_failed_baseline_evidence(monkeypatch):
    monkeypatch.setenv("CAPITAL_MIN_MODEL_PASS_RUNS", "2")
    monkeypatch.setenv("CAPITAL_MIN_MODEL_SYMBOLS", "2")
    now = datetime.now(timezone.utc).isoformat()
    metrics = {
        "brier_skill_score": 0.10,
        "expected_calibration_error": 0.05,
        "directional_accuracy": 0.60,
        "beats_all_baselines": False,
    }
    leakage = {"strict_ordering": True, "future_mutation_probe": {"ok": True}}
    evidence = [
        {"model": "m", "model_version": "v", "symbol": symbol, "status": "PASS", "metrics": metrics, "leakage_checks": leakage, "created_at": now}
        for symbol in ("AAA", "BBB")
    ]
    assessment = assess_model_evidence("m", "v", evidence, current_status="shadow")
    assert assessment.eligible_for_approval is False
    assert assessment.recommended_status == "shadow"


def test_readiness_state_machine_never_skips_shadow_or_paper_evidence():
    base = {
        "database": {"ok": True},
        "safety": {"ok": True},
        "accounting": {"ok": True},
        "data_integrity": {"ok": True},
        "models": {"ok": False},
        "durable_order_journal": {"ok": True},
        "reconciliation": {"ok": False},
        "broker": {"connectivity_ok": True, "funding_ok": False},
        "shadow_forward": {"ok": False},
        "paper_lifecycle": {"ok": False},
    }
    assert determine_overall_status(base) == "SHADOW_READY"
    base["models"]["ok"] = True
    base["reconciliation"]["ok"] = True
    base["broker"]["funding_ok"] = True
    assert determine_overall_status(base) == "SHADOW_READY"
    base["shadow_forward"]["ok"] = True
    assert determine_overall_status(base) == "SHADOW_READY"
    base["paper_lifecycle"]["ok"] = True
    assert determine_overall_status(base) == "MANUAL_LIVE_CANDIDATE"


def test_equity_equation_accounts_for_debt_and_interest():
    assert equity_equation(cash=1000, positions_value=500, margin_debt=100, margin_interest=5) == 1395


def test_remote_order_state_normalization_fails_unknown_closed():
    assert normalize_remote_state("filled") == "FILLED"
    assert normalize_remote_state("cancelled") == "CANCELED"
    assert normalize_remote_state("mystery") == "UNKNOWN_RECONCILE_REQUIRED"


@pytest.mark.skipif(not os.getenv("DATABASE_URL"), reason="PostgreSQL integration only")
def test_postgres_persistent_order_journal_survives_restart_and_reconciles():
    from migrations import run_migrations
    from database import connect

    run_migrations()
    with connect() as conn:
        conn.execute("DELETE FROM broker_reconciliation_runs")
        conn.execute("DELETE FROM broker_order_journal")

    journal = PersistentOrderJournal()
    journal.create(
        client_order_id="capital-readiness-restart-1",
        symbol="BTC-USD",
        side="BUY",
        quantity=0.001,
        notional=100.0,
    )
    journal.transition("capital-readiness-restart-1", "SUBMITTING")
    journal.mark_submit_timeout("capital-readiness-restart-1")

    restarted = PersistentOrderJournal()
    persisted = restarted.get("capital-readiness-restart-1")
    assert persisted is not None
    assert persisted["state"] == "UNKNOWN_RECONCILE_REQUIRED"

    result = reconcile_persistent_journal(
        restarted,
        [{"client_order_id": "capital-readiness-restart-1", "id": "broker-1", "state": "filled", "filled_quantity": "0.001", "average_price": "100000"}],
    )
    assert result["status"] == "PASS"
    assert restarted.get("capital-readiness-restart-1")["state"] == "FILLED"


@pytest.mark.skipif(not os.getenv("DATABASE_URL"), reason="PostgreSQL integration only")
def test_postgres_presubmission_order_does_not_become_ambiguous_on_restart():
    from migrations import run_migrations
    from database import connect

    run_migrations()
    with connect() as conn:
        conn.execute("DELETE FROM broker_order_journal WHERE client_order_id='capital-readiness-local-only-1'")
    journal = PersistentOrderJournal()
    journal.create(client_order_id="capital-readiness-local-only-1", symbol="BTC-USD", side="BUY", quantity=0.001, notional=100.0)
    result = reconcile_persistent_journal(journal, [])
    assert result["status"] == "PASS"
    assert result["local_only_pre_submission"] == 1
    assert journal.get("capital-readiness-local-only-1")["state"] == "CREATED"


@pytest.mark.skipif(not os.getenv("DATABASE_URL"), reason="PostgreSQL integration only")
def test_postgres_order_journal_rejects_changed_idempotent_intent():
    from migrations import run_migrations
    from database import connect

    run_migrations()
    with connect() as conn:
        conn.execute("DELETE FROM broker_order_journal WHERE client_order_id='capital-readiness-duplicate-1'")

    journal = PersistentOrderJournal()
    journal.create(client_order_id="capital-readiness-duplicate-1", symbol="BTC-USD", side="BUY", quantity=0.001, notional=100.0)
    with pytest.raises(RuntimeError):
        journal.create(client_order_id="capital-readiness-duplicate-1", symbol="ETH-USD", side="BUY", quantity=0.001, notional=100.0)


@pytest.mark.skipif(not os.getenv("DATABASE_URL"), reason="PostgreSQL integration only")
def test_postgres_shadow_fill_identity_is_unique():
    from migrations import run_migrations
    from database import connect

    run_migrations()
    with connect() as conn:
        conn.execute("DELETE FROM shadow_broker_orders WHERE paper_fill_id='paper-fill-capital-ready-1'")

    quote = {"symbol": "BTC-USD", "bid": "99990", "ask": "100010", "timestamp": datetime.now(timezone.utc).isoformat()}
    record_shadow_order(
        paper_fill_id="paper-fill-capital-ready-1",
        symbol="BTC-USD",
        side="BUY",
        quantity=0.001,
        oracle_reference_price=100000,
        paper_fill_price=100005,
        broker_quote=quote,
    )
    with pytest.raises(Exception):
        record_shadow_order(
            paper_fill_id="paper-fill-capital-ready-1",
            symbol="BTC-USD",
            side="BUY",
            quantity=0.001,
            oracle_reference_price=100000,
            paper_fill_price=100005,
            broker_quote=quote,
        )
