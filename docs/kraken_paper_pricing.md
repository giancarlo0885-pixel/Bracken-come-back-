# Kraken single-venue paper pricing

The user explicitly authorized Kraken alone for paper pricing. Select it with
`CRYPTO_PAPER_PRICING_PROVIDER=kraken` on the crypto worker and keep:

```
KRAKEN_REFERENCE_WS_ENABLED=true
EXECUTION_MODE=paper
ENABLE_BROKER_SUBMISSION=false
LIVE_TRADING_ARMED=false
```

An unset selector preserves Coinbase. Unknown selectors install the existing
unavailable-source guard. No auto-trading, entry, exit, rotation, model or risk
flag is enabled by this selection. This is not a live exchange connector.

## Data and permission boundary

The elected Kraken WebSocket v2 depth-10 ingestion path now publishes exact
string price/quantity levels and its session ID in its existing singleton state.
No additional production WebSocket or per-tick table is introduced. Its original
provider label, reference-only flags and execution authority NONE are unchanged.
The research-only quote accessor need not include depth; the bounded shared
health snapshot does. A new connection has a new session and needs snapshots.

`KrakenPaperPricing` reuses the existing receipt registration, final quote guard,
capital-source check, depth walk, slippage/latency model and atomic fill evidence
path. It issues a new receipt labeled `Kraken WebSocket v2 paper pricing` with
`kraken-depth10-paper-v1`, `PAPER_SIMULATION_ONLY`, provider verification false,
and `independent_validation=NOT_REQUIRED_SINGLE_VENUE_PAPER_SIMULATION`. It does
not relabel or mutate the reference packet or claim cross-venue verification.
Raw reference copies, tampered/unregistered receipts, disconnected/replaced
sessions and live/broker-enabled contexts fail the final source guard.

Both exchange and original local receipt time must be nonfuture and at most five
seconds old. The original top ten levels must reproduce Kraken's unsigned CRC32,
retain wire decimal precision, be ordered, positive and uncrossed/unlocked.
The existing 1.5% paper spread ceiling and all downstream limits remain.
A heartbeat, DB publication, cache hit or audit does not renew a source time.
Shared-state invalidation is observed when published/read; this architecture is
not a claim of zero-delay propagation of a socket failure across workers.

The existing bounded public-request client supplies exact, online USD product
rules (`AssetPairs`, assetVersion=1): cost/quantity minimums, tick and quantity
increments. Metadata is at most 300 seconds old, not guessed from an alias.
Its HTTP cache, request allowances and cooldowns are unchanged. Metadata failure
still blocks new quotes. Tick pricing itself makes no Coinbase request and no
Kraken REST book request. The worker shares its bounded HTTP cache with primary
research to avoid duplicate OHLC requests. Budget exhaustion for metadata or
history remains possible and must stay explicit; this change does not grant an
unlimited Kraken REST allowance.

Five-minute scans use actual closed Kraken OHLC bars; bar times stay separate
from fresh book times. Research-history failures preserve existing fallback
routing but cannot give those fallbacks Kraken paper authority. Forecast IDs,
exact signal linkage, failed calibration/walk-forward and model vetoes remain
mandatory. Legacy PAXG provenance is not repaired or retrospectively certified.

## Economics and persistence

BUY walks asks for notional; SELL walks bids for reference-derived quantity.
Unknown/below-minimum size and insufficient displayed depth fail closed. Depth
is not invented beyond ten levels. Fractional quantities are simulated; recorded
exchange increments are provenance, not a claim that an exchange order occurred.

The existing conservative **0.60% fee per side** remains an explicit simulation
assumption, not a Kraken fee-tier claim or account-verified charge. Existing
adverse slippage and latency assumptions remain; depth replaces a second impact
proxy. Kraken uses explicit-fee accounting so the fee is charged once in the
cash/ledger path and not also hidden in the fill price. There is no promise that
an actual order would fill at this simulated price.

The original reference depth/checksum/session and product-rule receipt persist
inside the original paper quote in `paper_fill_price_evidence`, in the canonical
trade/fill/lot transaction. The existing 32KiB receipt constraint applies. There
is no schema migration or historical rewrite; permanent evidence stays protected.
Only real eligible paper fills add permanent receipts. The existing singleton
pricing diagnostic table keeps its compatibility name but payload/logs identify
Kraken correctly. Read-only receipt audits recompute the original CRC32 and depth
at the original execution time without fetching substitute evidence. They still
explicitly do not prove immutable decision-time Brain consumption.

Quote-bound excursion measurement accepts authenticated Kraken receipts with
original checksum/session identity, never an invented sequence (v2 has none).
Sampling carries authority NONE and grants no strategy qualification.

## Validation and rollout

`test_kraken_paper_pricing.py` covers precision/CRC, coverage, reference/receipt
separation, freshness/future dates, invalid rules, depth, disconnect/new sessions,
fee separation, unchanged governance, real final guard and audit replay.
`test_kraken_paper_pricing_pg.py` uses disposable local PostgreSQL schemas to
verify BUY/SELL receipts, exact canonical linkage and full rollback.

`python scripts/kraken_paper_pricing_smoke.py --seconds 20` is an explicit public
connectivity/calculation test. It submits no orders and writes no DB records;
it is not production deployment or profitability evidence.

Release requires passing exact-head Python/PostgreSQL CI. Existing workers can
read the extra shared fields. A new adapter with an old producer (no depth or
session) fails closed until the new snapshot appears. Verify merged commit on
Railway and fresh `KRAKEN PAPER PRICING` health before claiming activation.
Rollback to `CRYPTO_PAPER_PRICING_PROVIDER=coinbase` preserves all evidence and
restores the prior pricing requirements; it may be quota-blocked. Do not delete
fills or change risk thresholds to make rollback appear successful.

Official specifications checked during implementation:
- https://docs.kraken.com/api/docs/websocket-v2/book/
- https://docs.kraken.com/api/docs/guides/spot-ws-book-v2/
- https://docs.kraken.com/api/docs/rest-api/get-tradable-asset-pairs/
