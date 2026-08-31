from __future__ import annotations

from dataclasses import dataclass, asdict
from datetime import datetime, timezone, timedelta
import json
import os
from typing import Any, Iterable

from database import rows
from model_registry import ModelStatus, model_status, update_model_status


@dataclass(frozen=True)
class ModelGovernanceAssessment:
    model: str
    model_version: str
    current_status: str
    recommended_status: str
    eligible_for_approval: bool
    recent_run_count: int
    passing_run_count: int
    distinct_symbols: int
    reasons: list[str]
    evidence: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _parse_ts(value: Any) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


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


def assess_model_evidence(
    model: str,
    model_version: str,
    evidence_rows: Iterable[dict[str, Any]],
    *,
    current_status: str | None = None,
    now: datetime | None = None,
) -> ModelGovernanceAssessment:
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)

    min_pass_runs = max(2, int(os.getenv("CAPITAL_MIN_MODEL_PASS_RUNS", "3")))
    min_symbols = max(2, int(os.getenv("CAPITAL_MIN_MODEL_SYMBOLS", "3")))
    max_age_days = max(1, int(os.getenv("CAPITAL_MODEL_EVIDENCE_MAX_AGE_DAYS", "30")))
    min_brier_skill = float(os.getenv("CAPITAL_MIN_BRIER_SKILL", "0.02"))
    max_ece = max(0.0, min(1.0, float(os.getenv("CAPITAL_MAX_ECE", "0.12"))))
    min_accuracy = max(0.0, min(1.0, float(os.getenv("CAPITAL_MIN_DIRECTIONAL_ACCURACY", "0.52"))))

    cutoff = now.astimezone(timezone.utc) - timedelta(days=max_age_days)
    recent: list[dict[str, Any]] = []
    for item in evidence_rows:
        created = _parse_ts(item.get("created_at"))
        if created is None or created < cutoff:
            continue
        if str(item.get("model") or "") != str(model):
            continue
        if str(item.get("model_version") or "") != str(model_version):
            continue
        recent.append(dict(item))

    passing: list[dict[str, Any]] = []
    failed_reasons: list[str] = []
    for item in recent:
        metrics = _json(item.get("metrics"))
        leakage = _json(item.get("leakage_checks"))
        brier_skill = metrics.get("brier_skill_score")
        ece = metrics.get("expected_calibration_error")
        accuracy = metrics.get("directional_accuracy")
        status_pass = str(item.get("status") or "").upper() == "PASS"
        leak_pass = leakage.get("strict_ordering") is True and _json(leakage.get("future_mutation_probe")).get("ok") is True
        metric_pass = (
            brier_skill is not None and float(brier_skill) >= min_brier_skill
            and ece is not None and float(ece) <= max_ece
            and accuracy is not None and float(accuracy) >= min_accuracy
            and metrics.get("beats_all_baselines") is True
        )
        if status_pass and leak_pass and metric_pass:
            passing.append(item)
        else:
            failed_reasons.append(str(item.get("run_id") or item.get("symbol") or "run") + " failed governance evidence")

    distinct_symbols = len({str(item.get("symbol") or "").upper() for item in passing if item.get("symbol")})
    eligible = len(passing) >= min_pass_runs and distinct_symbols >= min_symbols
    current = str(current_status or model_status(model, model_version).value).lower()
    reasons: list[str] = []
    if len(passing) < min_pass_runs:
        reasons.append(f"only {len(passing)} passing walk-forward runs; needs {min_pass_runs}")
    if distinct_symbols < min_symbols:
        reasons.append(f"only {distinct_symbols} distinct validated symbols; needs {min_symbols}")
    if not recent:
        reasons.append("no recent walk-forward evidence")

    if eligible:
        recommended = ModelStatus.APPROVED.value
        reasons.append("recent walk-forward, leakage, calibration and benchmark evidence satisfy promotion policy")
    elif current == ModelStatus.APPROVED.value:
        recommended = ModelStatus.DEGRADED.value
        reasons.append("approved model no longer satisfies current evidence policy")
    elif current == ModelStatus.DEGRADED.value and not recent:
        recommended = ModelStatus.SHADOW.value
        reasons.append("degraded model has no recent qualifying evidence")
    else:
        recommended = current if current in {item.value for item in ModelStatus} else ModelStatus.SHADOW.value

    return ModelGovernanceAssessment(
        model=model,
        model_version=model_version,
        current_status=current,
        recommended_status=recommended,
        eligible_for_approval=eligible,
        recent_run_count=len(recent),
        passing_run_count=len(passing),
        distinct_symbols=distinct_symbols,
        reasons=reasons,
        evidence={
            "minimum_pass_runs": min_pass_runs,
            "minimum_distinct_symbols": min_symbols,
            "maximum_evidence_age_days": max_age_days,
            "minimum_brier_skill": min_brier_skill,
            "maximum_ece": max_ece,
            "minimum_directional_accuracy": min_accuracy,
            "failed_run_count": len(failed_reasons),
        },
    )


def model_governance_assessment(model: str, model_version: str) -> ModelGovernanceAssessment:
    records = rows(
        """
        SELECT run_id, model, model_version, symbol, status, metrics, leakage_checks, created_at
        FROM walk_forward_validation_runs
        WHERE model=%s AND COALESCE(model_version,'')=COALESCE(%s,'')
        ORDER BY created_at DESC
        LIMIT 100
        """,
        (model, model_version),
    )
    return assess_model_evidence(model, model_version, records)


def apply_model_governance(model: str, model_version: str, *, actor: str = "capital-readiness-governance") -> ModelGovernanceAssessment:
    assessment = model_governance_assessment(model, model_version)
    if assessment.recommended_status != assessment.current_status:
        update_model_status(
            model,
            model_version,
            assessment.recommended_status,
            actor=actor,
            reason="; ".join(assessment.reasons)[:1000],
        )
    return assessment


def apply_registered_model_governance() -> list[ModelGovernanceAssessment]:
    registered = rows("SELECT model, model_version FROM model_registry ORDER BY model, model_version")
    results: list[ModelGovernanceAssessment] = []
    for item in registered:
        model = str(item.get("model") or "")
        version = str(item.get("model_version") or "")
        if not model:
            continue
        try:
            results.append(apply_model_governance(model, version))
        except Exception:
            # Governance failure must never promote a model. The existing status
            # remains unchanged and readiness will report the missing evidence.
            continue
    return results
