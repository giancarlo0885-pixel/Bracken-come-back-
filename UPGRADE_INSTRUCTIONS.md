# Upgrade to V33 Institutional Paper Broker

V33 keeps all execution simulated while giving the stock and crypto workers institutional-size paper capital and controlled broker leverage.

## Default paper accounts

- Stock equity: **$10,000,000**
- Stock leverage limit: **4.0x**
- Crypto equity: **$5,000,000**
- Crypto leverage limit: **2.0x**
- Hard margin-utilization ceiling: **82% of the configured leverage capacity**
- Automatic margin reduction begins if the hard ceiling or maintenance requirement is breached.
- Paper financing costs accrue against borrowed capital.

## Deployment

1. Replace the repository files with this package.
2. Keep PostgreSQL and the existing API secrets.
3. Web start command: `python start_web.py`
4. Stock worker start command: `python stock_worker.py`
5. Crypto worker start command: `python crypto_worker.py`
6. Link the same `DATABASE_URL` to all three services.
7. Copy the V33 variables from `railway_variables.example` to the web and both workers.
8. Deploy all three services.
9. Confirm the banner says **LIVE INSTITUTIONAL PAPER BROKER**.
10. Open Portfolios and confirm Broker Equity, Buying Power, Gross Exposure, Margin Used, Excess Liquidity, and Leverage are visible.

## Existing paper portfolios

`PAPER_CAPITAL_UPGRADE=true` raises legacy paper starting capital while preserving existing positions and historical profit/loss. It adds the difference to cash and updates `starting_balance` once. Set it to `false` after the upgrade if you never want future automatic capital increases.

## Important

This package does not connect to or submit orders to a real brokerage. Leverage, margin debt, interest, buying power, maintenance requirements, and forced reductions are all simulated in PostgreSQL.
