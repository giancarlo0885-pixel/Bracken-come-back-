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
table and changes no existing column. Normal FK/migration locks still need a
production compatibility review. Parent references are now permanent evidence;
old workers during a mixed rollout may fail a retention batch on an FK instead
of deleting a referenced parent. Roll out all three core services together after
approval. Do not delete new evidence to roll back application code.

Canonical snapshot/input payloads are capped at 1 MiB and outputs at 256 KiB.
Evidence is append-only and is never auto-pruned. Snapshot reuse reduces duplicate
storage; total permanent evidence necessarily grows with distinct decisions.
Capacity must be monitored under the existing non-destructive storage policy.

No configuration enables live trading or broker submission. Required settings
remain `EXECUTION_MODE=paper`, `ENABLE_BROKER_SUBMISSION=false` and
`LIVE_TRADING_ARMED=false`. Missing migration/data fails closed. After an approved
deployment, verify committed receipt logs and read-only full-chain audits for new
decisions; startup success alone does not establish receipt coverage.
