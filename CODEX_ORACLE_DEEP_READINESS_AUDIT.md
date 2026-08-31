# GARIBALDI MARKET ORACLE — Codex Deep Readiness Audit

## Mission

This file is a work order for Codex. The goal is **not** to make the application claim it can know the future. The goal is to turn GARIBALDI MARKET ORACLE into a rigorously tested, probabilistic decision system that can demonstrate when it has an edge, quantify uncertainty, refuse weak or unverified opportunities, and prove that its paper/shadow behavior is suitable for later supervised brokerage use.

**Do not fund or enable real-money execution because this file exists.** Funding/read-only account state and live order submission are separate decisions. Keep the repository safety invariants in `AGENTS.md` intact throughout this work.

## Non-negotiable safety invariants

1. Read `AGENTS.md` first and obey it.
2. Keep `EXECUTION_MODE=paper`.
3. Keep `ENABLE_BROKER_SUBMISSION=false`, `LIVE_TRADING_ARMED=false`, and live submission disabled.
4. Do not lower BUY/HOLD thresholds, forecast gates, risk limits, quote-verification rules, liquidity limits, concentration limits, drawdown limits, or other safety gates merely to create more trades or make historical results look better.
5. Do not tune against the full history and then report that same history as out-of-sample performance.
6. Do not commit credentials, API keys, Robinhood keys, signatures, database secrets, or `.env` contents.
7. Do not use brokerage username/password automation.
8. All market-data, model, risk, and broker failures must fail closed for execution.
9. Missing data must remain missing/unknown; never synthesize a favorable value to pass a gate.
10. Any proposed live-broker code must remain unreachable from production until a separate explicit live-trading authorization is given.

---

# 1. Current evidence that must be treated as audit starting points

These are **observations to verify in the current code**, not permission to assume the implementation is correct.

## 1.1 The core signal confidence is not an empirical probability

Inspect `engine.py::analyze_market`.

Current behavior includes a hand-weighted technical score using momentum, trend, RSI, Bollinger position, MACD, volume, volatility, regime, and news sentiment. `confidence` is derived from distance of the score from 0.5:

- it is not currently demonstrated to be a calibrated probability of future profit;
- the score weights appear manually specified;
- annualized volatility uses a fixed `sqrt(252)` in this module even though the system also handles intraday and 24/7 crypto data.

Codex must determine whether this module is still in the production decision path and quantify its influence on final decisions.

## 1.2 The current forecast model is a simple statistical diffusion model

Inspect `forecasting.py::forecast_price`.

Current behavior estimates drift and volatility from recent log returns, generates a target and interval from a log-return diffusion assumption, and maps a drift/volatility z-like value through a logistic function to produce `probability_up`.

This is a useful baseline, not sufficient proof of predictive edge. Audit whether:

- the probability is calibrated out-of-sample;
- distributional assumptions hold during high-volatility and regime transitions;
- horizons are consistent across 5m/1h/1d and crypto 24/7 data;
- uncertainty intervals have correct empirical coverage;
- model drift is detected;
- predictions beat naive baselines after costs.

## 1.3 The forecast models are explicitly still shadow models

Inspect `model_registry.py`.

At the time of this audit, the registry code registers:

- `regime-aware ensemble / v36-advisor-foundation` as `shadow`, reason: `requires walk-forward validation`;
- `log-return diffusion / v36-timeframe-aware` as `shadow`, reason: `requires walk-forward validation`.

This is a **hard readiness blocker** until Codex proves what is required to move a model from shadow to approved. Do not simply change the status value. The status may only become `approved` after objective evidence and tests exist.

## 1.4 Forecast validation is too weak to be the final approval standard

Inspect `forecast_quality.py`.

The current summary uses sample count, directional accuracy, MAPE, and a calibration error defined as the absolute difference between mean predicted probability and mean realized-up frequency.

That mean-only calibration metric can hide severe miscalibration inside probability buckets. Codex must add or prove equivalent support for:

