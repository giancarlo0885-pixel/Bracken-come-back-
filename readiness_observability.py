from __future__ import annotations

import logging
from typing import Any, Callable


def _state(checks: dict[str, Any], name: str) -> str:
    item = checks.get(name) or {}
    if item.get("ok") is True:
        return "PASS"
    return str(item.get("status") or "FAIL")


def emit_capital_readiness_report(
    logger: logging.Logger,
    report_builder: Callable[[], dict[str, Any]] | None = None,
) -> dict[str, Any] | None:
    """Emit a sanitized, read-only production readiness summary.

    The report intentionally exposes only readiness states and aggregate evidence
    metrics. It never logs credentials, account identifiers, order payloads, or
    live-capital authorization values beyond the explicit fail-closed state.
    """
    if report_builder is None:
        from oracle_readiness import build_readiness_report

        report_builder = build_readiness_report

    try:
        report = report_builder()
    except Exception as exc:
        logger.warning(
            "CAPITAL READINESS | overall=UNAVAILABLE | reason=%s | capital_authorized=NO",
            exc.__class__.__name__,
        )
        return None

    checks = report.get("checks") or {}
    broker = checks.get("broker") or {}
    shadow = checks.get("shadow_forward") or {}
    models = checks.get("models") or {}

    logger.info(
        "CAPITAL READINESS | overall=%s | database=%s | safety=%s | accounting=%s | "
        "data_integrity=%s | models=%s | durable_order_journal=%s | reconciliation=%s | "
        "broker_connectivity=%s | broker_funding=%s | shadow_forward=%s | "
        "shadow_evaluated=%s/%s | shadow_p95_error_pct=%s | model_count=%s | "
        "capital_authorized=NO | human_authorization_required=true",
        str(report.get("overall_status") or "NOT_READY"),
        _state(checks, "database"),
        _state(checks, "safety"),
        _state(checks, "accounting"),
        _state(checks, "data_integrity"),
        _state(checks, "models"),
        _state(checks, "durable_order_journal"),
        _state(checks, "reconciliation"),
        "PASS" if broker.get("connectivity_ok") is True else str(broker.get("status") or "FAIL"),
        "PASS" if broker.get("funding_ok") is True else str(broker.get("buying_power_state") or broker.get("status") or "FAIL"),
        _state(checks, "shadow_forward"),
        int(shadow.get("evaluated_samples") or 0),
        int(shadow.get("minimum_samples") or 0),
        shadow.get("p95_paper_vs_broker_error_pct"),
        len(models.get("models") or []),
    )

    if models.get("ok") is not True:
        for item in list(models.get("models") or [])[:10]:
            governance = item.get("governance") or {}
            calibration = item.get("calibration") or {}
            logger.info(
                "CAPITAL READINESS MODEL | model=%s | version=%s | governance_status=%s | "
                "eligible_for_approval=%s | calibration_status=%s | calibration_samples=%s | "
                "ece=%s | brier_skill=%s | directional_accuracy=%s | walk_forward_ok=%s | "
                "temporal_leakage_ok=%s",
                str(item.get("model") or "unknown"),
                str(item.get("model_version") or ""),
                str(governance.get("recommended_status") or "unknown"),
                bool(governance.get("eligible_for_approval")),
                str(calibration.get("status") or "unknown"),
                int(calibration.get("sample_count") or 0),
                calibration.get("expected_calibration_error"),
                calibration.get("brier_skill_score"),
                calibration.get("directional_accuracy"),
                bool(item.get("walk_forward_ok")),
                bool(item.get("temporal_leakage_ok")),
            )

    return report
