# SUPER CRYPTO ORACLE — staged migration contract (research-only)

Status: proposal; no runtime changes or live trading authorization.

## Mission
Concentrate future discovery, research, paper execution and City/Brain presentation on cryptocurrency and blockchain networks. Preserve stock history read-only, canonical paper fills, fees, ledger identities, provenance and Brain memories. Do not delete stock data or services until verified rollback is possible.

## Staged implementation
1. **Baseline and audit**: inventory stock/crypto services, live settings, data-provider coverage, per-scan candidate limits, all existing City/Brain routes, tests and deployment health. Record before/after metrics.
2. **Crypto-only display scope**: feature-flag crypto-first navigation and City ecosystem views; keep archived stock history accessible read-only. No migrations deleting stock data.
3. **Full-universe discovery**: decouple symbol discovery from costly deep analysis. Page through provider universes with rate limits, liquidity/quality screening, freshness tracking, queue backpressure, deduplication and symbol/chain/contract identity; no hardcoded 16-symbol truncation. Track discovered, eligible, analyzed, deferred and rejected counts.
4. **Blockchain evidence**: ingest only verifiable chain-specific signals (exchange flows, wallet concentration, DEX liquidity, token unlocks, funding/open interest) with timestamps, provenance, staleness and source-health flags. Missing evidence abstains rather than fabricating a signal.
5. **Winner-conditioned learning**: reconstruct unique winning and losing canonical paper trades from immutable FIFO fills and decision-time feature snapshots. Treat QNT/ONDO/SYRUP and earlier BTC winners as hypotheses, not a qualified edge. Group overlapping trades by independent market episode, preserve fee/slippage economics, split training vs forward holdout chronologically, quantify false-positive and missed-winner costs.
6. **Aggressive research, bounded execution**: increase parallel *shadow* candidate experiments and adaptive scan scheduling within provider budgets; do not loosen crossed-book, stale-quote, expected-edge, exposure or cost gates. Any new formula/weights start research-only; promote only after statistically meaningful independent episodes and positive post-cost out-of-sample expectancy.
7. **City/Brain**: show blockchain ecosystem districts, data freshness, learned vs hypothesized edges, winners/losers and capital/equity based on canonical records. Brain memory may influence ranked research proposals but cannot bypass final trade risk gates. Preserve responsive centering/zoom.
8. **Validation and rollout**: add unit/integration/regression tests for identity, data quality, no-lookahead, paper accounting, feature flag, and stock archival. Require clean exact-head CI and shadow evaluation before production rollout; deploy in reversible stages with health checks.

## Non-negotiable safeguards
- EXECUTION_MODE=paper; LIVE_TRADING_ARMED=false; ENABLE_BROKER_SUBMISSION=false; retain kill switches.
- No real-money orders; no broker credentials or secrets in commits.
- Do not claim a guaranteed profit or increase trade frequency merely to force wins.
- No schema-destructive migrations, no rewriting historical trades or evidence.
- No production switch or PR merge without clean exact-head CI and verified rollback.

## Acceptance criteria
- Crypto-only City/Brain feature flag renders correctly on mobile and desktop.
- All discovered crypto candidates accounted for each scan (processed/deferred/rejected with reasons).
- Historical winner and loser attribution deduplicated and reconciled to canonical paper ledger.
- Shadow strategy report includes net P&L, fees, expectancy, profit factor, drawdown, independent episodes, confidence intervals and unseen-period results.
- Stock history remains readable; live trading remains disarmed.
