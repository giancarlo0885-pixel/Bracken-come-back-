# Committed trade evidence

After each market-scoped Brain sync commits, the worker opens a separate
PostgreSQL connection with a repeatable-read, read-only transaction and a five
second statement timeout. It emits `ORACLE COMMITTED TRADE EVIDENCE` receipts
through ordinary runtime logs. The diagnostic cannot submit orders, mutate
evidence, run migrations, or change a successful learning-sync result.

By default, the bounded readback covers the latest two paper SELL ledger IDs in
that market and, for crypto, the latest ten distinct AEVE outcome IDs. Optional
`ORACLE_VERIFY_TRADE_IDS` specifies at most twelve comma-separated exact IDs.
IDs are SQL parameters. This is an internal diagnostic, not a public SQL API.

`ledger_status=COMMITTED` means the exact canonical ID is visible on the new
connection. `aeve_committed_count` counts durable evaluator rows, not log lines.
`brain_status=MATERIALIZED_EXACT` additionally requires matching canonical
identity, entry features, entry signal, UTC chronology, complete round-trip
costs, and exact accounting markers. Missing optional tables and query errors
report unavailable evidence. The check never creates missing rows.

A committed Brain episode proves durable ingestion. It does not prove a later
decision used that episode. `ranking_decision_consumption` and
`canonical_fill_linkage` remain `UNAVAILABLE` where no durable exact-ID receipt
exists. These receipts do not establish strategy profitability or independent
market episodes.

When explicit persisted fill/lot links exist, the same read-only snapshot now
reconciles the original BUY and each SELL to their actual execution, order,
fill, ledger row and lot. It checks quantities, proportional fees, gross/net
arithmetic, cumulative closes, entry identity and the observation cutoff.
`canonical_fill_linkage=EXACT_LINKED` exposes those IDs and recorded round-trip
costs; `brain_fill_accounting_status` separately reports whether durable Brain
accounting matches. Historical proximity never creates a missing link, and
stored historical metrics are not reclassified merely by this diagnostic.

`PENDING` means the Brain table exists but has no row for that exact closed
trade ID; it does not assert that the trade qualifies for ingestion.

Cash readbacks also emit `ORACLE COMMITTED TRADE SOURCE DIAGNOSTICS`, including
when the bounded ID readback emits `NO_RECENT_TRADES`. The aggregate diagnostic
uses the same independent repeatable-read, read-only snapshot and five second
statement timeout. Its `totals.ledger_rows` counts the entire visible ledger;
`source_buy_rows` and `source_sell_rows` count only the research source's exact
`market='cash'`, `broker_mode='PAPER'`, `account_environment='PAPER'`, and uppercase
BUY/SELL filters. Zero selected IDs or zero qualified research closes therefore
does not establish that all historical stock trades are absent.

The inventory groups ledger counts by market, side, broker mode, and account
environment. It preserves allowlisted raw labels such as `stock`, `CASH`, and
`paper` so excluded variants remain visible. Unknown values become `OTHER`;
NULL and empty values become `MISSING` and `EMPTY`. It never emits arbitrary
account tags, symbols, payloads, or credentials. At most 64 groups are returned,
with the full group count and `inventory_truncated`; aggregate totals remain
complete. The mutually exclusive exclusion counts distinguish noncanonical
markets, non-PAPER broker tags, non-PAPER account tags, and unsupported sides.
No diagnostic normalizes or relabels stored history.

`recorded_link_coverage` counts source rows with and without persisted exact-ID
link records, including unlinked historical SELL rows. When all required tables
exist, `recorded_graph_coverage` separately counts recorded links missing a fill,
order, lot, execution, or recorded BUY edge. These are edge-presence counts,
not accounting validation; downstream missing edges can overlap, and missing
relations report unavailable counts. `ALL_SOURCE_SELL_ROWS_LACK_RECORDED_LINKS`
explains missing recorded provenance without fabricating a historical mapping.
`qualification_status=NOT_EVALUATED_BY_INVENTORY` prevents aggregate coverage
from being read as qualification. Exact validation and the existing Brain/AEVE
receipts remain bounded to the selected trade IDs, and their separate bounded
counts do not claim coverage of all source history. No rows are backfilled.

For an internal runtime with its existing database connection configured:

```sh
python trade_evidence_verification.py --market crypto TRADE_ID_1 TRADE_ID_2
```

No database credentials need to be exported through Railway logs.
