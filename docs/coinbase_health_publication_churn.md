# Coinbase paper-pricing health publication rate — diagnostic only

The `coinbase_paper_pricing.PaperPricing.quote()` path previously attempted to
upsert the same `coinbase_paper_price_state` singleton and emit an info log on
**every** quote attempt, including repeated `PROVIDER_BUDGET_BLOCKED` and
`NO_FRESH_INDEPENDENT_REFERENCE` rejections. On the crypto-worker's frequent
scan schedule, identical failures create needless PostgreSQL transaction/WAL
churn and noisy logs without providing a new observation.

This draft changes **publication only**:

- Immediately publish the first state and every changed symbol/status/failure
  reason; the reader never receives a fabricated valid quote.
- Continue publishing **every** successful `VALID_PAPER_PRICE` result, even if
  its source identity appears unchanged. Trading quote timestamps, depth,
  validation, receipts and authenticity checks are not modified.
- Throttle *identical all-unavailable* state publications to once every
  60 seconds per worker process (using a monotonic clock); the in-memory
  failure state is updated on every attempt.
- Skip both the duplicate database upsert and the duplicate info log during
  that interval. No provider HTTP or DB-budget reservation attempts are
  skipped by this optimization. The result is **not** a request-rate fix.
- After a restart, the first observation is immediately published.
- Start the successful 60-second throttle only after the database transaction
  commits. An unchanged unavailable state whose write or commit failed retries
  at most once every five seconds, measured after the attempt finishes.
  Changed states and valid prices still bypass these diagnostic throttles.
- Logs distinguish `COMMITTED`, `LOG_ONLY` (persistence disabled), and
  `UNAVAILABLE` publication status without exposing database error details.

This avoids claiming that unchanged publication means an available price.
`read_pricing_status()` still checks each stored original quote timestamp
against wall-clock time and does not promote a stale price to current.
There is no schema migration, additional storage table, broker action,
payment, historical evidence rewrite, fresh-price widening or risk change.

Before deployment, confirm exact-head CI and production storage/WAL safety
with independent release review. This change does not remove the need for
a validated streaming source, authorized read-only budget ledger accounting
or evidence-preserving storage migrations. Existing held PRs remain held.
