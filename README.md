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
MARKET_SCOPE=US_CRYPTO
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
FINNHUB_CRYPTO_DAILY_REQUEST_BUDGET=0
EODHD_CRYPTO_DAILY_REQUEST_BUDGET=0
FINNHUB_CRYPTO_EXCHANGES=BINANCE,COINBASE,KRAKEN
ENABLE_AUTOTRADE=false
ENABLE_STOCK_AUTOTRADE=false
ENABLE_CRYPTO_AUTOTRADE=false
ENABLE_NEW_ENTRIES=false
ENABLE_AUTOMATED_EXITS=false
ENABLE_PORTFOLIO_ROTATION=false
ENABLE_BROKER_SUBMISSION=false
ENABLE_OPENAI=false
GLOBAL_KILL_SWITCH=false
LIVE_TRADING_ARMED=false
LIVE_ORDER_APPROVAL_MODE=manual
BROKER_MODE=paper
LIVE_TRADING_KILL_SWITCH=true
LIVE_MAX_SINGLE_ORDER_DOLLARS=100
LIVE_MAX_DAILY_NEW_EXPOSURE_DOLLARS=250
LIVE_MAX_DAILY_LOSS_DOLLARS=50
LIVE_MAX_POSITION_PCT=0.05
LIVE_MAX_TOTAL_DEPLOYED_PCT=0.25
PAPER_TAX_LOT_METHOD=FIFO
MAX_RECONCILIATION_DIFFERENCE_PCT=0.005
ROBINHOOD_CRYPTO_ENABLED=false
ROBINHOOD_CRYPTO_API_KEY=
ROBINHOOD_CRYPTO_PRIVATE_KEY_BASE64=
ROBINHOOD_CRYPTO_API_VERSION=v2
ROBINHOOD_CRYPTO_BASE_URL=https://trading.robinhood.com
ROBINHOOD_CRYPTO_API_MODE=disabled
CRYPTO_PRIORITY_WEIGHT=0.70
STOCK_PRIORITY_WEIGHT=0.30
CRYPTO_ALWAYS_INVESTED=true
CRYPTO_CORE_TARGET_PCT=0.30
CRYPTO_TACTICAL_MAX_PCT=0.60
CRYPTO_MIN_CASH_RESERVE_PCT=0.10
CRYPTO_DYNAMIC_UNIVERSE_SIZE=40
CRYPTO_MAX_ACTIVE_SCAN_SYMBOLS=50
CRYPTO_MIN_24H_DOLLAR_VOLUME=25000000
CRYPTO_MAX_SPREAD_PCT=0.02
MAX_SINGLE_CRYPTO_TACTICAL_POSITION_PCT=0.12
CRYPTO_ROTATION_MIN_SCORE_IMPROVEMENT=7
CRYPTO_FULL_SCAN_SECONDS=120
CRYPTO_SYMBOL_COOLDOWN_MINUTES=20
CRYPTO_ROTATION_COOLDOWN_MINUTES=30
CRYPTO_STOP_OUT_COOLDOWN_MINUTES=60
CRYPTO_BREAKEVEN_TRIGGER_R=1.0
CRYPTO_TRAILING_STOP_TRIGGER_R=1.5
MAX_PORTFOLIO_RISK_PER_TRADE=0.01
MAX_TOTAL_DEPLOYED_PCT=0.90
MIN_TRADE_NOTIONAL=2.00
ENABLE_FRACTIONAL_EQUITIES=true
ENABLE_FRACTIONAL_CRYPTO=true
SMALL_ACCOUNT_THRESHOLD=500
LARGE_ACCOUNT_THRESHOLD=100000
MAX_POSITION_VS_DAILY_DOLLAR_VOLUME_PCT=0.001
MAX_SINGLE_STOCK_POSITION_PCT=0.12
STOCK_MIN_CASH_RESERVE_PCT=0.15
STOCK_CORE_TARGET_PCT=0.40
GLOBAL_SCANNER_ENABLED=true
GLOBAL_SCAN_SYMBOLS_PER_CYCLE=45
GLOBAL_ACTIVE_CANDIDATES=20
GLOBAL_CANDIDATE_TTL_SECONDS=1800
GLOBAL_INCLUDE_PROVIDER_DISCOVERY=true
GLOBAL_CORE_SYMBOLS_PER_CYCLE=12
GLOBAL_ETF_SYMBOLS_PER_CYCLE=8
GLOBAL_MAJOR_MOVER_MIN_CHANGE_PCT=3.0
GLOBAL_GAP_MOVER_MIN_CHANGE_PCT=2.0
GLOBAL_UNUSUAL_VOLUME_MIN_RATIO=1.8
GLOBAL_PIT_MODE=true
GLOBAL_PIT_FAST_LOOP_SECONDS=5
GLOBAL_PIT_MARKET_LOOP_SECONDS=60
GLOBAL_PIT_DEEP_RESEARCH_SECONDS=900
GLOBAL_PIT_TARGET_INVESTED_PCT=0.95
GLOBAL_PIT_RESERVE_PCT=0.05
GLOBAL_PIT_PREFERRED_POSITION_PCT=0.08
GLOBAL_PIT_MAX_POSITION_PCT=0.10
GLOBAL_PIT_ROTATION_MIN_ADVANTAGE_PCT=2.5
GLOBAL_PIT_MAX_PARALLEL_LANES=9
CORE_SIGNAL_SUPPORT_THRESHOLD=60
MIN_CORE_SIGNALS_AGREE=3
MIN_CONFIDENCE_TO_TRADE=70
MIN_REWARD_RISK_RATIO=1.5
MAX_SECONDARY_SCORE_ADJUSTMENT=5
PENNY_STOCK_MIN_PRICE=0.50
PENNY_STOCK_MAX_PRICE=5.00
PENNY_STOCK_ENABLED=true
PENNY_STOCK_MIN_DAILY_VOLUME=500000
PENNY_STOCK_MIN_AVG_DOLLAR_VOLUME=2500000
PENNY_STOCK_MAX_TRADE_VALUE_PCT=0.01
PENNY_STOCK_MAX_OPEN_POSITIONS=3
PENNY_STOCK_MAX_PORTFOLIO_PCT=0.02
PENNY_STOCK_MIN_SCORE=75
PENNY_STOCK_MIN_CONFIDENCE=0.70
OTC_STOCKS_ENABLED=false
FORECAST_MIN_VALIDATION_SAMPLES=30
FORECAST_MIN_DIRECTIONAL_ACCURACY=0.52
FORECAST_MAX_CALIBRATION_ERROR=0.18
FORECAST_MIN_DATA_QUALITY_SCORE=55
FORECAST_MODEL_VERSION=v36-timeframe-aware
PRICE_CONSENSUS_ENABLED=false
PRICE_CONSENSUS_MAX_DIFF_PCT=0.50
ADVISOR_MODEL_VERSION=v36-advisor-foundation
ADVISOR_RECOMMENDATION_TTL_MINUTES=120
MAX_DAILY_TURNOVER_PCT=0.20
MAX_NEW_ENTRIES_PER_DAY=3
MAX_WEEKLY_LOSS_PCT=0.18
```

Lower intervals increase provider traffic and can trigger rate limits. The defaults balance responsiveness with API reliability.

## Production data scanner

The stock worker now combines the fixed watchlist with dynamic discovery. Every scan keeps recurring coverage for GOOGL, GOOG, AMZN, AAPL, MSFT, NVDA and other core stocks, plus major ETFs. The rotating universe also supports blue-chip core stocks, large caps, mid caps, small caps, qualified penny stocks, ETFs, major gainers, major losers, gap movers, unusual-volume names, and global liquid leaders.

Market Focus keeps the execution universe to U.S.-listed stocks/ETFs and crypto while preserving useful intelligence from broader market context. The scanner uses static watchlists only as seeds, ranks verified stock and crypto opportunities separately, and keeps unsupported assets intelligence-only. The capital planner targets 95% invested / 5% reserve only when enough verified, qualified paper-tradeable opportunities pass freshness, liquidity, concentration, and existing hard risk gates. Dashboard labels are driven by persisted scanner, research, learning, allocation, and rotation state; unknown or delayed data is never labeled live.

## Profit Attribution and Brokerage Readiness

V39 profit attribution adds canonical `trade_ledger`, `position_lots`, and `executions` records so realized P/L is traceable by symbol, market, bucket, strategy, lot, entry, exit, quantity, fees, provider, decision, order, broker mode, and account environment. Realized P/L is calculated from execution and lot records rather than balance changes. Unrealized P/L is shown only when the position has a current verified quote; otherwise the dashboard should display `WAITING FOR VERIFIED PRICE`.

## Wider Crypto Opportunity Engine

The crypto workflow is separate from Wall Street Market Focus. It keeps a protected strategic core basket and scans a wider tactical universe of liquid crypto assets. Tactical candidates must pass verified quote identity, 24/7 freshness, minimum 24h dollar volume, maximum spread, provider capability support, at least three of five supporting signals, and A/B/C tier rules. C-tier trades are smaller rather than automatically denied. Core and tactical quantities are tracked separately so tactical rotations cannot liquidate protected core exposure.

Default paper allocation structure:

- 30% strategic crypto core
- Up to 60% tactical crypto opportunities
- At least 10% crypto cash reserve

The dedicated Crypto page shows summary, current holdings, best crypto trades, strongest crypto movers, rotation opportunities, crypto profit attribution, core allocation, waiting/rejected candidates, and provider diagnostics.

## Adaptive Capital Sizing

Tactical paper entries use one canonical allocator for stocks and crypto. Size is derived from validated equity, validated cash, stop distance, tier, confidence, regime, liquidity, drawdown, current exposure, cash reserve, single-position limits, total deployment limits, and daily-volume participation. Buying power is used only when it is explicitly validated by the paper account path. Small portfolios use fewer positions and smaller orders; large portfolios can scale up while remaining capped by liquidity participation.

Future live brokerage support is staged behind safe modes only:

- `paper`
- `live_read_only`
- `live_preview`
- `live_manual_approval`

Live order submission remains blocked by default through `LIVE_TRADING_KILL_SWITCH=true` and `ENABLE_BROKER_SUBMISSION=false`. A live proposal must pass verified quote identity, freshness, reconciliation, risk checks, daily exposure limits, single-order limits, position concentration, and total deployed limits. Manual approval is tied to an immutable proposal hash; changing quantity, price, symbol, side, or risk payload invalidates the approval. Broker fills, when imported in the future, must override proposal/reference prices with the actual fill price.

Major movers are discovered first through supported provider mover/snapshot capabilities, including Polygon stock snapshots, Alpha Vantage top gainers/losers/most-active data, and EODHD screener signals when those configured plans expose them. The scanner falls back to rotating price-history discovery and ranks one-day change, five-day change, relative volume, volatility, and average dollar volume. Provider errors, rate limits, and unavailable symbols enter temporary cooldowns so a bad symbol such as an unavailable crypto pair does not stop either worker. Candidate rows older than `GLOBAL_CANDIDATE_TTL_SECONDS` are ignored or removed so stale high scores cannot dominate current opportunities.

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

## V39.4 Robinhood Crypto Readiness

Robinhood support is split into two isolated paths: `robinhood_agentic_mcp.py` for an authorized Agentic MCP connection and `robinhood_crypto_api.py` for the direct Crypto API v2. Both default to disconnected/disabled, never use username/password automation, never log keys or signatures, and require the normal Oracle quote, risk, duplicate-order, and live-arming gates before any future live submission path can be considered.

## V36 Forecast And Provider Quality

Forecasts are timeframe-aware: five-minute, hourly, daily, and crypto 24/7 inputs are scaled by their actual bar interval instead of being treated as daily returns. Newly saved forecasts record source interval, source quote timestamp, scan type, model version, expected move, data quality, and requested/provider symbol identity. Execution gates only use a forecast that matches the signal and quote that produced it, so a fast intraday forecast cannot replace a deep daily forecast.

Provider diagnostics include endpoint capability cooldowns. A 402, 403, or plan-limited response disables only that provider capability for a long cooldown while other capabilities can continue. Stock and crypto execution are split behind `ENABLE_STOCK_AUTOTRADE` and `ENABLE_CRYPTO_AUTOTRADE`; advisor scanning, signals, forecasts, rankings, and diagnostics continue while execution is disabled.

Use `python production_audit.py` for a non-destructive paper-data audit of trades and positions created before the V35 symbol-price-isolation merge. The audit writes review markings to a separate table and reports affected symbols, trades, positions, and estimated P/L impact.

## V36 Advisor Foundation

The foundation branch introduces separate modules for advisor recommendations, multi-strategy opportunity scoring, risk checks, portfolio fit, quote consensus, order proposals, shadow trading, broker adapter contracts, performance scorecards, provider budgets, secret redaction, and database health.

Operating modes:

- `advisor-only`: recommendations, explanations, and risk labels only.
- `read-only`: future broker adapters may inspect account data without orders.
- `shadow`: proposed orders and simulated fills are tracked without touching the official paper portfolio.
- `manual-approval paper`: proposals require review before any paper action.
- `automated paper`: remains disabled unless all execution switches are intentionally enabled.
- `live-disabled`: real brokerage submission is unavailable by design.

Execution switches are layered. `ENABLE_AUTOTRADE` remains a legacy compatibility switch, but new entry, automated exit, rotation, stock, crypto, and broker-submission switches default to `false`. Real-money broker execution is not implemented, credentials are not stored in PostgreSQL, and future broker credentials must come only from environment variables.

The advisor dashboard adds sections for Advisor Brief, Opportunities Now, Portfolio Health, Proposed Trades, Watchlist, Strategy Scorecards, Forecast Accuracy, Provider Health, Risk Center, Historical Audit, Worker Status, and Settings. BUY and ACCUMULATE are green, HOLD and WATCH are yellow, REDUCE and CAUTION are orange, and SELL, AVOID, and HALTED are red.

Premium intelligence is never fabricated. If dark-pool, options, insider, congressional, whale, or similar provider data is unavailable, the system reports `Provider not configured` or the active provider limitation. Forecasts and AI explanations must use supplied application data only; they cannot invent prices, news, trades, provider payloads, or override risk rules. Profits are not guaranteed.

Safe deployment procedure:

1. Deploy web, stock worker, crypto worker, and PostgreSQL as separate Railway services.
2. Keep `EXECUTION_MODE=paper` and every new execution switch set to `false` for the advisor pilot.
3. Confirm workers are scanning and provider diagnostics are healthy.
4. Review recommendations and shadow orders before enabling any paper automation.
5. Use `GLOBAL_KILL_SWITCH=true` or leave the market-specific switches false for emergency shutdown.

Future brokerage integration requires a new audited adapter implementation, external broker credentials in environment variables only, order reconciliation, duplicate-order protection, and separate human approval before live submission can ever be considered.
