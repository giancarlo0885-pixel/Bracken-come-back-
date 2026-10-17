# Read-only persisted lineage inventory

`paper_persisted_lineage_audit.audit_persisted_lineage(market="crypto")` reads
aggregate counts for an explicitly selected `crypto` or `cash` market. It uses
a fresh repeatable-read, READ ONLY transaction and a five-second statement
timeout. It is an opt-in helper, without dashboard/worker scheduling or startup
queries. Missing schema or database failures propagate; they never become zero
counts or positive evidence. No schema or production configuration changes are
required on current main.

Counts are an inventory, **not verification of exact fills or profitability**:

- `linked` means a recorded non-null canonical trade ID, not a verified lot graph.
- `invalid_fee` detects null, negative, NaN and infinite fees. A finite/default
  zero fee is not independently verified merely because it passes this check.
- `exact_brain_episodes` counts persisted exact/provenance labels; it does not
  independently reconcile accounting. Scope fields make these limits explicit.
- Reconstructed decision replays never establish decision-time Brain consumption.
  `verified_decision_consumption` remains `UNAVAILABLE`.

Use the existing `trade_evidence_verification` and `paper_fill_provenance` readers
for per-trade committed-row and exact fill/lot/accounting verification. This
aggregate helper supplements those readers and does not replace them. It never
backfills, writes historical evidence, promotes a strategy or authorizes fills.

PostgreSQL regressions use the actual repository paper accounting, canonical-link,
Brain foundation/learning and replay migrations in guarded local disposable UUID
schemas. They verify market separation, nonfinite fees, committed visibility,
repeatable-read consistency, rejected writes and unavailable consumption evidence.
These are local/CI schema-compatibility tests, not a direct production DB audit.
