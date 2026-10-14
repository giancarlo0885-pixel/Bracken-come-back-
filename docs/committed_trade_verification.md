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

`PENDING` means the Brain table exists but has no row for that exact closed
trade ID; it does not assert that the trade qualifies for ingestion.

For an internal runtime with its existing database connection configured:

```sh
python trade_evidence_verification.py --market crypto TRADE_ID_1 TRADE_ID_2
```

No database credentials need to be exported through Railway logs.
