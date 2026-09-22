from pages import __init__  # noqa: F401

def test_aeve_progress_query_contract():
    source = open("pages/3_Oracle_Brain.py", encoding="utf-8").read()
    assert "AEVE Generation Progress" in source
    assert "o.provenance_version >= 2" in source
    assert "o.config_hash = g.config_hash" in source
    assert "WHERE o.would_trade" in source
    assert "Verified trades" in source
    assert "Council approvals, scans, quote handoffs" in source