- Brier score;
- log loss / cross entropy where appropriate;
- expected calibration error (ECE) with bins;
- maximum calibration error (MCE);
- reliability table/curve data;
- calibration slope and intercept where statistically valid;
- confidence intervals for directional accuracy and profitability;
- interval coverage for forecast low/high ranges;
- per-horizon, per-asset-class, and per-regime validation;
- comparison against naive and simple benchmark models.

## 1.5 Several decision probabilities/EV values are formula-derived, not proven forecasts

Inspect at minimum:

- `quant_trade_standard.py::assess_trade`;
- `oracle_intelligence.py::evaluate_opportunity`;
- `scenario_engine.py::assess_scenarios`.

Examples that require scrutiny:

- `probability_win` in `quant_trade_standard.py` is a formula based on alpha/relative-value scores;
- `probability_of_profit` in `oracle_intelligence.py` is blended from hand-built probability components and scenario output;
- scenario drift and probabilities are generated from hand-coded relationships and Monte Carlo assumptions.

Do not present these values to a user as statistically calibrated probabilities unless validation proves that interpretation. If they are heuristic scores, rename or relabel them accordingly.

## 1.6 Market memory may have decision-attribution leakage/misalignment

Inspect `market_memory.py::record_closed_trade_memory`.

The current code queries the most recent approved `oracle_decision_audit` row for a market/symbol when a trade closes. Codex must verify whether this always corresponds to the **exact immutable entry decision** for that position.

If a newer approved decision occurred after entry, attaching it to the closed trade can contaminate Trade DNA with post-entry information or the wrong feature vector. This is a P0 issue if confirmed.

Required fix if confirmed:

- every position/lot/trade must store immutable `entry_decision_id`, `entry_signal_id`, `entry_forecast_id`, quote/correlation ID, model/version, and feature snapshot at entry;
- closed-trade memory must join to the exact entry provenance, never “latest decision for symbol”;
- add regression tests that deliberately create later decisions and prove the older trade still links to its original entry state.

## 1.7 Backtesting is improved but not yet a full research validation framework

Inspect `backtesting.py` and `test_backtesting.py`.

Positive existing behavior to preserve:

- signal window ends before the execution bar;
- entries/exits use next-bar/open or conservative stop logic;
- adverse paper fill modeling is reused;
- when stop and target are touched in one bar, the unfavorable ordering is assumed.

Remaining questions Codex must answer:

- Sharpe scaling must use the actual bar frequency and asset calendar, not a blanket `sqrt(252)`;
- current backtest is largely single-symbol and all-in/all-out and does not prove portfolio-level behavior;
- add true expanding/rolling walk-forward evaluation;
- add purge/embargo around labels when features/horizons overlap;
- test multiple market regimes and crash periods;
- benchmark against buy-and-hold, cash, simple momentum, and random/no-skill baselines;
- include realistic spread, fees, slippage, latency, participation caps, partial fills, unavailable quotes, and rejected orders;
- quantify turnover and capacity;
- prevent survivor/universe selection bias;
- test robustness to parameter perturbations instead of one best parameter set;
- report out-of-sample results by fold so one period cannot hide weak regimes.

## 1.8 News intelligence is currently a weak feature and runtime providers are rate-limited

Inspect `news_intelligence.py` and current provider-health/runtime logs.

Current code scores headline sentiment with a small positive/negative keyword lexicon. Grounded Google/Gemini and NewsAPI are fallbacks/sources, but production runtime has recently shown both Gemini HTTP 429 quota exhaustion and NewsAPI rate limiting, leaving Google News RSS as the fallback.

Codex must assess and improve, without making LLM text an execution price source:

