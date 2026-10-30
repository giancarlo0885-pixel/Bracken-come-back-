# Bounded prospective costed Kraken research pilot

This opt-in research feature freezes an entry hypothesis before observing its
future outcome. It does not repair historical accounting, authorize orders,
approve a model, or change strategy/risk thresholds. Existing failed calibration
and walk-forward tests remain vetoes. #392/#393 remain held; these application
INSERT-only records are not their immutable decision-consumption contract.

## Contract

`KRAKEN_FORWARD_PAPER_PILOT_ENABLED=true` is required in addition to the existing
Kraken paper-pricing adapter and paper/live/broker safety gates. Default is false.
No deployment or production activation is implied by this document.

The already elected five-minute primary-research worker reuses its installed
Kraken pricing adapter, metadata cache and shared WebSocket feed. It opens no new
feed, thread or service. It captures both forecasts and model abstentions;
an unconditional $25 passive entry hypothesis is a control observation, never an
approved trading decision. Unavailable entry books remain unavailable.

Original closed-history identity, original forecast or abstention, model identity,
entry book, CRC/session/timestamps, product rules, costs and quantity are frozen
in the new original research record. Existing records are never backfilled.
Forecasts must exist before capture, match the symbol and retain shadow status.
Hashes detect content disagreement against a trusted copy; they do not make
mutable PostgreSQL rows tamper-proof or prove Brain consumption.

Two predeclared exit horizons (300/900 seconds) share the exact entry and quantity.
The first sampled valid actual book inside each following 60-second window prices
the exit. No interpolation, source timestamp renewal or historical reconstruction
is allowed. Temporary invalid books are retried if another research cycle falls
inside the window. Missed windows become UNAVAILABLE, with no P/L. Five-minute
sampling, slow cycles, feed gaps or budget-denied metadata can therefore leave
many outcomes unavailable. The pilot is not a continuous-price execution replay.

Depth walking captures displayed spread/liquidity. Adverse slippage and latency
assumptions are frozen at entry, at least the existing conservative defaults
(0.135% and 0.03% per side). A modeled 0.6% fee per side is recorded separately
and deducted once. These are assumptions, not verified account fees. BUY quantity
is $25 divided by the modeled entry price. SELL uses that same quantity. Net P/L
is modeled proceeds minus entry notional and both explicit fees. It is not
realized canonical trading profit. The simulated notional has no balance effect.

## Storage, concurrency and backpressure

One existing Brain-source key freezes version, policy hash, start time and the
next at most 1,000 unused source-ID slots, capped by the existing 10,000 boundary.
No reset, capacity expansion, new table, migration, retention change or automatic
second pilot exists. For the supplied 9,373 high-water mark, that would allow at
most 627 slots; it is not a fresh production row-level measurement.

Each actual captured source occupies one existing research slot. Its forward
trial is bounded to 16KiB; combined payloads above a conservative 24KiB skip the
trial and preserve the ordinary research result. Outcomes are separate protected
`oracle_brain_sources` INSERTs, unique by version/source ID/horizon. Concurrent
finalizers cannot duplicate a key; a transaction failure rolls all its writes
back. Pending outcomes can finish after source backpressure stops new capture.

Incremental JSON payload upper bound per used slot is approximately 68KiB:
16KiB original trial plus its 16KiB Brain source copy, plus two 18KiB outcomes.
That is approximately 41.64MiB for 627 slots or 66.41MiB for 1,000, plus one small
configuration. This excludes PostgreSQL row/index/TOAST overhead, WAL, backups and
existing research history bytes. It is not a disk-free measurement, billing
forecast or capacity approval. Storage and usable slot count must be reviewed
before activation. No production SQL or capacity change is required by the code.

## Consumption and interpretation

Outcomes use `source_type=crypto_forward_paper_simulation`, confidence zero,
`context_only=true`, `ranking_eligible=false`, `trade_outcome=false`, and execution
impact/authority NONE. They do not become Brain canonical episodes or qualify
models/entries. Existing diagnostics expose `prospective_paper` in primary state
and logs. Per-horizon totals stay separate; the two horizons are not counted as
two independent entries. Matched deltas require the same original trial hash.

UTC-day counts are dependence proxies only. Independent episodes, actual
profitability, portfolio drawdown, MFE/MAE and promotion remain unavailable.
These simulations are not the canonical matched shadow-exit experiments and
cannot remove the open PAXG position's missing-original-evidence exclusion.
No adaptive selection, profitable-subset filter or automatic strategy promotion
exists. This small pilot diagnoses timing/cost behavior; it cannot demonstrate
an independent profitable trading edge by itself.

## Validation and rollback

Unit tests use synthetic books/forecasts. PostgreSQL tests use disposable local
schemas, actual INSERTs, separate committed readers, concurrent finalizers,
rollback, conflicting parent evidence, expired windows and full-capacity worker
behavior. They are not public-feed or production-row verification.

Rollback/disable the feature flag to stop pilot processing; retained originals,
configuration and outcomes remain. Re-enabling resumes the same bounded version,
never starts a new window. Older workers ignore the additional JSON fields.
The existing feed, paper adapter and canonical trading paths remain unchanged.
