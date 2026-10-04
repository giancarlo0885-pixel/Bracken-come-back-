from pages import __init__  # noqa: F401

def test_aeve_progress_query_contract():
    source = open("pages/3_Oracle_Brain.py", encoding="utf-8").read()
    assert "AEVE Generation Progress" in source
    assert "o.provenance_version = 6" in source
    assert "entry_evidence_complete" in source
    assert "o.config_hash = g.config_hash" in source
    assert "o.entry_evidence_complete AND o.would_trade" in source
    assert "Verified outcomes" in source
    assert "Incomplete, legacy, or incompatible outcomes" in source
