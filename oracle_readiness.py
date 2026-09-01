from __future__ import annotations

import argparse
import json
import os
from typing import Any

from accounting_invariants import accounting_health
from broker_order_journal import durable_journal_health, latest_reconciliation
from capital_data_health import capital_data_health
from capital_model_governance import model_governance_assessment
from config import (
    ENABLE_BROKER_SUBMISSION,
    EXECUTION_MODE,
    FORECAST_MODEL_VERSION,
    LIVE_TRADING_ARMED,
    LIVE_TRADING_KILL_SWITCH,
    ROBINHOOD_CRYPTO_ENABLED,
)
from database import database_health, rows
from forecast_calibration import evaluate_probability_calibration
from shadow_broker import shadow_readiness_summary


VALID_STATUSES = {"NOT_READY", "SHADOW_READY", "MANUAL_LIVE_CANDIDATE"}
SHADOW_READY_STATUSES = {"SHADOW_READY", "MANUAL_LIVE_CANDIDATE"}
MANUAL_LIVE_READY_STATUSES = {"MANUAL_LIVE_CANDIDATE"}


def readiness_exit_code(status: str, required: str = "shadow") -> int:
    """Return a fail-closed process exit code for automation.

    `NOT_READY` must never return success. The default CLI contract requires at
    least SHADOW_READY; callers preparing a live-capital change can require the
    stricter MANUAL_LIVE_CANDIDATE state with `--require manual-live`.
    """
    normalized = str(status or "NOT_READY").strip().upper()
    requirement = str(required or "shadow").strip().lower()
    if requirement == "manual-live":
        return 0 if normalized in MANUAL_LIVE_READY_STATUSES else 2
    return 0 if normalized in SHADOW_READY_STATUSES else 2


def _execution_models() -> list[tuple[str, str]]:
    try:
        records = rows(
            """
            SELECT model, COALESCE(model_version,'') AS model_version, MAX(created_at) AS latest
            FROM forecasts
            WHERE model IS NOT NULL AND model <> ''
            GROUP BY model, COALESCE(model_version,'')
            ORDER BY latest DESC
            LIMIT 10
            """
        )
    except Exception:
        records = []
    models = [(str(item.get("model") or ""), str(item.get("model_version") or "")) for item in records if item.get("model")]
    if not models:
        models = [("log-return diffusion", FORECAST_MODEL_VERSION)]
    return models


def _calibration_for_model(model: str, model_version: str) -> dict[str, Any]:
    try:
        records = rows(
            """
            SELECT probability_up, realized_move_pct
            FROM forecast_validation
            WHERE model=%s AND COALESCE(model_version,'')=COALESCE(%s,'')
            ORDER BY id DESC
            LIMIT 1000
            """,
            (model, model_version),
        )
        metrics = evaluate_probability_calibration(records, bins=10).to_dict()
        minimum = max(30, int(os.getenv("CAPITAL_MIN_WALK_FORWARD_SAMPLES", "100")))
        max_ece = max(0.0, min(1.0, float(os.getenv("CAPITAL_MAX_ECE", "0.12"))))
        min_skill = float(os.getenv("CAPITAL_MIN_BRIER_SKILL", "0.02"))
        min_accuracy = max(0.0, min(1.0, float(os.getenv("CAPITAL_MIN_DIRECTIONAL_ACCURACY", "0.52"))))
        ok = (
            int(metrics.get("sample_count") or 0) >= minimum
            and metrics.get("expected_calibration_error") is not None
            and float(metrics["expected_calibration_error"]) <= max_ece
            and metrics.get("brier_skill_score") is not None
            and float(metrics["brier_skill_score"]) >= min_skill
            and metrics.get("directional_accuracy") is not None
            and float(metrics["directional_accuracy"]) >= min_accuracy
        )
        return {"ok": ok, "status": "PASS" if ok else "INSUFFICIENT_OR_FAILED_EVIDENCE", **metrics}
    except Exception as exc:
        return {"ok": False, "status": "UNAVAILABLE", "reason": exc.__class__.__name__}


