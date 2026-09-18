from pathlib import Path


def test_oracle_city_page_is_read_only_observability():
    source = Path("pages/2_Oracle_City.py").read_text(encoding="utf-8")
    assert "Oracle City is read-only" in source
    assert 'SELECT * FROM market_worker_status' in source
    assert 'SELECT * FROM portfolios' in source
    assert 'SELECT * FROM positions' in source
    assert 'SELECT * FROM trades' in source
    assert 'FROM opportunity_rankings' in source
    assert 'FROM oracle_decision_audit' in source
    assert "UPDATE portfolios" not in source
    assert "INSERT INTO trades" not in source
    assert "ENABLE_BROKER_SUBMISSION=true" not in source
    assert "LIVE_TRADING_ARMED=true" not in source
    assert "submit_order(" not in source
