from __future__ import annotations

import paper_model_validation_gate as gate


def test_missing_model_never_passes_regime_gate():
    ok, reason = gate.regime_validation_ok({})
    assert ok is False
    assert reason == "model_identity_missing"


def test_json_helper_accepts_dict_and_json_string():
    assert gate._json({"bull": {"sample_count": 20}})["bull"]["sample_count"] == 20
    assert gate._json('{"bear":{"sample_count":25}}')["bear"]["sample_count"] == 25
    assert gate._json("not-json") == {}
