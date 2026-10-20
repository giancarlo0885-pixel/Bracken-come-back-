# Accurate crypto paper pricing and durable research evidence

Robinhood remains removed. Coinbase's public Exchange REST L2 book now supplies
**paper simulation pricing only**, independently checked against Kraken's existing
checksum-validated reference books. No private credentials, broker submission,
live orders, model promotion, trading-flag enablement or strategy-threshold change.

## Pricing contract

The adapter starts only with paper mode, broker/live flags false and the existing
Kraken reference opt-in enabled. `COINBASE_PAPER_PRICING_ENABLED=false` can disable
it; otherwise it defaults on in this safe context. No Railway variable change is
required. Ineligible runtime settings retain the no-execution-source guard.

Coverage requires an actual fresh Kraken reference plus an exact online Coinbase
USD product. Cancel-only, limit-only, post-only, auction and trading-disabled
products fail closed. Product minimums and increments are retained as provenance;
this is a fractional-quantity paper simulator, not an exchange order gateway.

The public product book must have a valid sequence, ordered positive levels,
uncrossed sides and no auction. Keep at most 50 levels per side. Provider event
and receipt times must be valid, at most five seconds old, with the existing
clock-skew limits. Independent venue timestamps must align within two seconds;
books can differ, with a maximum 75bp mid divergence and 1.5% spread bound.
Existing downstream spread, liquidity, risk and concentration checks still apply.

Paper price eligibility is **not** broker verification. Payloads explicitly carry
provider_quote_verified=false, paper_reference_verified=true,
execution_authority=PAPER_SIMULATION_ONLY and broker_submission=NONE. Kraken's
original quotes retain reference_only=true and execution eligibility false.
Only locally registered, unchanged Coinbase packets can reach the final fill
boundary. A copied Kraken quote, altered book or forged fill price fails.
Capital candidates use the same active-source check. Legacy `quote_verified=true`
alone cannot qualify a candidate when no pricing source is configured, including
the forecast-optional core-rebalance path. Original quote flags remain unchanged.

Walk displayed asks for BUY notional and bids for SELL quantity. Unknown notional,
below-product-minimum requests or insufficient visible depth are rejected.
Measured depth replaces the volume-based impact proxy; existing adverse slippage
and latency assumptions remain. Use a modeled 0.60% standard taker fee per side,
charged separately through the existing atomic fee-accounting path. This is the
published maximum standard tier, **not** a verified account fee or guaranteed
real-market fill. No historical fees or costs change.

Before a BUY, retain exact persisted signal/forecast scope, original chronology,
active model identity, approved model status, validation and walk-forward model
governance. Negative Brier skill, failed governance, missing references or model
abstention cannot be replaced by a favorable paper mark. Protective SELL checks
retain the original risk/quote controls; no strategy approval is invented.

## Forecast handoff and storage

For supported five-minute crypto scans, use actual closed Coinbase candles and
preserve their bar timestamp separately from the fresh Coinbase book timestamp.
Signal price uses the actual observed mid; original analysis price is retained.
Forecasting's live-data requirements and causal-model abstentions are unchanged.
Unsupported symbols/timeframes preserve research-provider routing and cannot
borrow the paper adapter's execution authority. Historical holdings outside
supported coverage, including an unsupported PAXG position, are not force-closed.

`coinbase_paper_price_state` is a singleton diagnostic snapshot. Cached books are
revalidated against their original event and receipt times; readers label old
observations as noncurrent. Live books use a two-second cache, historical data a
five-minute cache. Existing shared provider budgets and local/shared 429 cooldowns
remain; exhausted budgets or unavailable data block prices. Neither budgets nor
provider entitlements are increased. No repeated request can renew a source time.

`paper_fill_price_evidence` stores the exact original packet, depth, reference
comparison and fee model keyed to the committed fill. It is inserted in the
same transaction as the canonical trade, fill and lot links. Failure rolls the
whole transaction back. The table is protected from automatic retention and grows
only with real paper fills, never every feed tick. Migration adds tables only.

## Brain research receipts

The existing bounded Kraken evidence ledger now also retains genuine abstentions,
with no fabricated forecast ID. Original forecasts and abstentions are copied
idempotently into Brain sources as context-only research, with original snapshots,
history fingerprints and exact evidence IDs. They are not Brain trade episodes,
AEVE qualifying outcomes or model-approval evidence. Confidence is zero for
ranking; original prediction probabilities remain in the preserved payload.

The research writer commits the source copies, then reads matching Brain/ledger
rows back and logs `KRAKEN BRAIN RECEIPT`. Counts expose unmatched originals; this
is durable ingestion proof, not proof that a decision used that memory. The normal
Brain learner also reconciles the bounded backlog. Existing 5000-record research
ledger capacity remains; capacity exhaustion is explicit, never deletion.


## Provider-budget incident audit (read-only)

