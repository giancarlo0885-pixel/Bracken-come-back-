# Optional Kraken closed-candle streaming

Kraken's public WebSocket v2 OHLC channel supports five-minute candles and updates
on trade events. This feature adds that channel to Oracle's existing elected book
socket, not a new service/connection or a replacement execution source.
Official schema: https://docs.kraken.com/exchange/api-reference/spot-websocket-v2/ohlc

## Activation and unchanged boundaries

Default off: `KRAKEN_OHLC_WS_ENABLED=true` is required together with explicitly
present `KRAKEN_REFERENCE_WS_ENABLED=true`, `EXECUTION_MODE=paper`,
`ENABLE_BROKER_SUBMISSION=false`, `LIVE_TRADING_ARMED=false`.
No production variable was set, no release authorized by this document, and no
trading/auto-entry flag or model/risk threshold changes. The current <=4-market
subscription ceiling, provider reservations, prices, original quote timestamps,
CRC guards, pricing-receipt authentication and product-rule freshness remain.

Product metadata still uses existing fresh AssetPairs REST checks. Instrument
streaming is deliberately outside this change: it needs its own continuous-state
provenance contract rather than renewing metadata timestamps without evidence.
No new independent venue is claimed. Other-provider fallbacks are preserved.

## Data contract

The existing public research client obtains real closed REST bootstrap history
using the existing shared quota/cooldown/cache path. A ready, acknowledged OHLC
snapshot may then seed the current socket generation. Historical symbols,
interval, chronology and candle values must validate. A history call racing a reconnect cannot seed the new generation. An existing
bounded REST-cache response can seed only when its original closed bars remain
valid and contiguous with the new snapshot; its receipt time is retained.
Bootstrap observations are never invented.

Each candle retains its exact exchange `interval_begin`; the deprecated timestamp
is not used. OHLC/trade/volume values must be finite, consistent and correctly typed.
A new interval must be observed before the preceding streamed candle can enter
forecast history. Wall-clock passage or heartbeat alone cannot finalize or refresh
it. Forming candles remain excluded. Returned frames are independent copies with
`quote_verified=false`, `execution_quote_eligible=false`, and transport explicitly
`REST_BOOTSTRAP_AND_WEBSOCKET_V2_OHLC`. OHLC has no order-book CRC and never acquires
order-book or execution authority. Independent real book receipts are still needed.

Snapshot-before-update, five-minute continuity, monotone within-candle high/low/
volume and closed-candle consistency are enforced. Gaps, revisions or bad OHLC
messages invalidate streamed history. Invalid optional OHLC data/acks do not
relabel or destroy valid book quotes. In that condition the existing bounded REST
path remains, until a physical reconnect supplies new OHLC acknowledgement,
snapshot and genuine bootstrap. This is conservative degradation; the feature
must not be claimed to suppress every REST request during faults.

The existing socket's liveness/reconnect/backoff operates as before. Disconnect
clears stream history immediately. No worker or dashboard opens a second feed.
The installed Kraken paper-pricing client and primary research reuse the same
bounded in-process candle store. A different process without local feed ownership
uses the existing REST fallback; no distributed candle-store guarantee is claimed.

## Cost and storage scope

At most 721 rows per currently subscribed pair (720 history + forming interval),
up to four pairs, are held in RAM. Old in-memory bars can roll out; canonical trades,
Brain/AEVE and permanent research evidence are never deleted. No table, migration,
retention rule, evidence-ID reset or capacity extension is introduced. Existing
health state/logs gain bounded stream counts/status only, not the historical frame.
Existing original-history capture/persistence stays unchanged.

For four markets at one history refresh each five minutes, polling alone can use
1,152 REST reservations/day. With one additional five-minute metadata refresh,
the illustrative total is 1,440, versus 1,200 configured reservations. On an
uninterrupted streaming session, four initial histories plus 288 metadata refreshes
would be about 292 requests/day, excluding discovery, recovery and other users.
That is a workload calculation, not a production measurement, new entitlement,
quota override or guaranteed 80% saving. Bootstrap after disconnect and REST fallback
still consume legitimate reservations. WebSocket events add CPU/network work;
actual Railway savings require controlled before/after measurement.

## Validation and rollback

Synthetic tests cover acknowledgement/snapshot sequencing, real bootstrap identity,
partial candles, next-interval closure, disconnect/liveness, gaps/revisions, volume
regression, invalid values, bounds, session races, no redundant HTTP after warm-up,
unchanged REST receipt times, shared-client installation, and book-channel separation.
The existing unit and disposable PostgreSQL suites must pass at exact head.

`scripts/kraken_ohlc_smoke.py --seconds 15` performs one actual public BTC history
request and shares a public socket between book/OHLC, with no DB, credentials or
orders. A 15-second test establishes connectivity/bootstrap, not multi-day continuity
or profitability. Crossing a candle boundary before bootstrap can conservatively
leave a gap and fail the smoke; this is not repaired by inventing a candle.

Disable the new flag and restart to stop the OHLC subscription and return to the
unchanged REST path. There is no schema rollback or historical evidence rewrite.
Model abstentions, missing decision-consumption proof, full research-ID capacity,
unqualified strategy economics and other held PRs are separate blockers.