- publication timestamp and event-time normalization;
- duplicate/syndicated-story collapse;
- source reliability/provenance weighting;
- primary-source preference (SEC/company/regulator/exchange/official releases where available);
- symbol/entity disambiguation;
- event classification (earnings, guidance, legal/regulatory, hack/security, listing/delisting, macro, rates, etc.);
- sentiment/event decay with time;
- whether a story was already known before the decision timestamp;
- evidence that news features add out-of-sample value rather than noise;
- clear degradation when premium providers are unavailable.

Do not allow grounded AI output to directly authorize an order. It may contribute evidence only after provenance/time validation.

## 1.9 Crypto universe/provider reliability needs cleanup

Recent production logs have shown examples of:

- Yahoo missing-price errors for symbols such as `UNI-USD`, `SUI-USD`, `APT-USD`, `PEPE-USD`, and `MATIC-USD`;
- recurring `CRYPTO | EXECUTION SKIP | no verified live quote` for some symbols;
- Coinbase consensus blocking `ARB-USD` on price divergence;
- Coinbase stale-reference blocking `SHIB-USD`.

The **blocking behavior is correct and must be preserved**. The gap is operational quality: the scanner should spend less time on unsupported/stale mappings.

Codex must audit whether the active crypto execution universe is intersected with the broker's current API-tradable pairs and validated provider capabilities. Build durable symbol-alias/migration handling (for example token renames/migrations) and capability cooldowns so unsupported symbols do not repeatedly generate noisy failed requests.

## 1.10 Robinhood read-only authentication works, but account/buying-power compatibility still needs proof

Current production preflight has recently shown:

- connection configured;
- auth PASS;
- account PASS;
- crypto PASS;
- 14 tradable pairs;
- quote PASS;
- buying power FAIL;
- order journal PASS;
- live trading DISARMED.

Inspect `robinhood_crypto_api.py` carefully. In particular, `trading_pairs()` is using v2 while `account_details()` currently calls `/api/v1/crypto/trading/accounts/` even though the configured API mode/version is v2. Compare all paths and response schemas against the current official Robinhood Crypto Trading API documentation.

Required read-only brokerage audit:

- v2 account endpoint and schema;
- buying-power field/schema and currency;
- holdings permission and response;
- open/recent orders permission and response;
- tradable-pair metadata/increments/minimums;
- best bid/ask freshness;
- clock skew/timestamp tolerance;
- API error classification and retries;
- credential redaction;
- rate limits;
- no order call during preflight.

Do not enable order placement while fixing read-only compatibility.

## 1.11 The Robinhood `OrderJournal` is currently process-memory state

Inspect `robinhood_crypto_api.py::OrderJournal` and all live-broker adapter code.

An in-memory dictionary is not sufficient for real-money exactly-once/reconciliation safety because Railway restarts can erase local state.

Before any live submission can be considered, Codex must design and test a durable PostgreSQL broker order journal containing at minimum:

- immutable client order ID / idempotency key;
- decision/proposal hash;
- signal/forecast/decision IDs;
- symbol, side, requested notional/quantity;
- broker request state and timestamps;
- broker order ID;
- all observed broker states;
- fills/partial fills/fees;
- reconciliation status;
- unknown/timeout state that prevents blind retry;
- operator/manual approval identity when applicable.

A restart after “submit requested but response unknown” must **never** result in an automatic duplicate order.

## 1.12 Accounting/UI integrity must match the canonical ledger

Audit trade-history and dashboard P/L paths (`app.py`, `dashboard_helpers.py`, trade/lot/ledger helpers). Entry fees, exit fees, partial fills, and realized P/L must reconcile to the canonical execution/lot ledger. A UI row showing zero P/L for a BUY is acceptable only if the label is not claiming full fee-aware trade profitability. No dashboard metric should contradict canonical cash/lot accounting.

---

# 2. Codex audit protocol — inspect first, modify second

Codex must not start by rewriting the strategy. First produce an evidence map.

## Phase A — Build the exact production decision graph

Trace the current crypto and stock paths from source to final action:

