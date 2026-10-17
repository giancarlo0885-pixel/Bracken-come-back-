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
    """Verify a true 0..1 probability; never coerce percentages or missing evidence."""
    if value is None:
        raise ValueError("Probability evidence cannot be None.")
    if isinstance(value, bool):
        raise ValueError("Invalid numeric format for probability: boolean")
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError("Invalid numeric format for probability") from exc
    if not math.isfinite(number):
        raise ValueError("Probability cannot be an infinite value or NaN")
    if not 0.0 <= number <= 1.0:
        raise ValueError("Probability out of strict bounds [0.0, 1.0]")
    return number


def normalize_legacy_display_score(value: Any) -> float | None:
    """Compatibility-only heuristic score, never a calibrated forecast or Kelly input."""
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    if not math.isfinite(number) or number < 0.0 or number > 100.0:
        return None
    if number > 1.0:
        number /= 100.0
    return number


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
    value: float | None
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
    # A heuristic is never a probability. Unknown fields cannot silently
    # acquire a normalized value or a calibrated classification.
    if classification == HEURISTIC_SCORE:
        normalized = normalize_legacy_display_score(value)
    elif classification in {CALIBRATED_PROBABILITY, MODEL_ESTIMATE}:
        normalized = normalize_probability_value(value)
    else:
        normalized = None
    evidence = ProbabilityEvidence(
        field_name=field_name,
        value=normalized,
        classification=classification,
        source=source,
        calibrated=bool(calibrated and classification == CALIBRATED_PROBABILITY),
        sample_count=max(0, int(_finite(sample_count))),
        model=model,
        model_version=model_version,
        notes=notes,
    )
    return evidence.to_dict()
