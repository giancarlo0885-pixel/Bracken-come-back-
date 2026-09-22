"""Regression contracts for Oracle auditability upgrades 1-5."""
from pathlib import Path

def _source(path):
    return Path(path).read_text(encoding="utf-8")

def test_brain_exposes_authoritative_trade_lifecycle_and_generation_economics():
    source = _source("pages/3_Oracle_Brain.py")
    assert "Paper Trade Lifecycle" in source
    assert "Complete entry → exit" in source
    assert "Exact entry provenance" in source
    assert "Generation economics" in source
    assert "provenance_version>=2" in source
    assert "g.config_hash=o.config_hash" in source

def test_generation_report_is_isolated_and_research_only():
    source = _source("paper_aeve_generation_controller.py")
    assert "def generation_research_report" in source
    assert "config_hash=%s AND provenance_version=%s" in source
    assert '"avoided_losses"' in source
    assert '"missed_winners"' in source
    assert '"execution_impact": "NONE"' in source

def test_existing_provider_and_brain_provenance_layers_remain_present():
    assert Path("runtime_provider_reliability.py").exists()
    brain = _source("oracle_brain_learning.py")
    assert "feature_snapshot" in brain
    assert "entry_signal_id" in brain
    assert '"execution_impact": "NONE"' in brain
