from __future__ import annotations

import math
from typing import Any, Iterable

import numpy as np


MIN_SAMPLES = 500
MIN_ACCURACY = 0.54
MIN_BRIER_SKILL = 0.0
MAX_ECE = 0.06
MIN_WILSON_LOWER = 0.51
MIN_COVERAGE = 0.10
MAX_COVERAGE = 0.80


def _wilson_lower(successes: int, total: int, z: float = 1.959963984540054) -> float:
    if total <= 0:
        return 0.0
    p = successes / total
    denom = 1.0 + z * z / total
    centre = p + z * z / (2.0 * total)
    radius = z * math.sqrt((p * (1.0 - p) + z * z / (4.0 * total)) / total)
    return (centre - radius) / denom


def _ece(probability: np.ndarray, outcome: np.ndarray, bins: int = 10) -> float:
    if not len(outcome):
        return 1.0
    edges = np.linspace(0.0, 1.0, bins + 1)
    total = float(len(outcome))
    error = 0.0
    for index in range(bins):
        left, right = edges[index], edges[index + 1]
        mask = (probability >= left) & (probability < right if index < bins - 1 else probability <= right)
        count = int(mask.sum())
        if not count:
            continue
        error += count / total * abs(float(probability[mask].mean()) - float(outcome[mask].mean()))
    return float(error)


def evaluate_shadow_predictions(
    records: Iterable[dict[str, Any]],
    *,
    total_opportunities: int,
    temporal_leakage_ok: bool,
    beats_baselines: bool,
) -> dict[str, Any]:
    rows = [record for record in records if record.get("status") == "RESOLVED"]
    probability = np.array([float(row["probability_up"]) for row in rows], dtype=float)
    outcome = np.array([1.0 if bool(row["realized_up"]) else 0.0 for row in rows], dtype=float)
    n = int(len(rows))
    coverage = n / max(1, int(total_opportunities))
    if n:
        predicted = probability >= 0.5
        correct = int(np.sum(predicted == (outcome >= 0.5)))
        accuracy = correct / n
        brier = float(np.mean((probability - outcome) ** 2))
        baseline_probability = float(outcome.mean())
        baseline_brier = float(np.mean((baseline_probability - outcome) ** 2))
        brier_skill = 1.0 - brier / baseline_brier if baseline_brier > 1e-12 else -math.inf
        ece = _ece(probability, outcome)
        wilson = _wilson_lower(correct, n)
    else:
        accuracy, brier_skill, ece, wilson = 0.0, -math.inf, 1.0, 0.0

    checks = {
        "sample_count": n >= MIN_SAMPLES,
        "directional_accuracy": accuracy >= MIN_ACCURACY,
        "brier_skill": brier_skill > MIN_BRIER_SKILL,
        "calibration": ece <= MAX_ECE,
        "wilson_lower": wilson >= MIN_WILSON_LOWER,
        "coverage": MIN_COVERAGE <= coverage <= MAX_COVERAGE,
        "temporal_leakage": bool(temporal_leakage_ok),
        "beats_baselines": bool(beats_baselines),
    }
    eligible = all(checks.values())
    return {
        "model_version": "v45-flow-shadow",
        "eligible_for_promotion": eligible,
        "status": "PASS" if eligible else "SHADOW_ONLY",
        "n": n,
        "coverage": float(coverage),
        "accuracy": float(accuracy),
        "brier_skill": float(brier_skill),
        "ece": float(ece),
        "accuracy_wilson95_lower": float(wilson),
        "temporal_leakage_ok": bool(temporal_leakage_ok),
        "beats_baselines": bool(beats_baselines),
        "checks": checks,
    }
