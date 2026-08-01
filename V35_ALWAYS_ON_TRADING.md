# V35 Always-On Trading

- Continuous stock and crypto worker loops
- Rolling intraday candidate scans between deep global scans
- Open-position price and risk monitoring on every pulse
- Immediate qualified paper entries and exits
- Automatic capital rotation when a materially stronger opportunity appears
- Automatic retry after provider, database, or cycle failures
- Railway `ALWAYS` restart policies preserved
- No forced trades: missing, stale, weak, or unprofitable setups remain rejected

## Recommended Railway variables

```text
ALWAYS_ON_TRADING=true
FAST_SIGNAL_SCAN_ENABLED=true
STOCK_PULSE_SECONDS=10
CRYPTO_PULSE_SECONDS=5
STOCK_FAST_SCAN_SECONDS=15
CRYPTO_FAST_SCAN_SECONDS=10
FAST_SCAN_BATCH_SIZE=10
FAST_SCAN_TOP_RANKED=20
STOCK_DEEP_SCAN_SECONDS=60
STOCK_CLOSED_SCAN_SECONDS=120
CRYPTO_DEEP_SCAN_SECONDS=30
WORKER_CYCLE_ERROR_BACKOFF_SECONDS=5
REALTIME_MODE=true
ENABLE_AUTOTRADE=true
EXECUTION_MODE=paper
PAPER_CAPITAL_UPGRADE=false
```
