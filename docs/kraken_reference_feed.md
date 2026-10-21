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
Periodic publication is at most once per second during streaming, measured from
completion of the previous write, plus lifecycle transitions. Slow writes do not
trigger catch-up bursts. Only configured top-of-book references, counters,
last-valid timestamps and latest comparisons are stored: two default markets,
or at most four when the separate research expansion qualifies them. No raw
message/tick history is persisted. Original source timestamps are never renewed.
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

The observer wraps the existing provider client's `best_bid_ask_quotes` responses,
before forecast selection or execution handoff. It adds **no requests** and returns
the **same response object**, preserving original exceptions and execution paths.
Only allowlisted public book/timestamp fields for requested BTC/USD or ETH/USD
equivalents are retained. Duplicate, mismatched, missing or crossed responses
produce explicit diagnostic reasons, never replacement execution quotes.

The Robinhood payload timestamp is required for comparison and labeled
`provider_payload`; local receipt time is recorded separately, never substituted
for missing/malformed provider time. This does not change the existing execution
adapter's timestamp semantics. Both clocks must remain fresh; a new receipt cannot
refresh old provider time. Fixed mapping accepts BTC/USD ↔ BTC-USD and ETH/USD ↔
ETH-USD only, with no guessed asset aliases.

A ten-second in-memory history (maximum 128 samples per pair, at most ten samples
per second) pairs each primary timestamp with the closest fresh, checksum-validated
Kraken reference. This avoids losing a valid match when the latest Kraken update
is five seconds newer at publication. Matching never uses price equality. History
is cleared on disconnect/integrity failure and is not persisted as a tick ledger.
Each of the two latest comparisons includes its actual paired reference, which
can differ from the newest quote and is independently revalidated on read.
Only bounded fields are added to the overwritten singleton; original inputs expire.

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
comparison was published recently. Comparison identity must match its paired
Kraken reference; malformed comparisons are omitted without hiding valid quotes.
Expired current quotes are removed and labeled stale/unavailable; last-valid timestamps remain
historical diagnostics. A one-second publication schedule cannot prevent source
expiry or a slow publication path; displayed references can still expire between
publications. No cached value is represented as live after expiry. The additional
write volume and production latency require separate release review; see
[the delivery and source-time review](kraken_stale_book_source_diagnostics.md).

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

## Primary research analysis (separate opt-in)

`KRAKEN_PRIMARY_RESEARCH_ENABLED=true`, together with the existing
`KRAKEN_REFERENCE_WS_ENABLED=true`, enables a separate paper-only analysis path.
The new flag defaults to false. Live mode, broker submission or live arming
prevents startup. No execution/trading flag or model qualification changes.

The elected ingestion worker keeps the same single depth-10 WebSocket and may
expand to at most four exact USD instruments from the existing crypto watchlist.
Additional pairs require online currency instruments from public Kraken
AssetPairs metadata (`assetVersion=1`); no aliases, tokenized stocks, leveraged
products or inferred coverage. Discovery failure preserves BTC/ETH ingestion.
This metadata establishes research coverage, not broker tradability.

A separately elected research worker samples at most every five minutes. Kraken
is primary for current prices and same-venue closed five-minute candles. Public
OHLC bootstrap excludes the uncommitted final candle and validates ordered,
contiguous, finite OHLCV. The observed API can return 720 closed bars plus the
uncommitted bar; storage and model input stay capped at 720 completed bars.
Candles retain their historical timestamps/provider, independently of the book.
The existing 15-minute selective model runs unchanged: abstentions and failed
qualification remain abstentions/failures. Research target prices anchor the
model's unchanged predicted return to the actual fresh venue mid, with explicit
source/receipt timestamps; these forecasts are never routed to trading signals.

Coinbase public level-1 books provide independent timestamp-aligned diagnostics.
Auction, crossed, stale or missing-timestamp books are rejected. If the Kraken
book is missing, a fresh Coinbase book and same-venue closed Coinbase candles may
supply an explicitly labeled research backup. Coinbase never inherits a Kraken
checksum and missing Coinbase checksums remain `NOT_PROVIDED`. No synthetic
volume or local receipt time is substituted for an exchange timestamp. Robinhood
remains the execution authority; its invalid books still block fills.

Both public HTTP clients use existing shared provider budgets and cooldowns:
Kraken Public Research <=1200 requests/day, Coinbase Public Research <=2400/day,
with five-minute bounded caches and local plus shared 429/API-rate-limit
cooldowns. Database/ledger unavailability fails closed for new requests.

Brain and dashboard read `kraken_research_state`, a singleton containing latest
research/abstention diagnostics; readers label it a retained observation and
recheck freshness. Research is separate from qualified trade/episode statistics.
Actual shadow forecasts also append immutable, deduplicated
`kraken_research_evidence`: original quote, model output and compressed closed
input candles. This new protected evidence table cannot exceed 5000 slots;
payloads and compressed history are size bounded. Capacity exhaustion stops new
evidence acceptance with an explicit diagnostic, without deleting old evidence.
Repeated symbol/venue/bar/model generations are not counted twice. No trade,
fees, realized profitability or future outcome is synthesized. These records
support later reproducible evaluation; they do not establish a profitable edge.

`KRAKEN PRIMARY RESEARCH` logs report actual roles, source times, evidence IDs,
model abstentions and independent comparison availability. A successful forecast
is not approval to trade. Existing stocks, execution gates, sizing, canonical
trades/accounting, Brain memory and AEVE generations are unchanged.