def _model_evidence() -> dict[str, Any]:
    models: list[dict[str, Any]] = []
    all_governed = True
    all_calibrated = True
    all_walk_forward = True
    temporal_ok = True
    for model, version in _execution_models():
        try:
            governance = model_governance_assessment(model, version).to_dict()
        except Exception as exc:
            governance = {"eligible_for_approval": False, "recommended_status": "shadow", "reasons": [exc.__class__.__name__]}
        calibration = _calibration_for_model(model, version)
        governed = bool(governance.get("eligible_for_approval")) and str(governance.get("recommended_status")) == "approved"
        walk_forward = governed and int(governance.get("passing_run_count") or 0) > 0
        leak_ok = walk_forward
        all_governed = all_governed and governed
        all_calibrated = all_calibrated and bool(calibration.get("ok"))
        all_walk_forward = all_walk_forward and walk_forward
        temporal_ok = temporal_ok and leak_ok
        models.append(
            {
                "model": model,
                "model_version": version,
                "governance": governance,
                "calibration": calibration,
                "walk_forward_ok": walk_forward,
                "temporal_leakage_ok": leak_ok,
            }
        )
    if not models:
        all_governed = all_calibrated = all_walk_forward = temporal_ok = False
    return {
        "ok": all_governed and all_calibrated and all_walk_forward and temporal_ok,
        "model_governance_ok": all_governed,
        "calibration_ok": all_calibrated,
        "walk_forward_ok": all_walk_forward,
        "temporal_leakage_ok": temporal_ok,
        "models": models,
    }


def _broker_readiness() -> dict[str, Any]:
    if not ROBINHOOD_CRYPTO_ENABLED:
        return {
            "ok": False,
            "connectivity_ok": False,
            "funding_ok": False,
            "status": "DISABLED",
        }
    try:
        from broker_order_journal import PersistentOrderJournal
        from robinhood_crypto_api import RobinhoodCryptoClient, preflight

        result = preflight(RobinhoodCryptoClient(), PersistentOrderJournal())
        connectivity_keys = (
            "ROBINHOOD AUTH",
            "ACCOUNT STATUS",
            "CRYPTO STATUS",
            "QUOTE CHECK",
            "HOLDINGS CHECK",
            "ORDERS CHECK",
        )
        connectivity_ok = all(result.get(key) == "PASS" for key in connectivity_keys)
        funding_ok = result.get("BUYING POWER STATE") == "POSITIVE"
        return {
            "ok": connectivity_ok and funding_ok,
            "connectivity_ok": connectivity_ok,
            "funding_ok": funding_ok,
            "status": "PASS" if connectivity_ok and funding_ok else "NOT_CAPITAL_READY",
            "buying_power_state": result.get("BUYING POWER STATE", "UNKNOWN"),
            "tradable_pair_count": result.get("TRADABLE PAIR COUNT", 0),
            "quote_check": result.get("QUOTE CHECK", "UNKNOWN"),
            "holdings_check": result.get("HOLDINGS CHECK", "UNKNOWN"),
            "orders_check": result.get("ORDERS CHECK", "UNKNOWN"),
        }
    except Exception as exc:
        return {
            "ok": False,
            "connectivity_ok": False,
            "funding_ok": False,
            "status": "UNAVAILABLE",
            "reason": exc.__class__.__name__,
        }


def _reconciliation_health() -> dict[str, Any]:
    try:
        latest = latest_reconciliation()
        if not latest:
            return {"ok": False, "status": "NEVER_RUN"}
        ok = str(latest.get("status") or "").upper() == "PASS" and int(latest.get("discrepancies") or 0) == 0
        return {
            "ok": ok,
            "status": str(latest.get("status") or "UNKNOWN"),
            "local_unfinished": int(latest.get("local_unfinished") or 0),
            "remote_orders": int(latest.get("remote_orders") or 0),
            "discrepancies": int(latest.get("discrepancies") or 0),
            "created_at": latest.get("created_at"),
        }
    except Exception as exc:
        return {"ok": False, "status": "UNAVAILABLE", "reason": exc.__class__.__name__}


