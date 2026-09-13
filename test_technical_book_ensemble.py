import numpy as np
import pandas as pd

from technical_book_ensemble import assess_technical_book_ensemble
from entry_pattern_memory_runtime import expanded_feature_vector


def _history(values, volume_last=1000.0):
    close = np.asarray(values, dtype=float)
    volume = np.full(len(close), 1000.0)
    volume[-1] = volume_last
    return pd.DataFrame({
        "Open": close * 0.998,
        "High": close * 1.004,
        "Low": close * 0.996,
        "Close": close,
        "Volume": volume,
    })


def test_ensemble_emits_bounded_distinct_metrics():
    values = np.r_[np.linspace(80.0, 95.0, 60), np.linspace(95.0, 103.0, 20)]
    result = assess_technical_book_ensemble(_history(values, volume_last=2200.0), schwager_score=0.6)
    assert result.available is True
    scores = [
        result.pring_cycle_momentum_score,
        result.murphy_confirmation_score,
        result.oneil_breakout_quality_score,
        result.nison_candlestick_context_score,
        result.bulkowski_pattern_quality_score,
        result.shannon_multihorizon_alignment_score,
        result.schwager_systematic_structure_score,
        result.consensus_score,
    ]
    assert all(-1.0 <= score <= 1.0 for score in scores)
    assert result.metric_version == "ta-seven-book-ensemble-v1"
    assert result.bullish_votes + result.bearish_votes + result.neutral_votes == 7
    assert 0 <= result.agreement_count <= 7
    assert 0.0 <= result.conflict_score <= 1.0


def test_bullish_alignment_does_not_require_unanimity():
    values = np.r_[np.full(20, 80.0), np.linspace(80.0, 100.0, 60)]
    result = assess_technical_book_ensemble(_history(values, volume_last=2600.0), schwager_score=0.7)
    assert result.available is True
    assert result.shannon_multihorizon_alignment_score > 0
    assert result.murphy_confirmation_score > 0
    assert result.consensus_score > 0
    assert result.bullish_votes >= 3


def test_memory_vector_persists_ensemble_metrics():
    signal = {
        "price": 100.0,
        "ta_pring_score": 0.3,
        "ta_murphy_score": 0.4,
        "ta_oneil_score": 0.5,
        "ta_nison_score": 0.2,
        "ta_bulkowski_score": 0.6,
        "ta_shannon_score": 0.7,
        "ta_consensus_score": 0.45,
        "ta_conflict_score": 0.25,
        "ta_agreement_count": 5,
    }
    features = expanded_feature_vector(signal)
    for key in (
        "ta_pring", "ta_murphy", "ta_oneil", "ta_nison", "ta_bulkowski",
        "ta_shannon", "ta_consensus", "ta_conflict", "ta_agreement",
    ):
        assert key in features
    assert features["ta_consensus"] == 0.45
    assert 0.0 < features["ta_agreement"] <= 1.0


def test_short_history_is_neutral_and_unavailable():
    result = assess_technical_book_ensemble(_history(np.linspace(10.0, 11.0, 20)), schwager_score=1.0)
    assert result.available is False
    assert result.consensus_score == 0.0
    assert result.neutral_votes == 7
