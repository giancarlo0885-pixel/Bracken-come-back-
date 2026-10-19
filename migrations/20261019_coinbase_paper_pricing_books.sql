-- Additive, forward-only paper simulation book evidence.
-- Historical fills remain unchanged and unverified; no backfill or deletion.
-- A pricing receipt, when present, is inserted in the SAME transaction as
-- the canonical paper fill, trade, lot and ledger graph.
CREATE TABLE IF NOT EXISTS paper_fill_pricing_books (
    fill_id TEXT PRIMARY KEY REFERENCES paper_fills(fill_id) ON DELETE RESTRICT,
    canonical_trade_id BIGINT NOT NULL REFERENCES trades(id) ON DELETE RESTRICT,
    provider TEXT NOT NULL CHECK (provider = 'Coinbase Exchange public depth'),
    market TEXT NOT NULL CHECK (market = 'crypto'),
    symbol TEXT NOT NULL,
    side TEXT NOT NULL CHECK (side IN ('BUY','SELL')),
    book_sequence BIGINT NOT NULL CHECK (book_sequence >= 0),
    book_sha256 CHAR(64) NOT NULL,
    ticker_exchange_time TIMESTAMPTZ NOT NULL,
    received_at TIMESTAMPTZ NOT NULL,
    receipt JSONB NOT NULL CHECK (octet_length(receipt::text) <= 32768),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE(canonical_trade_id)
);