`provider/raw data -> normalization -> verified quote -> history/features -> signal -> forecast -> forecast validation -> quant/scenario/memory/global/radar -> final Oracle decision -> capital allocation -> pre-trade risk -> execution policy -> paper broker / future broker -> ledger -> dashboard`

For each arrow, document:

- source file/function;
- input schema;
- output schema;
- timestamp semantics;
- symbol identity semantics;
- whether data can be cached and cache key;
- failure behavior;
- whether execution can proceed when the component is missing;
- persistence table/ID used for provenance;
- tests covering it.

Create `CODEX_AUDIT_RESULTS.md` with a table:

| Severity | Component | File/function | Finding | Evidence | Failure mode | Proposed fix | Test required | Live blocker? |
|---|---|---|---|---|---|---|---|---|

Severity definitions:

- **P0** — could create false predictive confidence, data leakage, wrong symbol/price, duplicate live order, incorrect broker state, or loss of execution provenance.
- **P1** — materially weakens decision quality, calibration, robustness, capacity, or reliability.
- **P2** — observability, UX/accounting presentation, maintainability, or efficiency issue that does not by itself authorize a bad order.

## Phase B — Run an explicit leakage/time-travel audit

Search every feature/model/memory/research path for future leakage. Add tests proving that a decision at time `T` cannot access:

- bars after `T`;
- the execution bar close/high/low when the decision should only know prior bars;
- news published after `T`;
- forecast-validation outcomes not known at `T`;
- future trade outcomes;
- later Oracle decisions for an earlier trade;
- future constituent/universe membership;
- revised/corrected data that would not have been available at `T` unless the backtest explicitly models revisions.

Implement a reusable “as-of” test harness where practical.

## Phase C — Establish honest baselines

Every forecasting/decision model must be compared against relevant no-skill/simple baselines. At minimum:

- last price / zero-return forecast;
- historical mean or random-walk forecast;
- 50% direction probability;
- simple momentum rule;
- buy-and-hold where appropriate;
- cash/no-trade benchmark;
- existing production heuristic as incumbent benchmark when testing a replacement.

A more complex model is not allowed to be called better unless it improves out-of-sample performance and/or calibration after realistic costs with uncertainty bounds.

---

# 3. Statistical validation requirements

## 3.1 Split methodology

Use chronological walk-forward validation only. No random shuffled train/test split for time-series claims.

Where labels overlap, use purging and an embargo. The embargo should cover the maximum forecast/trade horizon required to prevent contamination.

Report each fold individually and aggregate only after showing dispersion.

## 3.2 Probability calibration

For each probability-like output intended to mean probability, compute:

- sample count;
- event rate;
- Brier score;
- log loss where valid;
- ECE;
- MCE;
- reliability bins;
- calibration slope/intercept where enough data exists;
- bootstrap or statistically justified confidence intervals.

Do this by:

- stock vs crypto;
- horizon/interval;
- regime;
- confidence bucket;
- major-liquidity tier if sample size permits.

If a value cannot be validated as a probability, change the user-facing label to `score`, `strength`, or `heuristic confidence` rather than leaving a misleading percentage.

## 3.3 Forecast interval validation

For forecast low/high ranges, verify empirical coverage. If a nominal 90% interval contains realized outcomes far less than ~90% out-of-sample, recalibrate the interval or stop displaying it as a 90% interval.

## 3.4 Trading-performance validation

At minimum report:

- net return after all modeled costs;
- benchmark-relative return;
- Sharpe using correct observation frequency;
- Sortino;
- Calmar;
- max drawdown;
- expected shortfall/tail loss;
- turnover;
- number of trades;
- win rate;
- average win/loss;
- profit factor;
- exposure/time-in-market;
- capacity/liquidity participation;
- performance by regime;
- performance by symbol group/liquidity tier;
- performance by confidence/score bucket.

Do not annualize intraday or crypto results with stock-daily assumptions.

## 3.5 Robustness / anti-overfitting

Perform at least:

