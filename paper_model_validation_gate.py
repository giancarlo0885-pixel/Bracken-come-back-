from __future__ import annotations

import json
import logging
import math
import os
from typing import Any


log = logging.getLogger("paper-model-validation-gate")


def _signal_value(signal: Any, name: str, default: Any = None) -> Any:
    if isinstance(signal, dict):
        return signal.get(name, default)
    return getattr(signal, name, default)


def _json(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return dict(value)
    if isinstance(value, str):
        try:
            decoded = json.loads(value)
            return decoded if isinstance(decoded, dict) else {}
        except Exception:
            return {}
    return {}


def _finite_metric(value: Any) -> float | None:
    """Accept only real, finite numerical evidence; booleans are not metrics."""
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return number if math.isfinite(number) else None


def regime_validation_ok(signal: Any) -> tuple[bool, str]:
    """Require regime-diverse validation before paper economics can boost size.

    This never vetoes exploration or shrinks a trade by itself. It only prevents
    positive size expansion when walk-forward evidence is concentrated in too few
    regimes or has weak regime calibration.
    """
    model = str(_signal_value(signal, "model", "") or _signal_value(signal, "forecast_model", "") or "").strip()
    version = str(
        _signal_value(signal, "model_version", "")
        or _signal_value(signal, "forecast_model_version", "")
        or ""
    ).strip()
    if not model:
        return False, "model_identity_missing"

    min_regimes = max(1, int(os.getenv("PAPER_MODEL_MIN_VALIDATED_REGIMES", "2")))
    min_samples = max(5, int(os.getenv("PAPER_MODEL_MIN_REGIME_SAMPLES", "20")))
    min_brier_skill = _finite_metric(os.getenv("PAPER_MODEL_MIN_REGIME_BRIER_SKILL", "0.0"))
    configured_max_ece = _finite_metric(os.getenv("PAPER_MODEL_MAX_REGIME_ECE", "0.18"))
    # Invalid configuration must not silently relax calibration and expand paper size.
    if min_brier_skill is None or configured_max_ece is None:
        return False, "regime_config_invalid"
    max_ece = max(0.0, min(1.0, configured_max_ece))

    try:
        from database import rows

        evidence = rows(
            """
            SELECT regime_metrics, status, created_at
            FROM walk_forward_validation_runs
            WHERE model=%s AND COALESCE(model_version,'')=COALESCE(%s,'')
            ORDER BY created_at DESC
            LIMIT 12
            """,
            (model, version),
        )
    except Exception:
        return False, "regime_evidence_unavailable"

    passed: set[str] = set()
    observed: set[str] = set()
    for run in evidence:
        if str(run.get("status") or "").upper() != "PASS":
            continue
        regimes = _json(run.get("regime_metrics"))
        for regime, payload in regimes.items():
            metrics = _json(payload)
            observed.add(str(regime))
            sample_count = _finite_metric(metrics.get("sample_count"))
            brier_skill = _finite_metric(metrics.get("brier_skill_score"))
            ece = _finite_metric(metrics.get("expected_calibration_error"))
            # Counts are nonnegative integers; Brier skill cannot exceed 1,
            # and expected calibration error is a probability-scale magnitude.
            # Invalid evidence cannot authorize a positive paper size boost.
            if (
                sample_count is None or not sample_count.is_integer()
                or sample_count < min_samples
                or brier_skill is None or brier_skill > 1.0
                or ece is None or not 0.0 <= ece <= max_ece
            ):
                continue
            if brier_skill >= min_brier_skill:
                passed.add(str(regime))

    if len(passed) >= min_regimes:
        return True, f"regime_validation_pass:{len(passed)}"
    return False, f"regime_validation_insufficient:{len(passed)}/{min_regimes};observed={len(observed)}"
