# Oracle FOMO Intelligence V1 — observational research

## Scope and data
- Integrated into `opportunity_radar.assess_opportunity_radar()` as `radar.fomo_shadow`. The Oracle decision snapshot already serializes the radar output.
- Uses point-in-time **existing** signal fields: 5- and 20-bar momentum (historically named `momentum_5d`/`momentum_20d`), relative volume, trend strength, RSI and Bollinger position. Does not claim to measure social-media activity, order-book imbalance, funding/open interest, sentiment acceleration or quote freshness.
- Outputs one of `unavailable`, `quiet`, `emerging`, `acceleration` or `exhaustion`. An unavailable input results in unavailable output, not a substitute value.
- Intensity and exhaustion scores are **uncalibrated feature heuristics** from 0–100, not profit likelihoods, directional forecasts, validated edges or trading advice. A single volume-ratio snapshot is **not** volume acceleration. Upstream indicator provenance requires separate validation.

## Safety contract
- `research_only=true`, `calibrated_probability=false`, `execution_impact=NONE`, `promotion_authorized=false`.
- FOMO is attached after Opportunity Radar's existing scoring logic and does not enter risk, score, veto, position multiplier, forecast, broker, order, portfolio or capital calculations.
- No live trading arming, new orders, threshold changes, database migrations, account resets, or historical trade rewrites.
- `EXECUTION_MODE=paper` and all disarmed execution defaults remain unchanged.

## Before any later trade-impacting proposal
1. Capture true entry-time snapshots of source timestamps, provider identity, interval, indicator inputs, spreads and fees, and avoid using observations formed after the decision timestamp.
2. Verify feature availability and quote freshness; reject cross-book, stale or unverifiable evidence. Deduplicate overlapping labels into independent market episodes and embargo dependent windows.
3. Evaluate matched forward cohorts against unchanged control decisions, including avoided losses and missed winners. Include spread, slippage, fees and partial fills in **actual-dollar** net P/L.
4. Require economically material positive post-cost expectancy in both risk-normalized and dollars, adequate independent samples, out-of-sample confidence bounds and no deterioration in risk/drawdown before considering promotion.
5. Treat experiment approval as separate from execution authority. Never auto-arm live trading.
