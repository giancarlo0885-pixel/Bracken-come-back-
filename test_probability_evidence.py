"""Regression guards for strict forecast probabilities and display-only heuristics."""
from __future__ import annotations

from datetime import datetime, timezone
import math

import pytest

from probability_evidence import (
    CALIBRATED_PROBABILITY,
    HEURISTIC_SCORE,
    MODEL_ESTIMATE,
    UNVALIDATED,
    normalize_legacy_display_score,
    normalize_probability_value,
    probability_metadata,
)
from prediction_engine import build_decisions


@pytest.mark.parametrize("value,expected", [(0, 0.0), (0.35, 0.35), ("0.35", 0.35), (1, 1.0), ("1.0", 1.0)])
def test_probability_accepts_only_unit_interval(value, expected):
    assert normalize_probability_value(value) == expected


@pytest.mark.parametrize("value,error", [
    (None, "cannot be None"),
    (75, "out of strict bounds"),
    ("75.0", "out of strict bounds"),
    ("75%", "Invalid numeric format"),
    ("-0.01", "out of strict bounds"),
    (150, "out of strict bounds"),
    ("invalid_string", "Invalid numeric format"),
    (float("nan"), "infinite value or NaN"),
    (float("inf"), "infinite value or NaN"),
    (float("-inf"), "infinite value or NaN"),
    (True, "Invalid numeric format"),
    ([], "Invalid numeric format"),
])
def test_probability_rejects_invalid_evidence(value, error):
    with pytest.raises(ValueError, match=error):
        normalize_probability_value(value)


@pytest.mark.parametrize("value,expected", [(75, .75), ("100.0", 1.0), (.5, .5), (0, 0.0), ("50.0", .5)])
def test_legacy_score_is_explicitly_display_only(value, expected):
    assert normalize_legacy_display_score(value) == expected


@pytest.mark.parametrize("value", [None, float("nan"), float("inf"), "-20", -0.2, 150, "bad", "75%", False, []])
def test_legacy_score_never_clamps_invalid_data_into_validity(value):
    assert normalize_legacy_display_score(value) is None


def test_evidence_separates_heuristic_from_calibrated_forecast():
    heuristic = probability_metadata(field_name="confidence", value=85, source="legacy_ui", calibrated=True, model_backed=True)
    calibrated = probability_metadata(field_name="probability_up", value=0.64, source="held_out", calibrated=True, model_backed=True)
    estimated = probability_metadata(field_name="probability_of_profit", value=.7, source="model", model_backed=True)
    assert heuristic["classification"] == HEURISTIC_SCORE
    assert heuristic["calibrated"] is False
    assert heuristic["value"] == .85
    assert calibrated["classification"] == CALIBRATED_PROBABILITY
    assert calibrated["value"] == .64
    assert estimated["classification"] == MODEL_ESTIMATE


@pytest.mark.parametrize("value", [None, 150, float("nan"), "garbage"])
def test_invalid_heuristic_metadata_is_explicitly_unavailable(value):
    evidence = probability_metadata(field_name="confidence", value=value, source="legacy_ui")
    assert evidence["value"] is None
    assert evidence["calibrated"] is False


def test_unknown_field_never_acquires_probability_metadata_value():
    evidence = probability_metadata(field_name="arbitrary_legacy_field", value=1.0, source="unknown")
    assert evidence["classification"] == UNVALIDATED
    assert evidence["value"] is None


def test_probability_metadata_rejects_unverified_percent_forecast():
    with pytest.raises(ValueError, match="out of strict bounds"):
        probability_metadata(field_name="probability_up", value=72, source="model", model_backed=True)


@pytest.mark.parametrize("invalid", [75, -0.2, float("nan"), "75%", True, None])
def test_prediction_rejects_invalid_nonmissing_forecast_probabilities(invalid):
    now = datetime.now(timezone.utc).isoformat()
    op = {"market": "cash", "symbol": "AAPL", "opportunity_score": 90, "payload": {"action": "BUY"}, "created_at": now}
    signal = {"market": "cash", "symbol": "AAPL", "price": 200, "action": "BUY", "confidence": .9,
              "created_at": now, "source_interval": "1d", "scan_type": "deep", "source_quote_timestamp": now}
    forecast = {"market": "cash", "symbol": "AAPL", "requested_symbol": "AAPL", "provider_symbol": "AAPL",
                "source_interval": "1d", "scan_type": "deep", "source_quote_timestamp": now,
                "target_price": 205, "probability_up": invalid, "created_at": now}
    decision = build_decisions([op], [signal], [forecast], 10)[0]
    if invalid is None:
        # Missing evidence has its own legacy presentation path; it is never
        # secretly reclassified as a calibrated forecast.
        assert decision["probability_up"] is None
        assert decision["probability_evidence"]["classification"] == HEURISTIC_SCORE
    else:
        assert decision["action"] == "WAIT"
        assert decision["trade_eligible"] is False
        assert decision["probability_up"] is None
        assert decision["probability_evidence"]["classification"] == UNVALIDATED
        assert "Invalid forecast probability" in decision["data_status"]

def _advisor_candidate(confidence):
    now = datetime.now(timezone.utc).isoformat()
    quote = {
        "symbol": "AAPL", "requested_symbol": "AAPL", "provider_symbol": "AAPL",
        "price": 200, "provider": "unit", "quote_timestamp": now,
        "quote_verified": True, "interval": "5m", "currency": "USD", "exchange": "NASDAQ",
    }
    return {
        "symbol": "AAPL", "market": "cash", "verified_quote": quote,
        "confidence": confidence, "opportunity_score": 90,
        "expected_return": 6, "expected_downside": 3,
        "data_quality_score": 90, "liquidity_value": 1_000_000,
        "validation_status": "approved",
    }


def test_advisor_invalid_confidence_is_not_rendered_as_probability():
    from advisor_engine import generate_recommendation
    for invalid in (150, -20, float("nan")):
        rec = generate_recommendation(_advisor_candidate(invalid))
        assert rec.probability_evidence == {}
        assert rec.confidence == 0.0
        assert rec.action not in {"BUY", "STRONG BUY", "ACCUMULATE"}


def test_advisor_legacy_scale_is_not_promoted_into_decision_confidence():
    from advisor_engine import generate_recommendation
    rec = generate_recommendation(_advisor_candidate(.9))
    assert rec.confidence == .9  # existing advisor decision-scale semantics
    assert rec.probability_evidence["classification"] == HEURISTIC_SCORE
    assert rec.probability_evidence["value"] == .9
