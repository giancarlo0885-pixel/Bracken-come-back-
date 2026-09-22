from oracle_research_cockpit import integrity_flags, learning_velocity, challenger_verdict


def test_integrity_flags_detect_impossible_values():
    flags = integrity_flags({"probability": 1.2, "fees": -1, "profit_factor": -0.1, "mfe_pct": -2, "mae_pct": 3, "quantity": -4})
    assert "probability_outside_0_1" in flags
    assert "fees_negative" in flags
    assert "profit_factor_negative" in flags
    assert "mfe_sign_invalid" in flags
    assert "mae_sign_invalid" in flags
    assert "quantity_negative" in flags


def test_learning_velocity_does_not_invent_progress():
    v = learning_velocity(250, 25)
    assert v["trades_per_hour"] == 10
    assert v["remaining"] == 750
    assert v["eta_hours"] == 75


def test_challenger_cannot_self_promote():
    v = challenger_verdict({"accepted_trades": 1000, "expectancy": 1, "profit_factor": 2}, {"expectancy": 0, "profit_factor": 1})
    assert v["evidence_complete"] is True
    assert v["promotion_authorized"] is False
    assert v["execution_impact"] == "NONE"


def test_cockpit_query_keeps_generation_config_and_provenance_isolation():
    src = open("pages/3_Oracle_Brain.py", encoding="utf-8").read()
    assert "o.generation=a.generation AND o.config_hash=a.config_hash" in src
    assert "o.provenance_version>=2" in src
    assert "Intelligence Data Lineage & Health" in src
    assert "Provenance v2" in src
    cockpit = src.split("# Unified research cockpit:", 1)[1].split("summary = snapshot", 1)[0]
    assert "feature_snapshot IS NOT NULL" not in cockpit
    assert "entry_signal_id IS NOT NULL" not in cockpit