def _safety_state() -> dict[str, Any]:
    safe = (
        str(EXECUTION_MODE).lower() == "paper"
        and ENABLE_BROKER_SUBMISSION is False
        and LIVE_TRADING_ARMED is False
        and LIVE_TRADING_KILL_SWITCH is True
    )
    return {
        "ok": safe,
        "status": "PASS" if safe else "UNSAFE_CONFIGURATION",
        "execution_mode": EXECUTION_MODE,
        "broker_submission_enabled": bool(ENABLE_BROKER_SUBMISSION),
        "live_trading_armed": bool(LIVE_TRADING_ARMED),
        "live_kill_switch": bool(LIVE_TRADING_KILL_SWITCH),
    }


def determine_overall_status(checks: dict[str, dict[str, Any]]) -> str:
    shadow_architecture = (
        bool(checks["database"].get("ok"))
        and bool(checks["safety"].get("ok"))
        and bool(checks["accounting"].get("ok"))
        and bool(checks["data_integrity"].get("ok"))
        and bool(checks["durable_order_journal"].get("ok"))
        and bool(checks["broker"].get("connectivity_ok"))
    )
    capital_evidence = (
        shadow_architecture
        and bool(checks["models"].get("ok"))
        and bool(checks["shadow_forward"].get("ok"))
        and bool(checks["broker"].get("funding_ok"))
        and bool(checks["reconciliation"].get("ok"))
    )
    if capital_evidence:
        return "MANUAL_LIVE_CANDIDATE"
    if shadow_architecture:
        return "SHADOW_READY"
    return "NOT_READY"


def build_readiness_report() -> dict[str, Any]:
    checks = {
        "database": database_health(),
        "safety": _safety_state(),
        "accounting": accounting_health(),
        "data_integrity": capital_data_health(),
        "models": _model_evidence(),
        "durable_order_journal": durable_journal_health(),
        "reconciliation": _reconciliation_health(),
        "broker": _broker_readiness(),
        "shadow_forward": shadow_readiness_summary(
            minimum_samples=max(30, int(os.getenv("CAPITAL_MIN_SHADOW_SAMPLES", "100"))),
            maximum_paper_error_pct=max(0.0, float(os.getenv("CAPITAL_MAX_SHADOW_P95_ERROR_PCT", "1.0"))),
        ),
    }
    overall = determine_overall_status(checks)
    return {
        "app": "GARIBALDI MARKET ORACLE",
        "overall_status": overall,
        "capital_authorized": False,
        "human_authorization_required": True,
        "checks": checks,
    }


def human_report(report: dict[str, Any]) -> str:
    checks = report.get("checks") or {}
    lines = ["GARIBALDI MARKET ORACLE — CAPITAL READINESS", ""]
    for name in (
        "database",
        "safety",
        "accounting",
        "data_integrity",
        "models",
        "durable_order_journal",
        "reconciliation",
        "broker",
        "shadow_forward",
    ):
        item = checks.get(name) or {}
        state = "PASS" if item.get("ok") else str(item.get("status") or "FAIL")
        lines.append(f"{name.upper():24s} {state}")
    lines.extend(["", f"OVERALL: {report.get('overall_status', 'NOT_READY')}", "CAPITAL AUTHORIZED: NO — explicit human authorization is always required."])
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="Read-only GARIBALDI capital-readiness report")
    parser.add_argument("--json", action="store_true", help="print machine-readable JSON")
    parser.add_argument(
        "--require",
        choices=("shadow", "manual-live"),
        default="shadow",
        help="minimum readiness state required for exit code 0",
    )
    args = parser.parse_args()
    report = build_readiness_report()
    print(json.dumps(report, indent=2, sort_keys=True, default=str) if args.json else human_report(report))
    return readiness_exit_code(report.get("overall_status", "NOT_READY"), args.require)


if __name__ == "__main__":
    raise SystemExit(main())
