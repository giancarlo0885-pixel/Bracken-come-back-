# Forward paper decision-consumption evidence

This change depends on the original pricing-receipt audit in PR #392. Keep both
PRs on hold until their exact heads, migration safety, production compatibility
and trading safeguards have been reviewed. No production migration is authorized
by this document. No historical backfill or certification is supported.

## Contract and actual consumption point

The stock and crypto worker entrypoints install this recorder after readiness
initializes the schema. It wraps the three existing references to
`oracle_intelligence.evaluate_opportunity`. It captures the Brain summaries on
the signal, the actual trade-memory inputs and the existing context-only crypto
history. These are the projections the decision function reads; a receipt does
not claim that every stored Brain source or episode influenced a decision.

The original signal and exact forecast must already exist and resolve uniquely
within the same market/symbol. A content-addressed snapshot copies those source
rows and the Brain projection in a separate committed transaction. Original
source row foreign keys and retention exclusions preserve the parents. Identical
snapshots are reused without updating their contents or timestamps.

The decision transaction reads that committed snapshot. The evaluator receives
those frozen values. Its complete call arguments, portable feature vector,
explicit runtime policy switches and snapshot hash are bound to the input hash.
Feature and model version identifiers include source-file hashes. The original
decision output and consumption receipt commit atomically. Only after commit
does the signal receive its decision/receipt ID and database decision timestamp.
Failure skips that candidate; it does not synthesize a neutral decision or erase
an abstention. A committed snapshot from a failed evaluation may remain as
original evidence, but has no decision receipt or outcome.

PostgreSQL enforces paired decision/receipt foreign keys, SHA256 checks over
canonical UTF-8 text, source identity and chronology, distinct source/decision
transaction IDs, server timestamps, and rejection of UPDATE/DELETE/TRUNCATE on
the four new evidence tables. No extension is installed. Neither application
nor database rules can establish truth against a privileged administrator who
disables constraints/triggers or changes running code.

The entry fill links to the exact receipt, lot and ledger row inside the existing
canonical fill transaction. The linker rejects missing receipts, changed input
features, identity mismatches and decisions later than the entry. Ranking keeps
the receipt ID rather than replacing it with a signal-derived label.

## Qualification and limits

`audit_forward_trade` runs only in a read-only transaction. It verifies original
receipt hashes and relationships, unchanged signal/forecast rows, bound entry
features, original entry and exit pricing receipts, finalized inventory and the
complete canonical fill/cost graph. Missing or partial evidence stays
unavailable. Up to 500 exit rows per lot can be checked; larger histories remain
unqualified rather than silently truncated. Full qualification counts unique
original entry lots, not split exit fills.

New Coinbase receipts additionally retain the original adverse-fill cap,
executable reference and separate-fee convention. The full-chain auditor checks
the recorded depth/cost formula using those original inputs, never current
environment assumptions. Older receipts lacking that policy remain unqualified.
These are recorded modeled paper costs, not account-verified broker fees.

A valid receipt alone is not a full-trade qualification or strategy promotion.
The audit never infers positive expectancy from a traceable decision. Existing
risk/model vetoes, negative historical evidence, execution quote guards, strategy
thresholds and all live-trading settings remain authoritative and unchanged.

Deterministic protective exits do not read Brain through this evaluator and are
not presented as Brain consumers. Other uninstrumented library/manual decision
paths cannot obtain full-chain qualification from storage or retrospective
replays. No risk exit is disabled to manufacture a receipt. The contract does
not retroactively certify historical decisions.

## Migration and release review

