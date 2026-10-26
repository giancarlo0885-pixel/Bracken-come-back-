# Research evidence capacity backpressure

Production on October 26 reported highest evidence ID 5,000, zero remaining
slots, and 5,000 matched Brain source receipts. All four research symbols were
rejected with EVIDENCE_CAPACITY_REACHED, yet the cycle reported ACTIVE and still
requested histories, generated forecasts and attempted independent venue reads
before discovering persistence was impossible. Coinbase's current-day diagnostic
separately reported actual exhaustion of its 2,400 reservation allowance, not an
active cooldown. The exhausted ledger must not be manually refilled.

Read the existing bounded MAX(id) capacity report before provider/model work.
Full or unavailable capacity blocks only this durable research producer, emits
an explicit blocked status and skips new history/comparison/forecast requests.
The elected loop rechecks on its existing five-minute cadence. If a valid capacity
read returns after a transient read failure, research can resume without restart.
Track successful slot allocation within the cycle so consuming the final slot
also stops subsequent symbols. Keep syncing and checking existing Brain evidence.

This adds one bounded read per research cycle; the existing singleton state write
and periodic receipt reporting remain. No new table, migration, retention policy,
cap increase, deletion, ID recycling or historical backfill is introduced. Existing
5,000 permanent records and linked Brain sources remain intact. Live reference
feed ingestion and all paper/broker/model/quote gates are unchanged. The paper
pricing adapter remains separate and its daily budget is not reset by this change.

Six behavioral regressions fail against original source: full/unknown capacity,
read errors, recovery and last-slot exhaustion. Disposable PostgreSQL cases verify
actual SQL when capacity is full or its table is unavailable; original stored bytes
remain unchanged. Fixture data is not production trade or profit evidence.

This prevents wasted research requests and misleading status. It does not restore
ledger capacity, current-day Coinbase quota, missing entry evidence or a profitable
strategy. Permanent learning expansion still needs an approved storage/retention
and migration design. Matched research-source receipts are not immutable proof
of decision-time Brain consumption. No positive trading expectancy is asserted.

Local Python 3.11 validation: 2,775 passed, 148 skipped. Configured disposable
PostgreSQL suite: 198 passed. Compilation and diff checks passed. Production
profit/pricing recovery is not established by these tests.
