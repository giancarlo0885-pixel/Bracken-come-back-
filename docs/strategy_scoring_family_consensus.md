# Scoring-family consensus integrity

Twenty entries in the strategy registry use the same generic market-composite formula. Only the short-horizon crypto mean-reversion implementation has a distinct calculation. Provider-specific labels and evidence prerequisites do not make repeated uses of the same formula independent votes.

The registry still returns all 21 named cards with their original provider-availability checks. Each implemented formula now carries a scoring-family identifier. Ensemble aggregation gives each family one vote and removes identical repeated score/confidence rows within that family. Separate implementations remain distinct even when their scores happen to match. Legacy/custom signals without the field retain their named-strategy fallback. Generic card text now explicitly identifies a shared composite rather than implying a separate strategy-specific model.

The existing average/dispersion/confidence formula is applied to these family votes; no confidence, risk, model, quote or portfolio threshold changes. New available_strategy_count and scoring_family_count fields distinguish display labels from implemented calculations. A scoring-family count is not independent market observations, calibrated probability, measured alpha or verified trading samples.

Synthetic reproduction: one generic score of 59 with a disagreeing mean-reversion score of 5 produces an ensemble score of 32 and heuristic confidence of 58.4. Adding all 20 names for the same generic calculation previously changed those numbers to 56.43 and 76.08. It now leaves them at 32 and 58.4. In an advisor fixture with quality 70 and otherwise approved synthetic evidence, duplicate labels previously changed HOLD into BUY; after correction the two-card and 21-card views both remain HOLD. These are score/action regressions, not measured P&L improvements.

This module feeds advisor recommendation views. It is not the canonical crypto broker/paper execution guard, and the correction cannot authorize a fill or prove profitability. No historical evidence, provider data, database schema, strategy thresholds, live settings or execution protections change.
