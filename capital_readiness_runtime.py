from __future__ import annotations

import logging
from typing import Any

from capital_model_governance import apply_registered_model_governance
from migrations import run_migrations


log = logging.getLogger("capital-readiness-runtime")


def prepare_capital_readiness_runtime(worker: Any, market: str) -> dict[str, Any]:
    """Prepare non-live capital-readiness infrastructure before a worker starts.

    This applies schema migrations, refreshes evidence-driven model governance,
    and installs read-only shadow-broker tracking for crypto paper execution.
    It never changes live-trading environment switches or submits broker orders.
    """
    result: dict[str, Any] = {"market": str(market or "").lower(), "migrations": [], "governance": [], "shadow": "NOT_APPLICABLE"}
    try:
        result["migrations"] = run_migrations()
    except Exception as exc:
        result["migration_error"] = exc.__class__.__name__
        log.warning("Capital readiness migrations unavailable: %s", exc.__class__.__name__)
        return result

    try:
        assessments = apply_registered_model_governance()
        result["governance"] = [item.to_dict() for item in assessments]
    except Exception as exc:
        result["governance_error"] = exc.__class__.__name__
        log.warning("Model governance refresh unavailable: %s", exc.__class__.__name__)

    if result["market"] == "crypto":
        try:
            from shadow_broker_runtime import install_shadow_broker_tracking

            install_shadow_broker_tracking(worker)
            result["shadow"] = "INSTALLED"
        except Exception as exc:
            result["shadow"] = "UNAVAILABLE"
            result["shadow_error"] = exc.__class__.__name__
            log.warning("Shadow broker tracking unavailable: %s", exc.__class__.__name__)
    return result
