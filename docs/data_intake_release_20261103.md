# Bounded data-intake release candidate

Base main: 547fc95aec249175d07bb2f426db9cee065bd1f7.

Consolidates existing proposals once: #421–424 capacity/safety plus PostgreSQL lock/rollback regressions from 14ae74b; #491 selected planner costs plus full optimizer regressions at efd3d2e; #476/#477 shared closed-candle streaming plus cumulative trade-count integrity; #431 bounded cache working-set correction. This is an integration release candidate, not four independently deployed releases or new strategy logic.

## Controls and rollout

No schema creation or stream activation by code rollout alone. Keep KRAKEN_RESEARCH_EXTENSION_CREATE_ENABLED and KRAKEN_OHLC_WS_ENABLED off during deployment. All readers must understand the third segment before creation: older readers stop at 10,000 and cannot certify receipts for extension rows. Verify all web/worker SHA values, original evidence receipt counts, volume headroom and query latency before explicit activation. Both opt-ins require EXECUTION_MODE=paper, ENABLE_BROKER_SUBMISSION=false, LIVE_TRADING_ARMED=false. Existing reference feed remains separately enabled. No autotraders, entry or exit flags are changed.

Activation of the additive extension creates bounded IDs 10,001–15,000 transactionally (1-second lock wait,3-second statement timeout). No original IDs or source/Brain data are rewritten. Removing the creation flag is not an ingestion stop: once present, the segment stays readable/writable. Rollback must preserve its table and use the existing primary research enablement control if stopping the producer is required. Do not deploy an old reader and claim full new receipt matching.

Five thousand slots provide about4.34days at four records/five minutes, not a permanent growth solution. Current relation sizes scale to roughly133MB for another5000records including Brain copies; this is an estimate, not a maximum, and excludes unmeasured WAL/backup growth. Schema payload byte limits permit roughly819MB before additional overhead. No volume resize or deletion is included.

#420 prospective pilot is excluded: its frozen configuration hard-codes last_source_id<=10000 and cannot use new extension IDs until separately corrected/tested. #392/#393 immutable decision-consumption evidence remains held. Storing research copies is not proof of decision consumption.

## Stream and pricing boundaries

Optional5-minute OHLC uses the same elected socket and at most four existing symbols. Initial closed history requires a real quota-enforced REST bootstrap. Current bars, gaps, regressing cumulative fields and revised closed bars cannot become valid history. Reconnect requires a new generation, acknowledgement, snapshot and bootstrap. OHLC lacks CRC and never becomes execution quotes; order-book CRC and paper pricing checks stay independent.

An OHLC integrity/subscription failure falls back to bounded REST and remains disabled until the shared socket physically reconnects; uninterrupted savings are not certified. Stable warm streams reduce repeated OHLC requests, not product metadata requests. Cache ceiling increases from12to16 for the existing13-key four-symbol workload; no quota/cooldown bypass. No public provider subscription purchase.

Selected-model cost is a minimum: quantity/depth impact or a later authenticated quote can increase final cost. Passing planner gates does not authorize a fill. Model, exact forecast, timestamp, fees, liquidity and portfolio gates remain mandatory.

No profit guarantee, model promotion, expanded live universe, stock-autotrade activation or canonical accounting changes. Record exact candidate CI and public diagnostic separately from production health.

## Scheduled intelligence handoff

The intentional cash-worker standby also stops its scheduled GitHub brief intake. An additional default-off ENABLE_CRYPTO_MARKET_BRIEF_HANDOFF flag reuses the existing validator and15-minute executor to ingest only the configured inbox issue while cash research stays paused. It never starts broad stock collectors, cash scans or order paths. Existing HTTP status/rate-limit diagnostics and durable event/payload deduplication remain intact. Cash and crypto service configurations must agree on standby before activation; no distributed HTTP-poll ownership is claimed. This is curated research intake, not live stock-price coverage or proof of profitable decisions. Missing explicit paper safety settings disables it.
