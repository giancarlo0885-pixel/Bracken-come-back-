from pathlib import Path


ROOT = Path(__file__).parent
RESET_SQL = ROOT / "migrations" / "20260830_archive_and_reset_paper_portfolios.sql"


def test_small_account_defaults_are_safe(monkeypatch):
    for name in (
        "STARTING_BALANCE",
        "STOCK_STARTING_BALANCE",
        "CRYPTO_STARTING_BALANCE",
        "STOCK_PAPER_LEVERAGE",
        "CRYPTO_PAPER_LEVERAGE",
        "PAPER_BROKER_PROFILE",
    ):
        monkeypatch.delenv(name, raising=False)

    import importlib
    import config

    config = importlib.reload(config)
    assert config.STARTING_BALANCE == 2000
    assert config.STOCK_STARTING_BALANCE == 2000
    assert config.CRYPTO_STARTING_BALANCE == 2000
    assert config.STOCK_PAPER_LEVERAGE == 1
    assert config.CRYPTO_PAPER_LEVERAGE == 1
    assert config.PAPER_BROKER_PROFILE == "small-account-paper"


def test_reset_archives_every_active_paper_table_before_deleting():
    sql = RESET_SQL.read_text(encoding="utf-8")
    tables = (
        "positions",
        "trades",
        "trade_ledger",
        "position_lots",
        "executions",
        "equity_snapshots",
        "portfolio_rotations",
    )

    for table in tables:
        archive_at = sql.index(f"'{table}', to_jsonb")
        delete_at = sql.index(f"DELETE FROM {table}")
        assert archive_at < delete_at

    assert "SET cash = 2000" in sql
    assert "starting_balance = 2000" in sql
    assert "leverage_limit = 1" in sql
