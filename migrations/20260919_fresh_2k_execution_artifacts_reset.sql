-- Finish the fresh $2,000 reset by clearing paper execution artifacts that
-- participate in accounting reconciliation, while archiving them for audit.
INSERT INTO paper_reset_archive(reset_id, source_table, row_data, archived_at)
SELECT 'fresh-2k-20260919', 'paper_fills', to_jsonb(row_data), timezone('utc', now())::text
FROM paper_fills AS row_data
WHERE market IN ('cash', 'crypto');

INSERT INTO paper_reset_archive(reset_id, source_table, row_data, archived_at)
SELECT 'fresh-2k-20260919', 'paper_orders', to_jsonb(row_data), timezone('utc', now())::text
FROM paper_orders AS row_data
WHERE market IN ('cash', 'crypto');

DELETE FROM paper_fills WHERE market IN ('cash', 'crypto');
DELETE FROM paper_orders WHERE market IN ('cash', 'crypto');

-- Seed a clean baseline snapshot so accounting invariants start from the new run.
INSERT INTO equity_snapshots(market, equity, cash, positions_value, drawdown, created_at)
VALUES
    ('cash', 2000, 2000, 0, 0, timezone('utc', now())::text),
    ('crypto', 2000, 2000, 0, 0, timezone('utc', now())::text);
