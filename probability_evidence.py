from __future__ import annotations

from dataclasses import asdict, dataclass
import math
from typing import Any


CALIBRATED_PROBABILITY = "CALIBRATED_PROBABILITY"
MODEL_ESTIMATE = "MODEL_ESTIMATE"
HEURISTIC_SCORE = "HEURISTIC_SCORE"
UNVALIDATED = "UNVALIDATED"
PROBABILITY_CLASSES = {
    CALIBRATED_PROBABILITY,
    MODEL_ESTIMATE,
    HEURISTIC_SCORE,
    UNVALIDATED,
}


def _finite(value: Any, default: float = 0.0) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return number if math.isfinite(number) else default


def normalize_probability_value(value: Any) -> float:
    number = _finite(value)
    if number > 1.0:
        number /= 100.0
    return max(0.0, min(1.0, number))


def classify_probability_field(name: str, *, calibrated: bool = False, model_backed: bool = False) -> str:
    field = str(name or "").lower()
    if calibrated and field in {"probability", "probability_up", "probability_of_profit"}:
        return CALIBRATED_PROBABILITY
    if model_backed and field in {"probability", "probability_up", "probability_of_profit", "expected_value"}:
        return MODEL_ESTIMATE
    if field in {"confidence", "score", "opportunity_score", "weighted_signal_score"}:
        return HEURISTIC_SCORE
    return UNVALIDATED


@dataclass(frozen=True)
class ProbabilityEvidence:
    field_name: str
    value: float
    classification: str
    source: str
    calibrated: bool
    sample_count: int = 0
    model: str | None = None
    model_version: str | None = None
    notes: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def probability_metadata(
    *,
    field_name: str,
    value: Any,
    source: str,
    calibrated: bool = False,
    model_backed: bool = False,
    sample_count: int = 0,
    model: str | None = None,
    model_version: str | None = None,
    notes: str = "",
) -> dict[str, Any]:
    classification = classify_probability_field(
        field_name,
        calibrated=calibrated,
        model_backed=model_backed,
    )
    evidence = ProbabilityEvidence(
        field_name=field_name,
        value=normalize_probability_value(value),
        classification=classification,
        source=source,
        calibrated=bool(calibrated and classification == CALIBRATED_PROBABILITY),
        sample_count=max(0, int(_finite(sample_count))),
        model=model,
        model_version=model_version,
        notes=notes,
    )
    return evidence.to_dict()
