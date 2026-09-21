# GARIBALDI MARKET ORACLE™ — Oracle Brain

Oracle Brain is the durable engineering/research memory for the project. It is not a trading engine, signal generator, execution router, or replacement for PostgreSQL market/trade evidence.

## Source-of-truth hierarchy

1. Canonical PostgreSQL evidence — trades, lots, decision provenance, forecasts, quotes, execution events, regime economics, accounting state.
2. Versioned code and tests — the exact implementation and safety invariants on the deployed commit.
3. Oracle Brain knowledge ledger — durable engineering lessons, experiment definitions, promotion/retirement notes, and architectural decisions.
4. Human-readable summaries — dashboards and documentation.

A Brain entry never overrides contradictory database evidence or deployed code.

## Permanent invariants

- Never invent missing price, feature, trade, fill, P&L, forecast, decision, or provenance data.
- Learning from a closed trade requires exact immutable entry-time provenance. A later decision is not a substitute.
- Candidate approval, execution eligibility, and actual execution are separate states.
- Super Hybrid is a bounded evidence layer. It cannot approve execution by itself.
- Risk, capital, liquidity, quote-integrity, accounting, and execution-capacity gates remain downstream authorities.
- Paper/shadow research must not implicitly arm broker submission or live-money trading.
- A parameter or model earns additional influence only after sufficient post-cost, out-of-sample evidence.
- Historical losses remain part of the evidence base. Do not erase losses to improve apparent performance.

## Architecture map

Market/provider data
→ normalized features
→ Market Memory / scenario / radar / global intelligence
→ Super Hybrid confluence
→ Council / decision layer
→ capital allocation and portfolio fit
→ risk + execution-capacity + quote integrity
→ paper execution
→ canonical trade ledger and lots
→ realized P&L / MFE / MAE / regime economics
→ learning and Oracle Brain research context

Oracle City/Brain Map visualizes this flow. Oracle Brain preserves why the system is designed this way and what empirical evidence has been learned.

## Automatic market-intelligence loop

The stock intelligence collector and event-opportunity radar feed one canonical `intelligence_events` intake. The radar continuously rotates across company catalysts, macro policy, AI, space, quantum, crypto market structure, commodities, and supply disruptions. Each observation receives a stable event key so repeated polling updates the same record instead of flooding memory.

The intake preserves provider, source URL, event time, first/last observation, verification status, confidence, freshness/expiry, affected symbols, sectors, asset classes, themes, catalysts, risks, transmission channels, and separate fact/inference fields. Source-free claims remain visible as unverified research but carry zero ranking influence.

The Brain learning sync converts canonical events into `oracle_brain_sources` and concept links. Signal research can retrieve relevant, fresh sources by exact symbol, sector, or truly market-wide category. That context can raise the bounded external-catalyst score used by opportunity surveillance, but it cannot create BUY/SELL direction, approve an order, alter position size, bypass price/volume confirmation, bypass Council V3 or risk vetoes, enable broker submission, or arm live trading. Crypto and stock workers use the same read-only retrieval boundary.

`ingest_market_brief()` in `market_intelligence_bridge.py` is the structured contract for an attributed external weekly brief. It deliberately requires per-development provenance and keeps verified facts distinct from inference. A transport that calls this contract must be authenticated and deployed separately; narrative text is never treated as ingested merely because it appeared in a chat.

## What belongs in Oracle Brain

Store durable items such as architecture decisions, experiment hypotheses, known failure modes and fixes, provider/API limitations, strategy/regime lessons supported by canonical evidence, promotion or retirement decisions, and follow-up work future coding agents should understand.

Do not store passwords, API secrets, personal credentials, raw private keys, or unverified claims.

## Experiment lifecycle

Every meaningful strategy or parameter change should follow:

Hypothesis → Shadow/Paper → Provenance check → Minimum sample depth → Post-cost evaluation → Out-of-sample check → Promotion / Hold / Retire

Promotion is not based on win rate alone. At minimum consider sample count and effective sample size, net P&L and expectancy after fees/slippage, profit factor, MFE/MAE and holding behavior, regime and symbol concentration, calibration and evidence quality, execution realism, accounting reconciliation, and whether gains survive a later evaluation window.

## How future coding agents should use this file

Before changing decision, learning, execution, accounting, or safety behavior:

1. Read this file.
2. Inspect current code on the deployed branch.
3. Query the relevant PostgreSQL evidence.
4. Check recent CI and runtime logs.
5. Preserve established invariants unless there is explicit, tested evidence for a change.
6. Record a durable Brain entry when a lesson would otherwise be rediscovered later.

The Brain exists to reduce repeated mistakes and context loss, not to create certainty where evidence is incomplete.
