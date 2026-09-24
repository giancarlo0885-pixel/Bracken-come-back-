from pathlib import Path


def test_bootstrap_skips_redundant_portfolio_column_ddl():
    source = Path("database.py").read_text(encoding="utf-8")
    assert "information_schema.columns" in source
    assert "existing_portfolio_columns" in source
    assert 'normalized.startswith("alter table portfolios add column if not exists")' in source
    assert "column in existing_portfolio_columns" in source


def test_bootstrap_keeps_missing_portfolio_column_repairs_available():
    source = Path("database.py").read_text(encoding="utf-8")
    for column in (
        "broker_profile", "leverage_limit", "margin_debt",
        "margin_interest_accrued", "margin_interest_updated_at",
        "peak_equity", "risk_state",
    ):
        assert f"ADD COLUMN IF NOT EXISTS {column}" in source
