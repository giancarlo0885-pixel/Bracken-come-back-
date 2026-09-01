from __future__ import annotations

import logging
import os
from typing import Any

from capital_model_governance import apply_registered_model_governance
from config import (
    BROKER_MODE,
    ENABLE_BROKER_SUBMISSION,
    EXECUTION_MODE,
    GLOBAL_KILL_SWITCH,
    LIVE_ORDER_APPROVAL_MODE,
    LIVE_TRADING_ARMED,
    LIVE_TRADING_KILL_SWITCH,
)
from migrations import run_migrations
from oracle_readiness import build_readiness_report


log = logging.getLogger("capital-readiness-runtime")


class LiveCapitalSafetyError(RuntimeError):
    """Raised when a worker attempts to start with incomplete live-capital safety evidence."""


def _env_flag(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return bool(default)
    return str(raw).strip().lower() in {"1", "true", "yes", "on"}


def _live_capital_requested() -> bool:
    """Treat any live execution/broker switch as a request for the strict gate."""
    return (
        str(EXECUTION_MODE).strip().lower() == "live"
        or str(BROKER_MODE).strip().lower() == "live"
        or bool(ENABLE_BROKER_SUBMISSION)
        or bool(LIVE_TRADING_ARMED)
    )


def _live_runtime_configuration_errors() -> list[str]:
    """Return fail-closed configuration errors for live-capital startup."""
    errors: list[str] = []
    if str(EXECUTION_MODE).strip().lower() != "live":
        errors.append("EXECUTION_MODE must be live")
    if str(BROKER_MODE).strip().lower() != "live":
        errors.append("BROKER_MODE must be live")
    if ENABLE_BROKER_SUBMISSION is not True:
        errors.append("ENABLE_BROKER_SUBMISSION must be true")
    if LIVE_TRADING_ARMED is not True:
        errors.append("LIVE_TRADING_ARMED must be true")
    if LIVE_TRADING_KILL_SWITCH is not False:
        errors.append("LIVE_TRADING_KILL_SWITCH must be false")
    if GLOBAL_KILL_SWITCH is not False:
        errors.append("GLOBAL_KILL_SWITCH must be false")
    if str(LIVE_ORDER_APPROVAL_MODE).strip().lower() != "manual":
        errors.append("LIVE_ORDER_APPROVAL_MODE must be manual")
    if not _env_flag("LIVE_CAPITAL_HUMAN_APPROVED", False):
        errors.append("LIVE_CAPITAL_HUMAN_APPROVED must be true")
    return errors


def _live_evidence_failures(report: dict[str, Any]) -> list[str]:
    """Evaluate capital evidence independently of the disarmed candidate safety state.

    oracle_readiness deliberately requires paper/disarmed switches while deciding
    whether the system is a MANUAL_LIVE_CANDIDATE. Once a worker is explicitly
    started in live mode, that safety-state check is expected to be false, so the
    live startup gate re-checks every capital evidence item except that candidate
    configuration check.
    """
    checks = report.get("checks") or {}
    required_ok = (
        "database",
        "accounting",
        "data_integrity",
        "models",
        "durable_order_journal",
        "reconciliation",
        "shadow_forward",
    )
    failures = [name for name in required_ok if not bool((checks.get(name) or {}).get("ok"))]

    broker = checks.get("broker") or {}
    if not bool(broker.get("connectivity_ok")):
        failures.append("broker_connectivity")
    if not bool(broker.get("funding_ok")):
        failures.append("broker_funding")
    return failures


def _enforce_live_capital_gate(result: dict[str, Any]) -> None:
    config_errors = _live_runtime_configuration_errors()
    if config_errors:
        result["live_capital_gate"] = "BLOCKED_CONFIGURATION"
        result["live_capital_errors"] = list(config_errors)
        raise LiveCapitalSafetyError("Live-capital startup blocked: " + "; ".join(config_errors))

    report = build_readiness_report()
    evidence_failures = _live_evidence_failures(report)
    result["live_readiness_status"] = str(report.get("overall_status") or "NOT_READY")
    if evidence_failures:
        result["live_capital_gate"] = "BLOCKED_EVIDENCE"
        result["live_capital_errors"] = list(evidence_failures)
        raise LiveCapitalSafetyError(
            "Live-capital startup blocked by incomplete evidence: " + ", ".join(evidence_failures)
        )

    result["live_capital_gate"] = "PASS"
    result["human_authorization"] = "EXPLICIT"


def prepare_capital_readiness_runtime(worker: Any, market: str) -> dict[str, Any]:
    """Prepare capital-readiness infrastructure and fail closed for live startup.

    Paper/shadow workers retain best-effort preparation behavior. If any live or
    broker-submission switch is requested, migrations, historical walk-forward
    evidence, governance preparation, runtime configuration, capital evidence,
    broker readiness, reconciliation, and explicit human approval become
    mandatory before the worker may start. This function never changes live-
    trading switches or submits broker orders.
    """
    live_requested = _live_capital_requested()
    result: dict[str, Any] = {
        "market": str(market or "").lower(),
        "migrations": [],
        "model_evidence": [],
        "governance": [],
        "shadow": "NOT_APPLICABLE",
        "live_capital_requested": live_requested,
        "live_capital_gate": "NOT_REQUESTED",
    }
    try:
        result["migrations"] = run_migrations()
    except Exception as exc:
        result["migration_error"] = exc.__class__.__name__
        if live_requested:
            result["live_capital_gate"] = "BLOCKED_MIGRATIONS"
            raise LiveCapitalSafetyError(
                f"Live-capital startup blocked: migrations unavailable ({exc.__class__.__name__})"
            ) from exc
        log.warning("Capital readiness migrations unavailable: %s", exc.__class__.__name__)
        return result

    # The governance code already knew how to judge walk-forward/calibration
    # evidence, but production previously had no path that generated it. Build
    # bounded, deduplicated historical out-of-sample evidence before governance.
    # A weak model still fails its real metrics; nothing here lowers thresholds.
    if result["market"] == "crypto":
        try:
            from capital_model_evidence_runtime import refresh_capital_model_evidence

            result["model_evidence"] = refresh_capital_model_evidence()
        except Exception as exc:
            result["model_evidence_error"] = exc.__class__.__name__
            if live_requested:
                result["live_capital_gate"] = "BLOCKED_MODEL_EVIDENCE"
                raise LiveCapitalSafetyError(
                    f"Live-capital startup blocked: model evidence refresh unavailable ({exc.__class__.__name__})"
                ) from exc
            log.warning("Capital model evidence refresh unavailable: %s", exc.__class__.__name__)

    try:
        assessments = apply_registered_model_governance()
        result["governance"] = [item.to_dict() for item in assessments]
    except Exception as exc:
        result["governance_error"] = exc.__class__.__name__
        if live_requested:
            result["live_capital_gate"] = "BLOCKED_GOVERNANCE"
            raise LiveCapitalSafetyError(
                f"Live-capital startup blocked: governance refresh unavailable ({exc.__class__.__name__})"
            ) from exc
        log.warning("Model governance refresh unavailable: %s", exc.__class__.__name__)

    if result["market"] == "crypto":
        try:
            from shadow_broker_runtime import install_shadow_broker_tracking

            install_shadow_broker_tracking(worker)
            result["shadow"] = "INSTALLED"
        except Exception as exc:
            result["shadow"] = "UNAVAILABLE"
            result["shadow_error"] = exc.__class__.__name__
            if live_requested:
                result["live_capital_gate"] = "BLOCKED_SHADOW_TRACKING"
                raise LiveCapitalSafetyError(
                    f"Live-capital startup blocked: shadow broker tracking unavailable ({exc.__class__.__name__})"
                ) from exc
            log.warning("Shadow broker tracking unavailable: %s", exc.__class__.__name__)

    if live_requested:
        _enforce_live_capital_gate(result)
    return result
