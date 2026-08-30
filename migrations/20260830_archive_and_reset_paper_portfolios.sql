-- Archive the corrupted institutional-scale simulation before resetting the
-- active stock and crypto paper accounts. schema_migrations and the advisory
-- lock in migrations.py make this transaction run exactly once.
CREATE TABLE IF NOT EXISTS paper_reset_archive (
    id BIGSERIAL PRIMARY KEY,
    reset_id TEXT NOT NULL,
    source_table TEXT NOT NULL,
    row_data JSONB NOT NULL,
    archived_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_paper_reset_archive_lookup
    ON paper_reset_archive(reset_id, source_table);

INSERT INTO paper_reset_archive(reset_id, source_table, row_data, archived_at)
SELECT 'small-account-20260830', 'portfolios', to_jsonb(row_data), timezone('utc', now())::text
FROM portfolios AS row_data
WHERE market IN ('cash', 'crypto');

INSERT INTO paper_reset_archive(reset_id, source_table, row_data, archived_at)
SELECT 'small-account-20260830', 'positions', to_jsonb(row_data), timezone('utc', now())::text
FROM positions AS row_data
WHERE market IN ('cash', 'crypto');

INSERT INTO paper_reset_archive(reset_id, source_table, row_data, archived_at)
SELECT 'small-account-20260830', 'trades', to_jsonb(row_data), timezone('utc', now())::text
FROM trades AS row_data
WHERE market IN ('cash', 'crypto');

INSERT INTO paper_reset_archive(reset_id, source_table, row_data, archived_at)
SELECT 'small-account-20260830', 'trade_ledger', to_jsonb(row_data), timezone('utc', now())::text
FROM trade_ledger AS row_data
WHERE market IN ('cash', 'crypto');

INSERT INTO paper_reset_archive(reset_id, source_table, row_data, archived_at)
SELECT 'small-account-20260830', 'position_lots', to_jsonb(row_data), timezone('utc', now())::text
FROM position_lots AS row_data
WHERE market IN ('cash', 'crypto');

INSERT INTO paper_reset_archive(reset_id, source_table, row_data, archived_at)
SELECT 'small-account-20260830', 'executions', to_jsonb(row_data), timezone('utc', now())::text
FROM executions AS row_data
WHERE market IN ('cash', 'crypto');

INSERT INTO paper_reset_archive(reset_id, source_table, row_data, archived_at)
SELECT 'small-account-20260830', 'equity_snapshots', to_jsonb(row_data), timezone('utc', now())::text
FROM equity_snapshots AS row_data
WHERE market IN ('cash', 'crypto');

INSERT INTO paper_reset_archive(reset_id, source_table, row_data, archived_at)
SELECT 'small-account-20260830', 'portfolio_rotations', to_jsonb(row_data), timezone('utc', now())::text
FROM portfolio_rotations AS row_data
WHERE market IN ('cash', 'crypto');

DELETE FROM position_lots WHERE market IN ('cash', 'crypto');
DELETE FROM trade_ledger WHERE market IN ('cash', 'crypto');
DELETE FROM executions WHERE market IN ('cash', 'crypto');
DELETE FROM trades WHERE market IN ('cash', 'crypto');
DELETE FROM positions WHERE market IN ('cash', 'crypto');
DELETE FROM equity_snapshots WHERE market IN ('cash', 'crypto');
DELETE FROM portfolio_rotations WHERE market IN ('cash', 'crypto');

UPDATE portfolios
SET cash = 2000,
    starting_balance = 2000,
    leverage_limit = 1,
    margin_debt = 0,
    margin_interest_accrued = 0,
    margin_interest_updated_at = timezone('utc', now())::text,
    broker_profile = 'small-account-paper',
    peak_equity = 2000,
    risk_state = 'normal',
    updated_at = timezone('utc', now())::text
WHERE market IN ('cash', 'crypto');
