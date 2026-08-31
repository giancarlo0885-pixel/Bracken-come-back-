# Robinhood Crypto read-only preflight

When `ROBINHOOD_CRYPTO_ENABLED=true`, the crypto worker performs a read-only Robinhood connectivity preflight before starting the normal market loop. The check does not place, preview-submit, cancel, or mutate orders. It logs only sanitized status fields for connection, authentication, account status, tradable crypto availability, quote validation, buying power, order journal readiness, and whether live trading remains armed/disarmed.

Production should keep `EXECUTION_MODE=paper`, `ENABLE_BROKER_SUBMISSION=false`, and `LIVE_TRADING_ARMED=false` until the read-only preflight passes and live trading is deliberately authorized.
