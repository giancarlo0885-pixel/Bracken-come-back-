# Paper trade evidence audit

`PAPER TRADE EVIDENCE AUDIT` runs once after database readiness on each paper
worker. It uses a separate REPEATABLE READ READ ONLY transaction and a five-second
statement timeout. There is no background query loop, repair, backfill, DDL,
profitability computation or execution authority.

The report separates all-market canonical trade counts, ledger mode/side counts,
fill counts and legacy missing links from a bounded sample of the latest 50
explicitly linked ledger rows. It reuses the exact persisted fill/order/execution/
lot validator and reads every SELL edge for sampled lots to check cumulative
quantity allocation. It never reconstructs an opening lot by nearby timestamps,
prices, symbol similarity or latest-record lookup.

Financial graph verification and decision-reference verification are distinct.
Unique sampled entry lots are checked against their stored lot IDs, decision
timestamps and feature snapshots, exact persisted signal identity/market/symbol,
and exact forecast identity/signal/market/symbol. Source and generation times
must precede entry; ambiguous, missing or late references are reported explicitly.
Missing forecast IDs are a diagnostic limitation, not proof that a heuristic
trade did not occur. No existing historical row is changed or reclassified by
this audit, and sample counts are not all-history qualifications or certified
independent market episodes. Brain decision-time consumption is not inferred.

The atomic writer also uses the same relative quantity tolerance as the exact
reader. A fixed absolute tolerance previously allowed material fractional/tiny
lot mismatches to be written and later rejected by the evidence reader. The fix
tightens identity reconciliation; it does not change trading thresholds or
convert any historical record into verified evidence.

When production data is accessible only through runtime logs, this audit provides
committed-database evidence through the worker's existing connection without
exposing credentials or exporting private order payloads. Query failure returns
UNAVAILABLE and logs only the exception class. It never claims zero anomalies
when a read failed. Live mode or armed broker/live flags disable the audit.

## Preserve entry-linked parent records

Signals and forecasts are high-volume analytical tables, but records referenced
by permanent position lots or trade-ledger entries are reproducibility evidence.
Rolling cleanup must exclude those references. Retained forecasts also preserve
their exact parent signal IDs. The existing 6000-signal and 3000-forecast budgets
apply to unlinked records; trade-linked parent records are retained separately.
A later pass may reclaim an unlinked signal after its unlinked forecast expires.

BUY attribution and forecast persistence hold a shared transaction advisory
lock while storing their parent references. Signal/forecast maintenance uses a nonblocking exclusive lock;
if a writer is active it skips that pass. This serializes reference capture and
cleanup without waiting on an entry writer or changing trade qualification.
Existing batch limits and maintenance cadence are unchanged; an active writer
can defer cleanup. No production cleanup, deletion, vacuum, reindex or historical
backfill is invoked by this repair workflow. Regression deletions use only guarded
disposable local PostgreSQL schemas.

The production baseline found 3132 legacy fills without canonical trade/lot links,
and 17 unresolved signal IDs plus 26 absent forecast IDs across 26 sampled entry
lots. These limitations remain visible. The unguarded old cleanup is a confirmed
mechanism for losing referenced records, but it does not prove the exact cause
of every missing historical ID. Absent forecasts may reflect model abstention;
none is synthesized. Market/strategy promotion still requires new, independently
validated post-cost outcomes.
