# V34 Live Data Integrity

This release prevents stale or incomplete market records from appearing as trade-ready BUY recommendations or reaching the institutional paper broker as new entries.

## Trade-readiness gate

A BUY must have:

- a positive current market price;
- a positive current forecast target;
- data inside the configured freshness window;
- forecast upside above the configured minimum expected-move threshold.

Records that fail a requirement are shown as WAIT with the exact reason. They remain visible for transparency but are excluded from the Dashboard's Top Live Opportunities and the trade-ready buy count. The same forecast/freshness/edge checks are enforced immediately before a new paper purchase.

## Global price labels

Individual asset prices retain their quote currency, including JPY and INR. Portfolio account totals remain USD.

## New Railway variables

- `DECISION_STOCK_MAX_AGE_MINUTES=180`
- `DECISION_CRYPTO_MAX_AGE_MINUTES=45`
- `MIN_ACTIONABLE_MOVE_STOCK_PCT=0.75`
- `MIN_ACTIONABLE_MOVE_CRYPTO_PCT=1.25`
- `REQUIRE_TARGET_FOR_BUY=true`
