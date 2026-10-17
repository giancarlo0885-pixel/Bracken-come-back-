# Kraken public book reference (shadow only)

The crypto worker can ingest **BTC/USD** and **ETH/USD**, depth **10**, from
`wss://ws.kraken.com/v2`. No private credentials, REST requests, trading endpoints,
new dependencies, or changes to existing provider budgets are required. This is
an independent venue reference; it does not replace Robinhood execution quotes,
repair crossed Robinhood books, authorize paper fills, or qualify a strategy.

## Configuration and ownership

Opt in by setting `KRAKEN_REFERENCE_WS_ENABLED=true` **on the crypto worker**.
It defaults to false. Startup also requires `DATABASE_URL`, `EXECUTION_MODE=paper`,
`ENABLE_BROKER_SUBMISSION=false`, and `LIVE_TRADING_ARMED=false`. Do not change
any trading flags to enable this integration. No configuration was changed as
part of implementation. Activation/deployment is a separate operational action.

Startup is in `crypto_worker.py`'s main entry point, after database preparation;
importing the module or loading the dashboard cannot open a Kraken connection.
A dedicated autocommit PostgreSQL session holds advisory lock `73420261603`.
Only its owner opens the socket. Standby replicas retry election every 10 seconds.
Socket errors reconnect while retaining ownership; storage/session failure closes
the socket and requires reacquiring the lock. Readers never create feed connections.
No application worker, database connection, or execution path waits for Kraken.

The new migration creates `kraken_reference_state`, an overwritten singleton with
`CHECK(id=1)`. The owner also idempotently ensures this isolated table exists.
Publication is at most once per five seconds during streaming plus lifecycle
transitions. Only two top-of-book references, counters, last-valid timestamps and
two latest comparisons are stored. No raw message/tick history is persisted.
Logical row count and payload shape are bounded; ordinary PostgreSQL MVCC cleanup
still applies. Existing retention policies and canonical evidence tables are
unchanged. Counters cover the current process lifetime, not lifetime accounting.

## Integrity and freshness

- JSON decimal values are decoded as `Decimal`. Binary floats are forbidden in
  the checksum input. Trailing zeros survive decoding and serialization.
- Apply updates in delivery order, replace quantities, remove zero quantities,
  truncate each side to depth 10, and validate unsigned CRC32 after every message.
  Hash asks ascending then bids descending, removing decimal points and leading
  zeros, exactly as Kraken specifies. Multiple updates to a level are sequential.
- Kraken does not publish a book sequence number. Equal timestamps are allowed;
  ordered transport and checksum validation establish state consistency. Regressing
  timestamps, missing timestamps, malformed messages, invalid levels, bad checksum,
  stale/future timestamps and crossed books invalidate state and require reconnect
  plus fresh subscriptions/snapshots. No update can bootstrap an empty book.
- Exchange and local receipt timestamps must both be fresh (10 seconds maximum;
  exchange time permits at most two seconds of forward clock skew). Heartbeats
  and pongs never refresh a book. Local receipt time cannot replace exchange time.
- Application ping every five seconds, 15-second wire-liveness/snapshot deadlines,
  one-second receive timeouts. Stale books force resnapshot. Reconnect delay uses
  bounded exponential backoff with jitter, minimum five and maximum 60 seconds.
  A stable session can reset backoff. This stays below the documented approximate
  150 connection attempts per rolling ten minutes **for this process**; unrelated
  clients sharing an egress IP also count toward Kraken's limit.

## Provenance, comparisons and diagnostics

Quotes retain Kraken source/venue, internal and exchange symbols, exact bid/ask
and quantities, midpoint, measured spread, timestamps, age and checksum status.
`reference_only=true`, all execution/provider/paper verification aliases false,
and `execution_quote_eligible=false`. Common provenance helpers explicitly reject
reference-only payloads and the Kraken reference provider identity, even if a
legacy verification alias is accidentally set true. No Kraken quote is routed
through `MarketSnapshot`, stock routing, broker reference anchoring or fill writers.

The observer calls the existing execution-payload function unchanged, records only
the returned primary book fields, and returns the **same object**, including `None`.
It cannot rescue a rejected quote. Comparisons require the same internal symbol,
fresh timestamps on both venues, no stale/crossed primary book, and timestamps no
more than two seconds apart. Signed midpoint difference in basis points is a
diagnostic venue basis, with **no equality assumption, threshold, or trading gate**.
If worker sampling does not provide a fresh aligned primary book, comparison is
unavailable; Kraken's health is evaluated independently.

`KRAKEN REFERENCE HEALTH` structured logs (30-second cadence) and the dashboard's
existing DATA STATUS details show connection state, last valid updates, reconnects,
checksum failures, stale/crossed rejection counts, current references and comparisons.
The reader rechecks publisher age and both quote timestamps. Comparisons are removed
when either original venue timestamp expires, even if the
comparison was published recently. Comparison identity must match its current
Kraken reference; malformed comparisons are omitted without hiding valid quotes.
Expired current quotes are removed and labeled stale/unavailable; last-valid timestamps remain
historical diagnostics. Five-second publication means displayed references can
expire between publications. No cached value is represented as live after expiry.

## Validation

Unit tests cover the official independent CRC32 example (`3310070434`), wire decimal
precision, snapshots, sequential/repeated updates, deletion/truncation, stale/crossed
books, timestamp regression/equality, invalid subscriptions, liveness, reconnect and
resnapshot, bounded backoff, reader expiry, aligned venue comparisons and execution
authority boundaries. Separate local PostgreSQL tests exercise session-lock
exclusion/release, singleton upserts, read-only visibility and the bounded-row constraint.

Run `python -m pytest test_kraken_reference_book.py test_kraken_reference_runtime.py`.
For PostgreSQL tests, use an explicitly local disposable database with a name ending
in `_test` or starting with `test_`. Fixtures create/drop only isolated UUID schemas.

Actual public feed check (no database or credentials):

```sh
python scripts/kraken_reference_smoke.py --seconds 20
```

This checks real subscriptions, snapshots, subsequent updates, checksums and current
freshness. It is a bounded connectivity observation, not a production deployment or
uptime guarantee. Mocked disconnect/error tests are distinct from this check.

Official documentation reviewed October 16, 2026:

- https://docs.kraken.com/exchange/api-reference/spot-websocket-v2/book
- https://docs.kraken.com/exchange/guides/websockets/book-checksum-v2
- https://docs.kraken.com/exchange/guides/websockets/introduction

Existing rejected models, strategy thresholds, trading flags, accounting, Brain,
AEVE and permanent evidence remain unchanged. No profitability claim follows from
improved reference-data availability.
