# GARIBALDI MARKET ORACLE™ — Institutional Paper Broker

A mobile-first AI Chief Investment Officer with continuously scanning stock and crypto workers, institutional-size simulated capital, controlled paper leverage, realistic buying power, financing costs, maintenance requirements, automatic margin reduction, and clear trade decisions.

## V33 Institutional Paper Broker

V33 upgrades the simulation from a small cash account into two paper prime-broker accounts:

- **Stock worker:** $10,000,000 paper equity with up to 4.0x broker leverage.
- **Crypto worker:** $5,000,000 paper equity with up to 2.0x broker leverage.
- **Risk buffer:** the workers stop adding exposure before the full leverage limit is reached.
- **Margin protection:** weak positions are automatically reduced if maintenance requirements or the utilization ceiling are breached.
- **Realistic financing:** simulated margin interest accrues over time.
- **Liquidity discipline:** large paper orders can be capped by a percentage of average dollar volume when that data is available.

The app displays Broker Equity, Buying Power, Gross Exposure, Margin Debt, Leverage Used, Excess Liquidity, and Margin Utilization in plain language. All orders remain simulated.

## Execution mode

The included build uses:

```text
EXECUTION_MODE=paper
```

It continuously performs **simulated trades** in PostgreSQL. It does not send real-money orders to a brokerage. A real broker would require a separately reviewed execution adapter, brokerage credentials stored only in Railway variables, order-state reconciliation, duplicate-order protection, and an emergency kill switch.

## Main navigation

1. **Dashboard** — live worker status, portfolio health, strongest decision, action plan, and current risks.
2. **Markets** — ranked stock and crypto opportunities plus price-history charts.
3. **Portfolios** — separate stock and crypto holdings, readable buy/sell history, portfolio advice, and hypothetical analysis.
4. **Oracle** — plain BUY, HOLD, WAIT, and SELL cards using native Streamlit components so HTML cannot leak onto the screen.
5. **Intelligence** — macro, policy, earnings, capital-flow, insider, options, and global events.
6. **Professional** — evidence ledger, backtests, provider health, and raw signals.

## Continuous adaptation

The application does not rewrite its own source code while running. It continuously updates its market evidence and recommendations. The existing Market Memory module records completed trade outcomes and uses market-history analogs to improve later quality scores without profiling the user's personal trading behavior.

## Railway deployment

Keep four Railway resources:

- PostgreSQL
- Web service — `python start_web.py`
- Stock worker — `python stock_worker.py`
- Crypto worker — `python crypto_worker.py`

Link the same `DATABASE_URL` to all three application services. Add the V33 variables from `railway_variables.example` to the web and both worker services. API keys should be stored only in Railway Variables.

## Important runtime variables

```text
REALTIME_MODE=true
EXECUTION_MODE=paper
UI_AUTO_REFRESH=true
UI_REFRESH_SECONDS=15
STOCK_PULSE_SECONDS=15
CRYPTO_PULSE_SECONDS=10
STOCK_DEEP_SCAN_SECONDS=60
STOCK_CLOSED_SCAN_SECONDS=300
CRYPTO_DEEP_SCAN_SECONDS=30
INTELLIGENCE_REFRESH_SECONDS=900
REALTIME_CACHE_TTL_SECONDS=10
LIVE_SCAN_WORKERS=5
LIVE_POSITION_PRICE_WORKERS=4
DEEP_ANALYSIS_CANDIDATES=35
PROVIDER_RATE_LIMIT_COOLDOWN_SECONDS=900
UNAVAILABLE_SYMBOL_COOLDOWN_SECONDS=1800
GLOBAL_SCANNER_ENABLED=true
GLOBAL_SCAN_SYMBOLS_PER_CYCLE=45
GLOBAL_ACTIVE_CANDIDATES=20
GLOBAL_INCLUDE_PROVIDER_DISCOVERY=true
GLOBAL_CORE_SYMBOLS_PER_CYCLE=12
GLOBAL_ETF_SYMBOLS_PER_CYCLE=8
GLOBAL_MAJOR_MOVER_MIN_CHANGE_PCT=3.0
GLOBAL_GAP_MOVER_MIN_CHANGE_PCT=2.0
GLOBAL_UNUSUAL_VOLUME_MIN_RATIO=1.8
PENNY_STOCK_MIN_PRICE=0.50
PENNY_STOCK_MAX_PRICE=5.00
PENNY_STOCK_MIN_AVG_DOLLAR_VOLUME=2500000
PENNY_STOCK_MAX_TRADE_VALUE_PCT=0.01
```

Lower intervals increase provider traffic and can trigger rate limits. The defaults balance responsiveness with API reliability.

## Production data scanner

The stock worker now combines the fixed watchlist with dynamic discovery. Every scan keeps recurring coverage for GOOGL, GOOG, AMZN, AAPL, MSFT, NVDA and other core stocks, plus major ETFs. The rotating universe also supports blue-chip core stocks, large caps, mid caps, small caps, qualified penny stocks, ETFs, major gainers, major losers, gap movers, unusual-volume names, and global liquid leaders.

Major movers are discovered by provider-backed price history: the scanner ranks one-day change, five-day change, relative volume, volatility, and average dollar volume. Provider errors, rate limits, and unavailable symbols enter temporary cooldowns so a bad symbol such as an unavailable crypto pair does not stop either worker.

Qualified penny stocks are kept in their own category and must pass separate minimum price and dollar-volume gates. The paper broker can further restrict sizing through `PENNY_STOCK_MAX_TRADE_VALUE_PCT`.

## Risk controls

Continuous operation does not mean forced trading. A trade still must pass signal, evidence, positive expected-value, portfolio, concentration, cash, correlation, cooldown, and risk checks. The workers keep scanning when no trade qualifies and execute only when all enabled rules approve the paper order.

## V34 live data-integrity rules

The normal investor interface will never label an asset as a trade-ready BUY unless it has a positive live price, a current forecast target, acceptable data freshness, and a minimum expected move. Incomplete or stale records remain visible as WAIT with a plain-English reason. Global asset quotes retain their native currency label (for example JPY or INR) while portfolio totals remain USD.

## V35 Always-On Trading Engine

The stock and crypto workers now use three simultaneous cadences:

1. **Risk pulse** refreshes open positions and enforces stops.
2. **Fast rolling scan** continuously rechecks holdings, recent leaders, and a rotating part of the universe.
3. **Deep global scan** performs broader research, news analysis, ranking, and market-memory work.

The workers automatically retry after cycle errors and Railway is configured to restart them if the process exits. The engine never forces a low-quality trade: it remains active at all times and executes immediately when a candidate passes live-data, forecast, portfolio, quant, leverage, and risk gates. When the portfolio is full, it can rotate out of a materially weaker holding for a stronger approved opportunity.
