from __future__ import annotations

import os
from typing import Any

from model_registry import model_status
from paper_lifecycle_readiness import paper_lifecycle_health
from shadow_broker import shadow_readiness_summary


def build_paper_readiness_scorecard(
    *,
    market: str = "crypto",
    active_model: str = "crypto selective sign transition",
    active_model_version: str = "v43-selective-transition",
) -> dict[str, Any]:
    """Return a read-only paper/live-readiness scorecard.

    This function never changes execution mode, model status, broker state, or
    portfolio state. It intentionally treats live-money arming as a separate
    human decision even when every paper criterion passes.
    """
    lifecycle = paper_lifecycle_health(market)
    shadow = shadow_readiness_summary(minimum_samples=100, maximum_paper_error_pct=1.0)
    live_armed = os.getenv("LIVE_TRADING_ARMED", "false").strip().lower() == "true"
    broker_submission = os.getenv("ENABLE_BROKER_SUBMISSION", "false").strip().lower() == "true"
    execution_mode = os.getenv("EXECUTION_MODE", "paper").strip().lower()
    model_state = model_status(active_model, active_model_version).value

    checks = {
        "paper_mode": execution_mode == "paper",
        "live_trading_disarmed": not live_armed,
        "broker_submission_disabled": not broker_submission,
        "paper_round_trip": bool(lifecycle.get("round_trip_proven")),
        "shadow_execution_fidelity": bool(shadow.get("ok")),
        "model_governance_approved": model_state == "approved",
    }
    paper_evidence_complete = all(
        checks[name]
        for name in (
            "paper_mode",
            "live_trading_disarmed",
            "broker_submission_disabled",
            "paper_round_trip",
            "shadow_execution_fidelity",
            "model_governance_approved",
        )
    )
    return {
        "status": "HUMAN_REVIEW_READY" if paper_evidence_complete else "COLLECTING_EVIDENCE",
        "paper_evidence_complete": paper_evidence_complete,
        "human_authorization_required": True,
        "automatic_live_activation_allowed": False,
        "checks": checks,
        "paper_lifecycle": lifecycle,
        "shadow_execution": shadow,
        "active_model": {
            "model": active_model,
            "model_version": active_model_version,
            "status": model_state,
        },
    }
