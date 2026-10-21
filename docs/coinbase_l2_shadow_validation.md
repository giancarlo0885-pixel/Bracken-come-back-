# Coinbase Exchange Level2 shadow-book validation — research only

## Motivation

The existing Coinbase Exchange REST paper-pricing path and Kraken-primary
research's Coinbase diagnostics share the Coinbase Public Research /
__provider__ PostgreSQL request ledger: configured maximum 2,400 per UTC
day, not a measured account/provider entitlement. A single uncached HTTP
request every 10 seconds demands 8,640 per day. Multiple symbols,
product metadata and research comparisons increase the rate. REST polling
at that rate cannot provide a continuous fresh paper execution source
under the current configured ledger. Quota denials MUST NOT be bypassed
by accepting stale packets or relaxing venue checks.

Coinbase Exchange documents level2 and unauthenticated level2_batch
WebSocket channels, which send an aggregated entire-book snapshot and
subsequent l2update messages. An update time is an exchange engine
event time, not the local network receipt. Updates set the complete
quantity at each price; zero deletes a price level. Crucially, the
documented snapshot has no provider event timestamp.

https://docs.cdp.coinbase.com/exchange/websocket-feed/channels

## Scope of this draft

coinbase_l2_shadow.py is a deterministic, non-networked reducer that
accepts only BTC-USD / ETH-USD. It does not start any socket, import a
broker, change worker startup, write to PostgreSQL, hook paper fills,
update the existing REST adapter, or receive any live traffic. It is
currently NOT an alternative source of executable paper quotes.

- Each symbol requires its own entire-book snapshot. Snapshots are
  explicitly not quote-eligible: they have no exchange event timestamp.
- A valid update supplies the original exchange timestamp and caller
  receipt. Both must be fresh and their clocks aligned. No heartbeat,
  re-read or cached receipt can refresh a stale venue timestamp.
- Maintain Decimal prices and quantities with bounded inputs and
  memory. Apply batch updates atomically; reject malformed, nonpositive,
  empty, locked/crossed, stale, future, regressing or pre-snapshot input.
  On any integrity error discard the affected book and require a new
  snapshot. A socket client MUST call disconnected() on stream
  breaks before consuming any subsequent messages.
- Emit only a top-of-book shadow reference. Every returned packet
  carries reference_only=true, execution_quote_eligible=false,
  provider_quote_verified=false, paper_reference_verified=false,
  execution_authority=NONE, broker_submission=NONE.
- This source has no Kraken checksum or standalone sequence number in
  the documented Level2 snapshot/update schema. Do not invent either
  verification field. WebSocket delivery assurances do not establish
  a validated actual feed or paper fill price.

## Required before any future promotion

1. Implement an independently reviewed singleton owner/reconnect client,
   bounded wire frames and message processing with safe backoff,
   subscription rejection handling, and authenticated/unauthenticated
   constraints where applicable. No socket runs in this draft.
2. Verify live Exchange event/receipt age distributions, book
   consistency, continuity after reconnect, subscription coverage
   and operational uptime using captured independent episodes.
3. Confirm exact online/trading-enabled Coinbase product metadata,
   venue comparison with fresh Kraken checksum books, bid/ask spread,
   visible sell/buy depth, clock drift, and actual fee/slippage assumptions.
4. Design a separately approved, immutable source receipt bound to the
   correct decision and atomic paper fill/lot/fees/Brain provenance.
   Do not reuse a shadow reference or stale data as execution authority.
5. Keep separate provider budgets and no broker connectivity, and
   retain all negative-model, governance and risk vetoes.

PAXG lacks current independent pricing and remains excluded. Legacy
missing signal/forecast IDs are not backfilled. PR #392 and #393 remain
on independent release hold. CI fixtures exercise protocol semantics,
not real exchange availability, network integrity or profitability.