The migration creates four protected tables, indexes on the new snapshot source
columns, append-only triggers and foreign keys. It scans/reindexes no historical
table and changes no existing column. Blocking bootstrap-lock acquisition and
both the legacy migration runner and installed `safe_run_migrations` wrapper set
a transaction-local five-second `lock_timeout` before
waiting. The SQL migration also sets it for direct transactional installation.
The runner already owns a single transaction: a lock error propagates and rolls
back schema changes and migration bookkeeping. The timeout bounds each lock
wait, not total migration duration; it does not modify session-wide settings or
automatically retry. Disposable-PostgreSQL tests hold the bootstrap/migration
advisory locks and a late FK parent lock, verify failure before repair or rollback
of partially created tables/functions, then verify a clean retry. Actual
production lock pressure still needs review. Parent references are permanent evidence;
old workers during a mixed rollout may fail a retention batch on an FK instead
of deleting a referenced parent. Roll out all three core services together after
approval. Do not delete new evidence to roll back application code.

A mixed-version regression executes the old forecast-retention eligibility
predicate without the new snapshot exclusion. Its DELETE fails the new FK,
rolls back without losing the original forecast, and a separate new worker can
still commit a verified decision/receipt. This proves evidence preservation and
recovery, **not** uninterrupted old retention. Avoid a prolonged mixed rollout;
if uninterrupted old-worker retention is required, release a retention-only
compatibility change before enabling new snapshot writers. That rollout remains
an approval/release gate, not something these tests silently authorize.

Canonical snapshot/input payloads are capped at 1 MiB and outputs at 256 KiB.
Evidence is append-only and is never auto-pruned. Snapshot reuse reduces duplicate
storage; total permanent evidence necessarily grows with distinct decisions.
Capacity must be monitored under the existing non-destructive storage policy.

`paper_brain_consumption.storage_diagnostics(conn)` is an opt-in read-only
measurement. It reports total relation bytes (including indexes/TOAST) for the
four new tables and at most 1,000 unordered payload samples per table. It never
requests an exact full-table count and marks absent tables unavailable. Samples
are not necessarily representative; zero canonical bytes on receipt/link rows
means those rows have no canonical JSON columns, not zero physical storage.

Local PostgreSQL synthetic measurements (100 HOLD decisions, tiny fixture
payloads, no fills) observed 212,992 incremental relation bytes with one reused
snapshot (2,129.92 bytes/decision), versus 335,872 with 100 distinct snapshots
(3,358.72 bytes/decision). At a purely hypothetical 10,000 decisions/day, linear
extrapolation is about 20.31–32.03 MiB/day. These are reproducible fixture results,
**not production sizing or an upper bound**: actual Brain/input payloads can be
much larger. WAL, replicas/backups, retained source rows and real fill links add
storage. The payload caps alone allow up to 2.25 MiB of canonical text for a new
snapshot plus decision input/output, before overhead. Obtain production payload
and decision-rate observations and available-capacity figures before approval.

## Production capacity review — 2026-10-20 04:13 UTC

Read-only Railway observations, not a release approval:

- Production volume `postgres-volume`: provisioned `sizeMB=81920`, unchanged.
- Postgres `DISK_USAGE_GB`: current 53.31263488 in the one-hour query
  (61 samples; minimum 53.31132416, maximum 53.31263488).
- Seven-day query: 169 hourly samples, minimum 52.01899776, maximum/current
  53.312524288. The 1.293526528 GB range is not a measured daily growth rate:
  the summarized API does not provide timestamped first/last values or isolate
  permanent tables, WAL, and reusable space.
- Keep the API's reported MB/GB labels. Exact filesystem free bytes, unit
  normalization and operational reserve are not verified by these two APIs;
  do not claim an exact free-space/runway figure by subtracting mixed units.
- Core web/stock/crypto and Postgres each report one running replica; no pending
  deployment or staged patch. Main remains the pre-receipt production version.
- Existing maintenance logs show successful bounded passes, but omit the storage
  report returned internally. No production SQL execution or row-level payload
  measurement was obtained. The new four-table diagnostics cannot establish
  actual production evidence growth before those writers have run.

