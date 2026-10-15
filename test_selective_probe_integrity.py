"""Runtime-installed leakage probes cannot turn missing numbers into equality."""
from copy import deepcopy
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest

import forecasting
import capital_model_evidence_runtime as evidence
import walk_forward_validation as walk
from selective_model_leakage_fix import install_selective_model_leakage_fix


FIELDS = dict(target_price=100.,low_price=99.,high_price=101.,spot_price=100.,
              probability_up=.5,expected_move_pct=0.)


def run_probe(monkeypatch, originals):
    monkeypatch.setattr(walk,"temporal_leakage_probe",walk.temporal_leakage_probe)
    monkeypatch.setattr(evidence,"temporal_leakage_probe",evidence.temporal_leakage_probe)
    values = iter(originals)
    monkeypatch.setattr(forecasting,"forecast_price",lambda *args,**kwargs: next(values))
    install_selective_model_leakage_fix()
    assert evidence.temporal_leakage_probe is walk.temporal_leakage_probe
    history=pd.DataFrame({"Close":np.linspace(100,120,100)})
    return walk.temporal_leakage_probe(history,decision_position=80,horizon_bars=3,
        source_interval="5m",asset_class="crypto",market="crypto")


@pytest.mark.parametrize("field", list(FIELDS))
@pytest.mark.parametrize("invalid", [None,float("nan"),float("inf"),-float("inf"),True])
def test_invalid_original_or_mutated_forecast_never_passes(monkeypatch,field,invalid):
    bad=dict(FIELDS,**{field:invalid})
    result=run_probe(monkeypatch,[SimpleNamespace(**bad),SimpleNamespace(**bad)])
    assert result["ok"] is False
    assert result["status"] == "FAIL_CLOSED"


def test_missing_forecast_fields_never_pass(monkeypatch):
    result=run_probe(monkeypatch,[SimpleNamespace(),SimpleNamespace()])
    assert result["ok"] is False
    assert result["status"] == "FAIL_CLOSED"


@pytest.mark.parametrize("changes", [dict(probability_up=-.1),dict(probability_up=1.1),
    dict(low_price=0),dict(target_price=-1),dict(high_price=99.5),dict(low_price=100.5),dict(spot_price=0)])
def test_out_of_range_forecast_is_not_leakage_evidence(monkeypatch,changes):
    bad=SimpleNamespace(**dict(FIELDS,**changes))
    assert run_probe(monkeypatch,[bad,bad])["ok"] is False


def test_valid_zero_expected_move_and_boundary_probabilities_remain_usable(monkeypatch):
    forecast=SimpleNamespace(**dict(FIELDS,probability_up=0.,expected_move_pct=0.))
    assert run_probe(monkeypatch,[forecast,deepcopy(forecast)])["status"] == "PASS"


def test_valid_changed_forecast_fails_invariance(monkeypatch):
    original=SimpleNamespace(**FIELDS)
    after=SimpleNamespace(**dict(FIELDS,target_price=100.1))
    result=run_probe(monkeypatch,[original,after])
    assert result["ok"] is False
    assert result["status"] == "FAIL"


def test_legitimate_abstention_backtracks_but_never_becomes_synthetic_forecast(monkeypatch):
    forecast=SimpleNamespace(**FIELDS)
    result=run_probe(monkeypatch,[None,None,forecast,deepcopy(forecast)])
    assert result["status"] == "PASS"
    assert result["probe_backtrack_bars"] == 2
    result=run_probe(monkeypatch,[None]*100)
    assert result["status"] == "NO_EVIDENCE"
    assert result["ok"] is False


def test_mutated_forecast_must_still_have_valid_numbers(monkeypatch):
    original=SimpleNamespace(**FIELDS)
    after=SimpleNamespace(**dict(FIELDS,expected_move_pct=float("nan")))
    result=run_probe(monkeypatch,[original,after])
    assert result["status"] == "FAIL_CLOSED"
    assert result["ok"] is False