- parameter perturbation/sensitivity tests;
- bootstrap or block-bootstrap uncertainty where appropriate;
- performance excluding the best symbols and best months;
- performance during the worst regimes;
- transaction-cost stress (for example 1x, 1.5x, 2x modeled cost);
- latency/slippage stress;
- provider outage/missing-data simulation;
- stale quote rejection simulation;
- universe changes/delistings;
- multiple-testing accounting if many variants are explored.

Do not select the best parameter combination from hundreds of trials and report it without correcting for data snooping.

---

# 4. Required architecture upgrades if the audit confirms the gaps

## 4.1 Introduce immutable decision provenance

Every actionable decision should have one immutable provenance record linking:

- decision ID;
- signal ID;
- forecast ID;
- model + version;
- feature snapshot/hash;
- requested/provider symbol;
- provider/capability;
- quote timestamp;
- quote correlation/cache identity;
- news/event evidence IDs and their publication timestamps;
- regime;
- portfolio snapshot ID;
- risk-check version/results;
- proposal hash;
- eventual order/fill IDs.

The same provenance must be visible in paper, shadow-live, and future live paths.

## 4.2 Separate heuristic scores from calibrated probabilities

Create an explicit schema such as:

- `signal_score` — heuristic/ranking strength;
- `model_probability_up` — model output before calibration;
- `calibrated_probability_up` — only present after calibration model is approved;
- `probability_of_net_profit` — only present if validated against realized net outcomes after costs;
- `uncertainty_state` — LOW/MEDIUM/HIGH/UNVALIDATED;
- `calibration_sample_count` and validation version.

Execution must not treat heuristic score percentages as probabilities.

## 4.3 Add a real model-validation pipeline

Build a repeatable command/module that can regenerate validation results from canonical data without manually editing DB rows.

Expected outputs:

- model version;
- dataset version/hash/time range;
- fold definitions;
- metrics by fold/bucket;
- calibration metrics;
- benchmark comparisons;
- go/no-go result;
- reason for approval/rejection.

Model registry approval should reference this validation artifact/version.

## 4.4 Add a live-shadow broker-validation mode

Before any real order permissions are enabled, implement a **read-only shadow-live** recorder that:

1. receives an otherwise actionable paper decision;
2. checks the actual Robinhood tradable-pair metadata and BBO;
3. records what exact order would have been proposed;
4. records broker spread/mid and later observable price evolution;
5. does **not** submit anything;
6. compares the paper fill/cost estimator against the real venue conditions.

Use this to measure live-market slippage assumptions, rejection rates, stale quote rates, and decision latency.

## 4.5 Broker state must be durable and restart-safe

Move future broker order/reconciliation state into PostgreSQL with transactional/idempotent semantics. Create tests for:

- timeout before broker acknowledgement;
- timeout after broker accepted order;
- worker crash/restart during submit;
- partial fill then restart;
- duplicate decision event;
- duplicate webhook/poll response;
- canceled/rejected order;
- broker returns unknown/unexpected state.

The correct behavior under ambiguity is `UNKNOWN_RECONCILE_REQUIRED`, not automatic retry.

## 4.6 Build a broker-compatible crypto universe

The live/shadow crypto universe should begin with Robinhood API-tradable pairs, then intersect with reliable market-data support. Maintain canonical aliases separately from provider symbols. Unsupported mappings should enter cooldown rather than repeatedly failing.

## 4.7 Improve news/event intelligence only if it proves incremental value

Replace headline word counting as the primary sentiment method with a timestamped event/evidence pipeline. However, do not add complexity unless walk-forward ablation shows the news/event feature improves out-of-sample decision quality, risk avoidance, or calibration.

Run ablations:

- price/technical only;
- + regime;
- + volume/liquidity;
- + news/events;
- + market memory;
- full stack.

Report whether each layer adds value.

---

# 5. Production-readiness gate command

Create a non-destructive command, suggested name:

```bash
python oracle_readiness.py
```

