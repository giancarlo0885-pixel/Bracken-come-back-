from __future__ import annotations

import pytest

import capital_readiness_runtime as runtime
from oracle_readiness import readiness_exit_code


def _good_live_report() -> dict:
    return {
        "overall_status": "NOT_READY",  # expected while active live switches make candidate safety false
        "checks": {
            "database": {"ok": True},
            "safety": {"ok": False, "status": "UNSAFE_CONFIGURATION"},
            "accounting": {"ok": True},
            "data_integrity": {"ok": True},
            "models": {"ok": True},
            "durable_order_journal": {"ok": True},
            "reconciliation": {"ok": True},
            "broker": {"ok": True, "connectivity_ok": True, "funding_ok": True},
            "shadow_forward": {"ok": True},
        },
    }


def _configure_live(monkeypatch) -> None:
    monkeypatch.setattr(runtime, "EXECUTION_MODE", "live")
    monkeypatch.setattr(runtime, "BROKER_MODE", "live")
    monkeypatch.setattr(runtime, "ENABLE_BROKER_SUBMISSION", True)
    monkeypatch.setattr(runtime, "LIVE_TRADING_ARMED", True)
    monkeypatch.setattr(runtime, "LIVE_TRADING_KILL_SWITCH", False)
    monkeypatch.setattr(runtime, "GLOBAL_KILL_SWITCH", False)
    monkeypatch.setattr(runtime, "LIVE_ORDER_APPROVAL_MODE", "manual")
    monkeypatch.setenv("LIVE_CAPITAL_HUMAN_APPROVED", "true")
    monkeypatch.setattr(runtime, "run_migrations", lambda: [])
    monkeypatch.setattr(runtime, "apply_registered_model_governance", lambda: [])
    monkeypatch.setattr(runtime, "build_readiness_report", _good_live_report)


def test_readiness_exit_codes_fail_closed() -> None:
    assert readiness_exit_code("NOT_READY") == 2
    assert readiness_exit_code("SHADOW_READY") == 0
    assert readiness_exit_code("MANUAL_LIVE_CANDIDATE") == 0
    assert readiness_exit_code("NOT_READY", "manual-live") == 2
    assert readiness_exit_code("SHADOW_READY", "manual-live") == 2
    assert readiness_exit_code("MANUAL_LIVE_CANDIDATE", "manual-live") == 0


def test_live_startup_passes_only_with_complete_evidence_and_human_approval(monkeypatch) -> None:
    _configure_live(monkeypatch)

    result = runtime.prepare_capital_readiness_runtime(object(), "cash")

    assert result["live_capital_requested"] is True
    assert result["live_capital_gate"] == "PASS"
    assert result["human_authorization"] == "EXPLICIT"


def test_live_startup_blocks_without_explicit_human_approval(monkeypatch) -> None:
    _configure_live(monkeypatch)
    monkeypatch.delenv("LIVE_CAPITAL_HUMAN_APPROVED", raising=False)

    with pytest.raises(runtime.LiveCapitalSafetyError, match="LIVE_CAPITAL_HUMAN_APPROVED"):
        runtime.prepare_capital_readiness_runtime(object(), "cash")


def test_live_startup_blocks_incomplete_capital_evidence(monkeypatch) -> None:
    _configure_live(monkeypatch)
    report = _good_live_report()
    report["checks"]["reconciliation"] = {"ok": False, "status": "FAIL"}
    monkeypatch.setattr(runtime, "build_readiness_report", lambda: report)

    with pytest.raises(runtime.LiveCapitalSafetyError, match="reconciliation"):
        runtime.prepare_capital_readiness_runtime(object(), "cash")


def test_live_startup_blocks_migration_failure(monkeypatch) -> None:
    _configure_live(monkeypatch)

    def fail_migrations():
        raise RuntimeError("database unavailable")

    monkeypatch.setattr(runtime, "run_migrations", fail_migrations)

    with pytest.raises(runtime.LiveCapitalSafetyError, match="migrations unavailable"):
        runtime.prepare_capital_readiness_runtime(object(), "cash")


def test_paper_runtime_preserves_best_effort_behavior(monkeypatch) -> None:
    monkeypatch.setattr(runtime, "EXECUTION_MODE", "paper")
    monkeypatch.setattr(runtime, "BROKER_MODE", "paper")
    monkeypatch.setattr(runtime, "ENABLE_BROKER_SUBMISSION", False)
    monkeypatch.setattr(runtime, "LIVE_TRADING_ARMED", False)
    monkeypatch.setattr(runtime, "LIVE_TRADING_KILL_SWITCH", True)
    monkeypatch.setattr(runtime, "GLOBAL_KILL_SWITCH", False)

    def fail_migrations():
        raise RuntimeError("database unavailable")

    monkeypatch.setattr(runtime, "run_migrations", fail_migrations)

    result = runtime.prepare_capital_readiness_runtime(object(), "cash")

    assert result["live_capital_requested"] is False
    assert result["live_capital_gate"] == "NOT_REQUESTED"
    assert result["migration_error"] == "RuntimeError"
