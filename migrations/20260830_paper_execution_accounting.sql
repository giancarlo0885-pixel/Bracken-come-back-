ALTER TABLE trades ADD COLUMN IF NOT EXISTS fees DOUBLE PRECISION NOT NULL DEFAULT 0;
ALTER TABLE trades ADD COLUMN IF NOT EXISTS gross_realized_pnl DOUBLE PRECISION;

CREATE TABLE IF NOT EXISTS paper_orders (
    id BIGSERIAL PRIMARY KEY,
    order_id TEXT NOT NULL UNIQUE,
    market TEXT NOT NULL,
    symbol TEXT NOT NULL,
    side TEXT NOT NULL,
    order_type TEXT NOT NULL DEFAULT 'MARKET_IOC',
    status TEXT NOT NULL,
    requested_quantity DOUBLE PRECISION,
    requested_notional DOUBLE PRECISION,
    filled_quantity DOUBLE PRECISION NOT NULL DEFAULT 0,
    filled_notional DOUBLE PRECISION NOT NULL DEFAULT 0,
    remaining_quantity DOUBLE PRECISION NOT NULL DEFAULT 0,
    remaining_notional DOUBLE PRECISION NOT NULL DEFAULT 0,
    reference_price DOUBLE PRECISION,
    average_fill_price DOUBLE PRECISION,
    fee_amount DOUBLE PRECISION NOT NULL DEFAULT 0,
    fee_pct DOUBLE PRECISION NOT NULL DEFAULT 0,
    liquidity_value DOUBLE PRECISION,
    participation_rate DOUBLE PRECISION,
    quote_provider TEXT,
    quote_timestamp TEXT,
    reason TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_paper_orders_market_created
    ON paper_orders(market, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_paper_orders_symbol_created
    ON paper_orders(market, symbol, created_at DESC);

CREATE TABLE IF NOT EXISTS paper_fills (
    id BIGSERIAL PRIMARY KEY,
    fill_id TEXT NOT NULL UNIQUE,
    order_id TEXT NOT NULL REFERENCES paper_orders(order_id) ON DELETE CASCADE,
    market TEXT NOT NULL,
    symbol TEXT NOT NULL,
    side TEXT NOT NULL,
    quantity DOUBLE PRECISION NOT NULL,
    reference_price DOUBLE PRECISION,
    fill_price DOUBLE PRECISION NOT NULL,
    notional DOUBLE PRECISION NOT NULL,
    fee_amount DOUBLE PRECISION NOT NULL DEFAULT 0,
    fee_pct DOUBLE PRECISION NOT NULL DEFAULT 0,
    slippage_pct DOUBLE PRECISION NOT NULL DEFAULT 0,
    spread_pct DOUBLE PRECISION NOT NULL DEFAULT 0,
    market_impact_pct DOUBLE PRECISION NOT NULL DEFAULT 0,
    latency_pct DOUBLE PRECISION NOT NULL DEFAULT 0,
    quote_provider TEXT,
    quote_timestamp TEXT,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_paper_fills_order
    ON paper_fills(order_id, created_at ASC);
