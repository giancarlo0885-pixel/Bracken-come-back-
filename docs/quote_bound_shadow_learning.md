# Quote-bound shadow learning

This correction separates descriptive historical cohort reports from evidence
that was actually available before a future candidate entry. It does not
change trading formulas, thresholds, broker authorization or model vetoes.

## Market observations

A position's current/average/entry price has no independent source event time.
Reinserting it at the sampler's wall clock cannot establish a fresh observation.
The old sampler did that, including falling back to average or entry price.

The Coinbase paper adapter now notifies a bounded, measurement-only collector
when it already holds an authenticated quote. The collector reuses the existing
identity/freshness validation, rejects future or stale observations and keeps
original exchange time, local receipt time, sequence, provider, quote ID and
source identity. Its cache holds at most 64 latest symbol observations. It
never calls an API, writes a fill or grants execution authority. Observer
failures do not authorize trades or suppress the original quote guards.

The regular sampler writes these observations to the existing price-sample
table at ORIGINAL exchange event time, not as a new live mark. Same-event
observations cannot be repeatedly refreshed in memory. A stable venue/symbol/
sequence/event-time fingerprint also deduplicates later collector receipts. A transaction advisory
try-lock and an indexed existence check prevent duplicate persisted samples
across sampler workers. Original rows are not deleted or rewritten. Only
source-prefixed original observations enter newly calculated excursion paths.
The JSON source receipt is measurement-only and cannot substitute for the full
pricing receipt required for paper fills.

Only the existing Coinbase paper adapter supplies this receipt contract. Cash
marks remain unavailable for new source-bound excursions until an equivalent
quote receipt collector exists. This does not disable cash/crypto execution,
position monitoring, risk exits, forecasts or Brain ingestion. Legacy MFE/MAE
values remain historical research; this release cannot certify them. Observed
sample extrema do not establish a continuous price path or executable exit P/L.

## Temporal learning evidence

A new metric receives the entry-ready version marker only if exact accounting
and original quote observations both exist when that metric is first created.
Later accounting repairs use the base version and never retroactively award
that original-completeness marker. The marker is not full trade certification
or evidence of decision-time Brain consumption.

Entry challenger v4 starts a separate durable prospective epoch. Its prior
statistics and loss streak require those originally complete metric records,
with metric `created_at` strictly before the candidate's entry and prior exit
also before entry. This prevents later-created/initially incomplete metrics
from being silently reconstructed into an earlier prior. Existing versions,
results and epochs remain. Missing expectancy, excursions, costs, score or undefined
profit factor remain NULL and abstain. Minimum samples, score formula, profit
factor and edge gates are unchanged. V4 remains shadow-only.

## Reporting

Regime summaries retain their descriptive aggregate but expose exact-accounting
and incomplete counts separately, with exact-accounting P/L and fees. Exact
allocation accounting is not equivalent to a fully verified round trip: rows
can represent partial closes, and original forecasts, Brain consumption,
independent episodes and out-of-sample proof remain separate requirements.
Unavailable/nonfinite metrics are NA, not invented zero. The summary explicitly
states retrospective closed-ledger-row scope, unavailable independent episodes,
uncertified excursions and no strategy qualification.

## Validation and remaining limits

Regression fixtures cover legacy mark rejection, authentic quote capture,
stale/future/reference rejection, original time and price preservation, bounded
memory, duplicate/concurrent sampling, legacy-path exclusion, mixed accounting,
nonfinite/missing metrics, later-created/incomplete prior rejection, valid
prior acceptance, generation preservation and idempotence. PostgreSQL fixtures
are disposable local schemas; public HTTP is mocked. No profitable production
experiments are backfilled by tests or this implementation.

No schema migration, provider quota change, new network feed, trading-setting
change, volume resize or destructive database action is included. The $20
compute and $0 Agent caps remain. Coinbase's exhausted allowance and the need
for separately validated streaming paper pricing are not resolved by these
learning-integrity fixes. PRs #392/#393 remain held. Positive independent
post-cost expectancy still requires genuine forward observations.
