-- Additive, non-destructive, forward-only canonical fill attribution.
-- Old paper_fills remain NULL/unverified; never infer missing IDs by time/price.
ALTER TABLE paper_fills
    ADD COLUMN IF NOT EXISTS canonical_trade_id BIGINT REFERENCES trades(id);

CREATE UNIQUE INDEX IF NOT EXISTS idx_paper_fills_exact_trade
    ON paper_fills(canonical_trade_id)
    WHERE canonical_trade_id IS NOT NULL;

CREATE TABLE IF NOT EXISTS paper_fill_lot_links (
    fill_id TEXT NOT NULL REFERENCES paper_fills(fill_id) ON DELETE RESTRICT,
    lot_id TEXT NOT NULL REFERENCES position_lots(lot_id) ON DELETE RESTRICT,
    ledger_trade_id TEXT NOT NULL REFERENCES trade_ledger(trade_id) ON DELETE RESTRICT,
    quantity DOUBLE PRECISION NOT NULL CHECK (quantity > 0),
    PRIMARY KEY (fill_id, ledger_trade_id),
    UNIQUE (ledger_trade_id)
);

CREATE INDEX IF NOT EXISTS idx_paper_fill_lot_links_lot
    ON paper_fill_lot_links(lot_id);