Capacity remains HOLD. Before release, obtain read-only, time-bounded measurements
of filesystem total/free bytes and current database/relation sizes, representative
original evaluator payloads and decision frequency. Count attempted evaluations,
including HOLD/rejected decisions and snapshots left by failures, not just fills.
Model snapshot reuse versus distinct snapshots, protected source-row growth,
indexes, TOAST, existing growth, WAL and backups. Keep unknown inputs unavailable.
The approval record must specify a reserve and planning horizon and evaluate
`runway_days = (verified_free_bytes - approved_reserve_bytes) /
(existing_daily_growth + projected_receipt_daily_growth)` only when inputs are
measured consistently and the denominator is positive. Fixture byte counts and
the seven-day min/max range cannot fill those missing inputs. Do not resize,
prune canonical evidence, or enable trading to produce a measurement.

## Proposed rollout and rollback — not executed

All steps below remain subject to the user's merge/deployment hold. Updating a
draft or writing this procedure does not authorize a production operation.

1. Reverify both exact heads and independent release approval, all paper-only
   variables, volume capacity review, backup/restore readiness and currently open
   positions. Record exact running SHAs and deployment IDs for every database
   client. Do not assume that merging dependency #392 is harmless: main pushes
   can auto-deploy independently to web and workers.
2. Inventory all database clients, not only the three core services. Railway
   currently shows `crypto-flow-lab-fast` running `v45_shadow_sampler.py` with a
   DATABASE_URL variable. Current source writes its own shadow predictions, not
   the Brain tables or forecast retention; verify its deployed revision and
   actual database reference before declaring it compatible. The two model labs,
   cross-asset research and forecast diagnostic have zero running replicas in
   this observation. Verify they cannot restart old retention code unattended;
   an online service label alone does not prove a running client or safe code.
3. Choose and approve one compatibility route before any new snapshot is written:
   a retention-only compatibility release to every relevant old reader/writer
   (without enabling receipt writers), or a maintenance window that quiesces all
   affected clients before schema installation. The former requires a separately
   reviewed/tested release and known rollback baseline; it has not been created
   or deployed here. The latter must explicitly cover protective-exit continuity
   for open paper positions; stopping workers is not authorized by this document.
   Railway does not make a multi-service rolling deployment atomic.
4. After capacity and route approval, deploy the approved dependency and receipt
   changes in that controlled order. Serialize migration installation; retain
   five-second per-lock waits and transactional rollback. On timeout, stop release
   progression, confirm no migration ledger/table partial state, investigate the
   blocker, and retry only after it clears. Do not kill sessions or raise limits
   to force installation. Paper readiness can log migration failure and continue,
   so startup/SUCCESS alone is not evidence of schema readiness or receipt coverage.
5. Read back each service's exact deployed SHA and terminal SUCCESS. Verify the
   schema/migration ledger, original committed snapshots, paired decision receipts,
   retention behavior, pricing/quote safeguards and unchanged governance vetoes.
   Use read-only audits and post-approval storage observations at known times to
   calculate actual byte and decision-count deltas. Failures, missing receipts,
   uncontrolled growth, old retention errors or stalled protective exits halt
   promotion. Do not fabricate trades to exercise receipt generation.
6. Rollback is application-only to the approved retention-compatible baseline:
   prevent additional affected decision work through the approved operational
   procedure, preserve protective exits, and roll all affected clients back in
   the agreed order. Leave additive tables, FKs, snapshots, receipts, source rows,
   fills and migration history intact. Never delete evidence or mark old decisions
   verified. A rollback straight to unmodified `61560a59` can restore old retention
   failures and is not a reviewed steady-state rollback once snapshots exist.
   If no compatible baseline is approved, remain stopped at the release gate;
   prefer a reviewed forward fix over destructive schema reversal.

Record which route was selected, its authorized operator, maintenance/rollback
triggers and observed results in the release record. This procedure defines the
remaining review; it does not claim mixed-version rollout approval, production
capacity approval, or profitable trading evidence.

No configuration enables live trading or broker submission. Required settings
remain `EXECUTION_MODE=paper`, `ENABLE_BROKER_SUBMISSION=false` and
`LIVE_TRADING_ARMED=false`. Missing migration/data fails closed. After an approved
deployment, verify committed receipt logs and read-only full-chain audits for new
decisions; startup success alone does not establish receipt coverage.