The Coinbase paper-pricing adapter and the optional Kraken-primary research's
Coinbase diagnostics both call `PublicResearchData.request()`. Each call that
misses the **process-local** cache reserves from the same
`Coinbase Public Research / __provider__` UTC-day PostgreSQL ledger. The configured
ceiling is 2,400 reservations/day; Kraken's separate public REST research ceiling
is 1,200/day. The Coinbase L2 book cache expires after two seconds; product and
five-minute candle caches expire after 300 seconds. Separate processes do not
share their HTTP caches. The cache never renews source or receipt timestamps.

At one uncached Coinbase request every ten seconds, demand is 8,640/day,
already above the entire nominal 2,400/day allowance. This arithmetic diagnoses
a capacity risk, **not** a measured request count, rate-limit entitlement,
confirmed cause of a particular ledger denial, or an executable pricing quote.
More frequent paper scans, multiple symbols and research consumers make the
shortage worse. Slowing requests must never lengthen quote freshness windows.
A sustained high-frequency paper source would need a separately validated
streaming or appropriately budgeted execution-pricing architecture.

Until actual ledger counts are available, `PROVIDER_BUDGET_BLOCKED` is ambiguous.
The public research adapter now distinguishes these *fail-closed* statuses:
`PROVIDER_BUDGET_EXHAUSTED` (actual reservation quota exhausted),
`PROVIDER_LEDGER_UNAVAILABLE` (shared-ledger read/write error),
`PROVIDER_COOLDOWN` (real local/shared cooldown), and
`PROVIDER_BUDGET_BLOCKED` (unknown reservation denial). They expose no SQL
exception text, credentials, unverified prices or bypass paths.

The elected Kraken-primary research worker now emits a bounded
`PUBLIC RESEARCH BUDGET` log every successful five-minute cycle. This executes
one read-only `SELECT` over the two named provider-wide ledger rows for the
current UTC day (limit two). It returns configured allowance, requests
reserved, remaining reservations and active cooldown state, or an explicit
missing/inconsistent/unavailable status. It cannot see external Coinbase API
account usage, infer actual data coverage or grant execution authority.
Provider failures report exception *classes only*, never database error text.
This runtime report will appear only **after** the reviewed change is deployed
and the elected research worker is enabled. It is not running in production
during this PR's review.

The following query is for an authorized **read-only** production database session;
it has **not** been executed as part of this change:

```sql
SELECT provider, capability, utc_date, daily_budget, requests_used,
       remaining_budget, last_failure, cooldown_until, updated_at
FROM provider_budget_ledger
WHERE provider IN ('Coinbase Public Research', 'Kraken Public Research')
ORDER BY utc_date DESC, provider, capability
LIMIT 12;
```

Record current and previous UTC days, match the original log timestamps, and
verify whether the ledger actually exhausted versus becoming unavailable.
The ledger does not attribute spend to each code path. Neither this diagnostic
change nor its tests increase quotas, retry on denied reservations, recycle
timestamps, change risk/governance gates, reconcile missing legacy signals,
authorize deployment, or claim validated profitability.


## Documentation consulted

- https://docs.cdp.coinbase.com/exchange/reference/exchangerestapi_getproductbook
- https://help.coinbase.com/en/exchange/trading-and-funding/exchange-fees

Retrieved October 19, 2026. L2 is aggregated public depth, with sequence, timestamp
and auction metadata. Fee documentation states standard taker fees up to 0.60%.

A healthy pricing adapter does not establish profitable expectancy. Qualified
forward outcomes and independent post-cost validation remain required. Live
trading stays disabled.


### Zero-budget cooldown marker is not measured quota exhaustion

The production table definition and mark_provider_cooldown_db in
global_adaptive_engine.py establish a distinct case: recording a provider
cooldown before any reservation for the UTC day creates a row with
daily_budget=0, requests_used=0 and remaining_budget=0. That is NOT
evidence that 2,400 Coinbase requests (or 1,200 Kraken requests)
were consumed.

The elected provider-ledger report distinguishes this situation as
COOLDOWN_MARKER_NO_CAPACITY when data_mode=shared_cooldown and prints
created_as_cooldown_marker=true and exhaustion_proven_by_ledger=false.
An intentionally configured zero allowance with a different data_mode
is labeled ZERO_CONFIGURED_BUDGET instead. Both labels avoid the false
claim that the normal positive quota was used up.
It separately indicates whether cooldown_until is active. The label
CONFIGURED_BUDGET_EXHAUSTED (or an exhaustion_proven_by_ledger=true
flag during an active cooldown) requires a positive configured daily
budget whose recorded requests_used equals that allowance, with no
remaining reservations. This distinction changes diagnostic labeling
only; it does NOT reset budgets, repair cooldown marker rows,
increase provider access, make data available or permit a trade.

The configured ledger records reservations rather than confirmed
successful external API requests. A fully consumed positive budget
therefore proves reserved capacity exhaustion, not externally
successful requests or an actual exchange-imposed limit.
