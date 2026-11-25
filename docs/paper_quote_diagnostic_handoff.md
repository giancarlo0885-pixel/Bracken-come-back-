# Paper quote handoff and diagnostic database latency

## Confirmed defect

Both public paper adapters returned a validated quote from inside a `try` block
but synchronously published diagnostic health in `finally`. Python executes
that `finally` before returning. A diagnostic database lock or slow commit could
therefore consume the original five-second quote lifetime before the caller
received the price. Freshness guards correctly rejected the expired receipt;
relaxing those guards would make the situation worse.

Two installed-runtime regression cases fail against main `8e68852`: a blocked
diagnostic commit prevents Coinbase and Kraken quote handoff. A separate
synthetic reproduction advances the clock by six seconds during commit and
returns a quote that immediately fails the unchanged `authentic()` check.

Production logs on 2026-11-25 show fresh Kraken reference books (under 0.3 seconds
old in the inspected samples), alongside expired cached paper receipts and
occasional unavailable source books. Some expired health records are simply
last-request receipts that have not been requested again. They do not prove a
feed outage. The contribution of synchronous diagnostic commits to production
staleness is not measured by those logs alone.

## Targeted correction

The installed pricing adapter starts one daemon that publishes diagnostic health
to the existing singleton table. Quote finalization only submits a deep-copied
snapshot; database and logging I/O never own the pricing lock or queue lock.
The queue retains one latest pending snapshot, capped at eight symbols, while
an in-flight snapshot may be publishing. Intermediate diagnostic snapshots may
be coalesced. Canonical fills, decisions and learning evidence are never queued
or coalesced by this component.

The publisher owns its publication/throttle state independently. Successful
delivery is acknowledged only after commit. Failed writes retry after five
seconds without requiring another quote. Existing identical-unavailable-state
throttling remains. Original quote timestamps are retained and rechecked at
publication/log time; no diagnostic work fetches or re-dates a quote. The existing
database-status reader independently checks the persisted source timestamp.

The existing worker shutdown event stops idle publication promptly. A database
operation already in progress is not cancelled; `stop(timeout=1)` reports
whether the daemon has finished. Queue storage is bounded, but database write
completion time is not guaranteed. No new Railway service, subscription,
provider allowance, schema, migration, retention rule or evidence capacity is
introduced. Standalone/log-only adapters retain synchronous diagnostic behavior.

No changes to quote TTL, CRC/depth checks, crossed-book rejection, model vetoes,
forecast identity, entry economics, sizing, risk controls or fill accounting.
The source remains paper simulation only. There is no live order gateway.

## Validation and limits

Local Python 3.11 validation: 3,069 tests passed, 225 skipped. The full configured
disposable PostgreSQL selection passed 313 tests, including the new real row-lock
test. Focused health/handoff tests passed 17 cases; the broader adapter/forecast
selection passed 119 with two PostgreSQL cases skipped in that invocation.
The helper's six concurrency/retry tests passed five consecutive runs.
Compilation and whitespace checks passed. Exact-head GitHub CI is recorded in
the draft PR; local tests do not imply deployment.

The PostgreSQL test locks the actual diagnostic singleton row on a separate
connection. The quote still returns and authenticates before lock release;
the unchanged source timestamp is durably published after release. All test
books, forecasts and fills are synthetic. Existing accounting regression
selection also passed 31 disposable PostgreSQL cases after the local test
database was restarted. No production SQL was executed.

## Current quote-to-decision trace

A sampled ADA candidate at 21:27 UTC received fresh paper pricing and retained
signal/forecast IDs, but its approximately 0.024% expected move was below the
approximately 1.977% required cost-buffered edge. Its HOLD is an intentional
economic safeguard, not a software execution blockage to remove. This patch
does not establish positive expectancy or authorize such candidates.

Original-entry and fee evidence is required for historical reconciliation.
The 21:28 UTC production reconciliation still reports 1,248 FIFO identity
mismatches and 1,776 unverified fill/fee exclusions; these overlap. No new
nonduplicative accounting persistence defect was reproduced. Missing original
links cannot be safely inferred or reconstructed from summary counts.

Remaining limitations include source publication cadence (held #401), separate
provider/metadata edge cases, the full 15,000-ID research boundary, incomplete
decision-time Brain-consumption proof, and negative/unqualified economics.
These are not silently resolved by this handoff correction. #521 remains a
separate unmerged draft; its receipt-query and challenger-report fixes are not
duplicated here. #393 remains held and #312 is untouched.

Disposition: draft only. No merge, deployment, infrastructure increase, billing
change, historical rewrite or trading-permission change. The $20 monthly target
remains; actual bill and billing-cap enforcement remain independently unverified.
Removing diagnostic latency is an engineering improvement, not a profit claim.
