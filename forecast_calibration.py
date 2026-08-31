from __future__ import annotations

from dataclasses import asdict, dataclass
import math
from typing import Any, Iterable


_EPS = 1e-12


@dataclass(frozen=True)
class CalibrationBin:
    lower: float
    upper: float
    count: int
    mean_probability: float
    observed_frequency: float
    absolute_gap: float

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class CalibrationMetrics:
    sample_count: int
    base_rate: float | None
    directional_accuracy: float | None
    directional_accuracy_ci_low: float | None
    directional_accuracy_ci_high: float | None
    brier_score: float | None
    climatology_brier_score: float | None
    brier_skill_score: float | None
    log_loss: float | None
    expected_calibration_error: float | None
    maximum_calibration_error: float | None
    calibration_intercept: float | None
    calibration_slope: float | None
    bins: list[CalibrationBin]

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["bins"] = [item.to_dict() for item in self.bins]
        return payload


def _finite(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _probability(value: Any) -> float | None:
    number = _finite(value)
    if number is None or number < 0.0 or number > 1.0:
        return None
    return number


def _outcome(record: dict[str, Any]) -> int | None:
    realized = _finite(record.get("realized_move_pct"))
    if realized is None:
        return None
    return 1 if realized > 0.0 else 0


def wilson_interval(successes: int, total: int, z: float = 1.959963984540054) -> tuple[float | None, float | None]:
    if total <= 0:
        return None, None
    p = successes / total
    denom = 1.0 + (z * z) / total
    center = (p + (z * z) / (2.0 * total)) / denom
    radius = z * math.sqrt((p * (1.0 - p) / total) + (z * z) / (4.0 * total * total)) / denom
    return max(0.0, center - radius), min(1.0, center + radius)


def _logit(probability: float) -> float:
    p = min(1.0 - 1e-6, max(1e-6, probability))
    return math.log(p / (1.0 - p))


def _calibration_regression(probabilities: list[float], outcomes: list[int]) -> tuple[float | None, float | None]:
    if len(probabilities) < 20 or len(set(outcomes)) < 2:
        return None, None
    try:
        from sklearn.linear_model import LogisticRegression

        model = LogisticRegression(C=1_000_000.0, solver="lbfgs", max_iter=2000)
        x = [[_logit(value)] for value in probabilities]
        model.fit(x, outcomes)
        return float(model.intercept_[0]), float(model.coef_[0][0])
    except Exception:
        return None, None


def evaluate_probability_calibration(
    records: Iterable[dict[str, Any]],
    *,
    bins: int = 10,
) -> CalibrationMetrics:
    """Evaluate probability forecasts against realized up/down outcomes.

    Invalid or incomplete probability/outcome rows are excluded rather than
    silently coerced. The no-skill Brier benchmark is the empirical climatology
    rate over the same evaluation sample.
    """
    pairs: list[tuple[float, int]] = []
    for record in records:
        probability = _probability(record.get("probability_up"))
        outcome = _outcome(record)
        if probability is None or outcome is None:
            continue
        pairs.append((probability, outcome))

    if not pairs:
        return CalibrationMetrics(
            0, None, None, None, None, None, None, None, None, None, None, None, None, []
        )

    probabilities = [item[0] for item in pairs]
    outcomes = [item[1] for item in pairs]
    count = len(pairs)
    positives = sum(outcomes)
    base_rate = positives / count

    correct = sum(1 for probability, outcome in pairs if (probability >= 0.5) == bool(outcome))
    accuracy = correct / count
    ci_low, ci_high = wilson_interval(correct, count)

    brier = sum((probability - outcome) ** 2 for probability, outcome in pairs) / count
    climatology_brier = sum((base_rate - outcome) ** 2 for outcome in outcomes) / count
    brier_skill = None
    if climatology_brier > _EPS:
        brier_skill = 1.0 - (brier / climatology_brier)

    log_loss = -sum(
        outcome * math.log(min(1.0 - _EPS, max(_EPS, probability)))
        + (1 - outcome) * math.log(min(1.0 - _EPS, max(_EPS, 1.0 - probability)))
        for probability, outcome in pairs
    ) / count

    bucket_count = max(2, min(50, int(bins)))
    calibration_bins: list[CalibrationBin] = []
    ece = 0.0
    mce = 0.0
    for index in range(bucket_count):
        lower = index / bucket_count
        upper = (index + 1) / bucket_count
        bucket = [
            (probability, outcome)
            for probability, outcome in pairs
            if probability >= lower and (probability < upper or (index == bucket_count - 1 and probability <= upper))
        ]
        if not bucket:
            continue
        mean_probability = sum(item[0] for item in bucket) / len(bucket)
        observed_frequency = sum(item[1] for item in bucket) / len(bucket)
        gap = abs(mean_probability - observed_frequency)
        ece += (len(bucket) / count) * gap
        mce = max(mce, gap)
        calibration_bins.append(
            CalibrationBin(
                lower=lower,
                upper=upper,
                count=len(bucket),
                mean_probability=mean_probability,
                observed_frequency=observed_frequency,
                absolute_gap=gap,
            )
        )

    intercept, slope = _calibration_regression(probabilities, outcomes)
    return CalibrationMetrics(
        sample_count=count,
        base_rate=base_rate,
        directional_accuracy=accuracy,
        directional_accuracy_ci_low=ci_low,
        directional_accuracy_ci_high=ci_high,
        brier_score=brier,
        climatology_brier_score=climatology_brier,
        brier_skill_score=brier_skill,
        log_loss=log_loss,
        expected_calibration_error=ece,
        maximum_calibration_error=mce,
        calibration_intercept=intercept,
        calibration_slope=slope,
        bins=calibration_bins,
    )