It must never place an order. It should return non-zero when a mandatory gate fails and print a machine-readable + human-readable report.

Minimum checks:

1. safety switches remain disarmed;
2. PostgreSQL reachable and canonical tables consistent;
3. no unresolved data migrations/schema mismatch;
4. provider health and quote verification rules active;
5. active symbols have valid canonical/provider mappings or are quarantined;
6. forecast model status and validation artifact present;
7. calibrated probability metrics meet approved policy or outputs are labeled unvalidated;
8. no confirmed P0 leakage issues;
9. paper ledger reconciles cash/positions/fills/fees;
10. Robinhood read-only auth/account/pairs/BBO/holdings/orders checks pass where permissions allow;
11. broker buying-power state is understood (ZERO vs POSITIVE vs MISSING/INVALID);
12. durable broker journal/reconciliation design exists before live capability can be considered;
13. shadow-live sample requirements are satisfied before any separate live review.

Suggested final result classes:

- `NOT_READY_RESEARCH`
- `READY_FOR_PAPER_VALIDATION`
- `READY_FOR_LIVE_SHADOW`
- `READY_FOR_MANUAL_LIVE_REVIEW`

**Never output `LIVE_ENABLED` and never change environment switches.**

---

# 6. Minimum evidence before discussing account funding for trading

Depositing funds is not technically required to complete most research validation. Do not use a deposit as a substitute for model proof.

Before recommending that real capital be made available for trading, the report should show all of the following:

1. Zero unresolved P0 findings.
2. Exact entry-decision provenance is immutable and tested.
3. No look-ahead/time-travel leakage found by automated tests.
4. Forecast/decision probabilities are either demonstrably calibrated or explicitly relabeled as non-probabilistic scores.
5. Approved model status is backed by reproducible walk-forward evidence, not a manual DB status change.
6. Out-of-sample performance remains economically positive after stressed realistic costs and does not depend on one symbol/period.
7. Risk limits are tested under crash/high-volatility/provider-outage scenarios.
8. Paper accounting reconciles to the canonical ledger.
9. Robinhood read-only account, pair, BBO, holdings, order-history and buying-power schemas are verified using current v2 docs/permissions.
10. Live-shadow broker observations show the Oracle's price/cost assumptions are realistic.
11. Persistent broker reconciliation/idempotency is implemented and chaos-tested.
12. The system can explain `NO TRADE` as confidently as it explains a trade.

For live-shadow operational evidence, propose a statistically defensible minimum sample policy. A conservative starting policy can require both a calendar duration and a decision count (for example 30 days and >=200 broker-validated shadow proposals), but Codex should justify the final threshold from observed event frequency and confidence intervals rather than treating those numbers as magic.

---

# 7. Files/modules that must receive special attention

At minimum inspect:

- `AGENTS.md`
- `config.py`
- `engine.py`
- `forecasting.py`
- `forecast_quality.py`
- `model_registry.py`
- `quant_trade_standard.py`
- `oracle_intelligence.py`
- `oracle_one.py`
- `scenario_engine.py`
- `market_memory.py`
- `global_intelligence.py`
- `opportunity_radar.py`
- `research_lab.py`
- `market_worker.py`
- `oracle_bot.py`
- `capital_allocator.py`
- `risk_engine.py`
- `execution_policy.py`
- `market_data.py`
- `provider_router.py`
- `crypto_execution_guard.py`
- `paper_execution_reality.py`
- `paper_execution_accounting.py`
- `paper_fee_policy.py`
- `profit_attribution.py`
- `backtesting.py`
- `news_intelligence.py`
- `robinhood_crypto_api.py`
- `robinhood_agentic_mcp.py`
- `broker_interface.py`
- `database.py`
- `migrations.py`
- `app.py`
- `dashboard_helpers.py`
- all tests touching the above modules.

Also search globally for:

- `confidence`
- `probability`
- `expected_value`
- `latest`
- `ORDER BY id DESC LIMIT 1`
- `datetime.now`
- `utc_now`
- `shift(-`
- `iloc[i+`
- backfill/revised-data paths
- default/fallback price values
- `except Exception: return []/{}/0`
- broker retry logic
- symbol aliasing
- any `sqrt(252)` assumption.

Every occurrence that can affect a trade decision should be classified as safe, questionable, or requiring a fix.

---

# 8. Required tests to add

Codex should add targeted tests, not only broad smoke tests.

Required categories:

### Temporal integrity
- future bar is inaccessible;
- future news is inaccessible;
- later decision cannot alter earlier trade DNA;
- forecast validation only uses outcomes available at evaluation time.

### Probability/calibration
- reliability-bin calculation;
- Brier/log-loss correctness;
- ECE/MCE correctness;
- insufficient sample => no approval;
- confidence interval policy => fail closed when inconclusive.

### Backtest realism
- next-bar execution;
- correct annualization by interval/asset class;
- fees/slippage/spread/latency included;
- partial fill/liquidity-cap behavior;
- rejected/stale quote produces no fill;
- walk-forward folds do not overlap improperly.

### Broker read-only
- v2 signed account request path;
- buying-power schema parse;
- holdings/orders read parse;
- pair increments/minimums;
- BBO symbol/freshness/spread checks;
- auth secrets never appear in errors/logs.

### Broker reconciliation
- durable idempotency;
- crash/restart;
- timeout/unknown state;
- partial fill;
- duplicate event;
- no blind retry.

### Provider/universe
- unsupported symbols quarantined;
- token alias/migration mapping;
- stale Coinbase reference blocks execution;
- price divergence blocks execution;
- fallback quote cannot become falsely provider-verified.

### Accounting
- entry/exit fees included exactly once;
- partial-fill lots reconcile;
- dashboard totals equal canonical ledger totals.

---

# 9. Codex deliverables

Codex should produce changes in small reviewable commits/PRs. The first pass should be audit/reporting, not a giant rewrite.

Required deliverables:

1. `CODEX_AUDIT_RESULTS.md` — evidence-backed findings with P0/P1/P2 severity and source references.
2. `oracle_readiness.py` — non-destructive readiness command.
3. Tests for every confirmed P0 issue.
4. A reproducible walk-forward validation command/pipeline.
5. Calibration metrics and model-validation persistence/versioning.
6. Exact entry-decision provenance fix if the market-memory issue is confirmed.
7. Robinhood v2 read-only schema/path fixes and tests.
8. Persistent broker-journal/reconciliation design and implementation before any live submission work.
9. Live-shadow broker comparison recorder.
10. Provider/universe cleanup for unsupported crypto mappings.
11. A concise final `GO_NO_GO.md` summarizing remaining blockers.

For each PR, include:

- problem statement;
- evidence;
- implementation;
- tests;
- migration impact;
- rollback plan;
- whether the change affects paper decisions, live-read-only behavior, or neither.

---

# 10. Validation commands

Follow `AGENTS.md` and run at minimum:

```bash
python -m compileall .
python -m pytest
```

Also run any PostgreSQL integration suite and the new readiness/validation commands. Do not merge a P0/P1 fix with failing CI.

---

# 11. Final standard

The desired Oracle is not a system that claims certainty. It is a system that:

- knows exactly what information was available at decision time;
- distinguishes signal strength from calibrated probability;
- proves its edge chronologically out-of-sample;
- quantifies uncertainty and tail risk;
- survives missing providers and bad symbols without inventing data;
- refuses trades when evidence is weak or market/broker prices disagree;
- maintains immutable provenance from evidence to fill;
- reconciles every broker action durably and idempotently;
- can explain why it did **not** trade;
- can be audited after the fact without reconstructing history from mutable/latest rows.

Do not optimize for the number of BUY signals. Optimize for **truthfulness, calibration, reproducibility, execution integrity, and risk-adjusted net outcomes**.
