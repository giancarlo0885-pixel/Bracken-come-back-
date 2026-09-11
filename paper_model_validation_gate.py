from __future__ import annotations

import json
import logging
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
    min_brier_skill = float(os.getenv("PAPER_MODEL_MIN_REGIME_BRIER_SKILL", "0.0"))
    max_ece = max(0.0, min(1.0, float(os.getenv("PAPER_MODEL_MAX_REGIME_ECE", "0.18"))))

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
            sample_count = int(metrics.get("sample_count") or 0)
            brier_skill = metrics.get("brier_skill_score")
            ece = metrics.get("expected_calibration_error")
            if sample_count < min_samples or brier_skill is None or ece is None:
                continue
            try:
                if float(brier_skill) >= min_brier_skill and float(ece) <= max_ece:
                    passed.add(str(regime))
            except (TypeError, ValueError):
                continue

    if len(passed) >= min_regimes:
        return True, f"regime_validation_pass:{len(passed)}"
    return False, f"regime_validation_insufficient:{len(passed)}/{min_regimes};observed={len(observed)}"
