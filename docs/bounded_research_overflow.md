# Bounded additive research overflow

The first 5,000 immutable Kraken research records and original Brain source keys
must remain unchanged. This feature adds one independent protected table with
5,000 additional slots, IDs 5,001–10,000. Both tables have the same payload/history
bounds; no existing CHECK constraint, row, ID, source key or evidence blob is
altered, renumbered, recycled or removed. No canonical trade/fill/fee is changed.

## Staged activation

The new code defaults to measurement/read compatibility only. Creation requires
`KRAKEN_RESEARCH_OVERFLOW_CREATE_ENABLED=true` in the elected crypto research
worker, plus paper mode, broker submission false and live trading false. Do not
activate until production storage measurements and rollout checks are reviewed.
Once created, readers automatically include the table even if its creation flag
is later removed. That flag controls creation, not erasure or ongoing visibility.
The existing research enable flag can pause the producer without deleting data.

Before activation require current volume use below 75%, at least 10 GiB free,
and a successful metadata-only footprint report. On October 26 the platform
reported 54,835.953 MB used / 81,920 MB limit (~27,084 MB headroom); this is a
pre-activation snapshot, not a guarantee of future free space. The new report
exposes legacy/overflow total relation bytes, the whole Brain sources relation,
and database bytes. Database bytes exclude some volume consumers such as WAL;
whole-Brain size cannot be attributed solely to Kraken. Do not manufacture
bytes-per-record, WAL rates or free capacity from unmatched metric scopes.

The added 5,000 records have at most 819,200,000 bytes of logical payload+compressed
history under existing field caps; Brain copies can add up to approximately
163,840,000 logical evidence bytes, plus metadata, physical/index/WAL overhead.
These are field-envelope planning bounds, not measured physical storage. Review
actual footprint against volume headroom. No volume resizing is part of rollout.
At four new observations per five minutes, 5,000 extra slots are about 4.3 days
of capacity, not perpetual retention. Existing warnings and full-capacity
backpressure remain; further archival/capacity design requires a separate review.

## Rollout, locks and compatibility

Creation runs only after existing producer election, in one transaction with a
one-second lock timeout, three-second statement timeout and dedicated transaction
advisory lock. Contention rolls back without partial schema. Repeat creation is
idempotent. The original relation is untouched. Readers union only two fixed
internal table names; global ID ranges cannot overlap. The single elected writer
checks duplicate evidence keys across both tables before allocating the next
highest ID. No holes or committed IDs are reused.

Older producers remain blocked at the original 5,000 limit. New code can read
legacy-only schema before activation, then both stores after activation. Rolling
back code stops legacy production without deleting overflow records; roll forward
to recover their visibility. New Brain receipts verify original payloads, table
identity, execution authority and unique global source IDs across both stores.
Existing Brain source rows are not rewritten. Missing or conflicting receipts
remain unavailable/unmatched, never certified as decision-time consumption.

## Validation

Tests cover default-off and paper-only creation; bounded lock/statement behavior;
transaction rollback; repeated migration; retained original rows/Brain keys;
legacy/new writer handoff; both stores' duplicate detection and final capacity;
invalid IDs and payload limits; wrong-origin Brain receipts; retention protection;
and a real-SQL full cycle from the full legacy table to a new shadow record and
matched Brain copy. Fixture records do not represent profitable market evidence.

This restores bounded research intake after authorized activation. It does not
reset Coinbase's genuine daily allowance, supply a validated streaming paper-price
adapter, recover missing historical signal links, approve an unqualified model,
change strategy thresholds, enable real-money trading, or prove profitability.
