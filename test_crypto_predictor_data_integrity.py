"""Unknown/invalid bars cannot become fabricated selective-model examples."""
import numpy as np
import pandas as pd
import pytest

import crypto_predictor_v43 as predictor
from test_capital_model_v43 import _block_reversal_history


def test_missing_lag_and_unresolved_future_are_unknown():
    close = pd.Series(100*np.exp(np.arange(180)*.001))
    table, lag = predictor._resolved_transition_table(close, 3)
    assert table.loc[lag.isna(), "lag_up"].isna().all()
    assert table.iloc[-3:]["target_up"].isna().all()
    resolved = table.iloc[:-3].dropna()
    assert len(resolved) == 174
    assert np.isfinite(lag.loc[resolved.index]).all()


def test_nonfinite_returns_never_acquire_direction_labels():
    close = pd.Series(100*np.exp(np.arange(180)*.001))
    close.iloc[20] = float("inf")
    table, lag = predictor._resolved_transition_table(close, 3)
    assert table.loc[~np.isfinite(lag), "lag_up"].isna().all()
    assert np.isfinite(table.dropna()["forward_return"]).all()


@pytest.mark.parametrize("invalid", [float("nan"), float("inf"), -float("inf"), 0, -1, None, "bad", True])
def test_an_invalid_interior_bar_abstains_without_compressing_time(invalid):
    history = _block_reversal_history(1203)
    assert predictor.predict_crypto_direction(history, 3) is not None
    history["Close"] = history["Close"].astype(object)
    history.iloc[20, history.columns.get_loc("Close")] = invalid
    assert predictor.predict_crypto_direction(history, 3) is None


def test_old_nonfinite_one_bar_return_abstains_even_outside_recent_volatility_window():
    history = _block_reversal_history(1203)
    history.iloc[20, 0] = 1e-300
    history.iloc[21, 0] = 1e300
    assert predictor.predict_crypto_direction(history, 3) is None


def test_valid_reversal_keeps_selection_guards_and_counts_only_real_transitions():
    history = _block_reversal_history(1203)
    result = predictor.predict_crypto_direction(history, 3)
    assert result is not None
    assert result["training_samples"] == len(history)-6
    assert result["selection_brier_skill"] > 0
    assert result["selection_accuracy"] >= .52
    assert result["current_confidence"] >= result["confidence_threshold"]
    assert result["probability_up"] < .5
