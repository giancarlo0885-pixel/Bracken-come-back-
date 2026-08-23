# GARIBALDI MARKET ORACLE — Codex Instructions

## Runtime
- Use Python 3.11, matching `runtime.txt` and the Docker image.
- Install dependencies with `python -m pip install -r requirements.txt`.
- Validate changes with `python -m compileall .` and `python -m pytest`.

## Safety and execution invariants
- Keep `EXECUTION_MODE=paper`.
- Never turn on `ENABLE_AUTOTRADE`, `ENABLE_STOCK_AUTOTRADE`, `ENABLE_CRYPTO_AUTOTRADE`,
  `ENABLE_NEW_ENTRIES`, `ENABLE_AUTOMATED_EXITS`, `ENABLE_PORTFOLIO_ROTATION`, or
  `ENABLE_BROKER_SUBMISSION` unless the user explicitly requests that exact change.
- Never weaken verified-quote identity/freshness checks, forecast linkage, concentration,
  cash-reserve, sector, correlation, margin, turnover, drawdown, or duplicate-execution gates.
- Never commit API keys, database credentials, tokens, `.env` files, or provider secrets.

## Data integrity
- Missing provider data must remain missing/unknown; do not invent liquidity, quality, prices,
  timestamps, symbols, news, or provider support.
- A capital-planning candidate must use the same verified quote identity/freshness standard
  as the paper execution path.
- Provider plan limitations must degrade gracefully through existing fallbacks.

## Database
- Tables already listed in `CANONICAL_PROTECTED_TABLES` must never be auto-deleted by retention cleanup.
- High-frequency analytical tables must use bounded retention and PostgreSQL-safe migrations.
- Database changes must remain concurrency-safe for the web, stock worker, and crypto worker.

## Scope
- Prefer focused, reversible changes.
- Do not change the trading formula, thresholds, starting balances, leverage, or provider
  entitlements as part of infrastructure/hardening work unless explicitly requested.
