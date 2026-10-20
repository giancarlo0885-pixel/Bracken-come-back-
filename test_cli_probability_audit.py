"""Unit tests for the standalone, no-write historical evidence audit."""
import importlib.util
from pathlib import Path

import pytest

_SPEC = importlib.util.spec_from_file_location(
    "cli_probability_audit",
    Path(__file__).parent / "tools" / "cli_probability_audit.py",
)
audit = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(audit)


@pytest.mark.parametrize("value,category", [
    (None, "missing_null"),
    (0, "valid_0_to_1"),
    (0.5, "valid_0_to_1"),
    ("1.0", "valid_0_to_1"),
    (1.01, "percentage_gt1_le100"),
    (75, "percentage_gt1_le100"),
    ("100", "percentage_gt1_le100"),
    (-.1, "malformed_out_of_bounds"),
    (101, "malformed_out_of_bounds"),
    ("75%", "malformed_out_of_bounds"),
    (True, "malformed_out_of_bounds"),
    (float("nan"), "nonfinite"),
    (float("inf"), "nonfinite"),
])
def test_evidence_classification(value, category):
    assert audit.classify_probability(value) == category


def test_summary_partitions_every_input_once():
    values = [None, 0.5, 75, -1, float("nan")]
    result = audit.summarize_probabilities(values)
    assert sum(result.values()) == len(values)
    assert set(result) == set(audit.CATEGORIES)


@pytest.mark.parametrize("kwargs", [
    {"days": 0}, {"days": 366}, {"batch_size": 0},
    {"batch_size": 5001}, {"max_rows": 0}, {"timeout_ms": 999},
    {"timeout_ms": 30001},
])
def test_invalid_configuration_fails_before_database_connection(kwargs):
    with pytest.raises(ValueError):
        audit.audit_database("postgresql://invalid", **kwargs)
