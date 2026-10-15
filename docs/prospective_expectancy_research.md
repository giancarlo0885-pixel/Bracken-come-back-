# Prospective post-cost expectancy research

This protocol records the research objective and evidence boundaries for the
quote/forecast/market-isolation repair. It adds no trading rule, configuration,
model approval or order path. There is no demonstrated universally profitable
formula.

## Objective

For each completed original entry lot, measure:

```text
net_USD = sum(actual allocated exit proceeds - allocated entry cost
              - recorded entry and exit fees)
net_return = net_USD / allocated entry notional
net_R = net_USD / verified initial risk_USD
```

Executed fill prices already incorporate their execution spread/slippage.
Do not deduct those effects a second time. Pre-entry estimated all-in costs are
separate forecast inputs; they cannot replace missing recorded outcome costs.
Missing or nonpositive initial risk makes net_R unavailable.

The empirical expectancy is the average of all qualified outcomes, including
wins, losses and zero outcomes. It is not the average of winners. For research
cohorts the existing score is a lower bound on mean post-cost return from whole
dependence-window bootstrap samples, corrected for the number of cohorts
searched. Positive dollars and positive risk-normalized paired returns are both
necessary for shadow qualification; neither an improved win rate nor more
trades establishes profitability.

## Evidence admitted

Use the exact persisted BUY/fill/lot/partial-SELL graph and immutable entry-time
features. Require full entry closure, reconciled allocated costs, exact identity
and cutoff-valid timestamps. Partial SELLs are members of one original entry,
not independent observations. Preserve missing and conflicting evidence as
unavailable. Do not infer entry patterns, quotes or forecast inputs after exit.

A price-reference agreement is not broker verification. Keep original provider,
paper-reference status, bid/ask availability, timestamp and cost assumptions
separate. Estimated or unverified quotes may remain useful paper observations;
they cannot be relabeled as broker-verified evidence. Invalid numeric values,
crossed books and future timestamps fail their applicable quality gates.

Selective forecast abstention remains an abstention. It cannot be replaced with
a diffusion forecast or a stored entry forecast to fabricate a current expected
edge. A repaired model receives a distinct shadow model version; old failed
Brier/walk-forward evidence remains intact and cannot approve the repaired
version.

## Freeze before measuring

The winner study's 2026-10-11 discovery cutoff remains fixed. The observed
October 15 winner and losing forward trades cannot be used to rediscover
cohorts in that same study.

Corrected crypto-only AEVE provenance and entry-edge challenger versions begin
at their own durable prospective epochs. Their producer requires entry at or
after the epoch; already-open positions do not become new prospective entries.
Pre-entry cohort and loss-streak history is market-scoped and closes strictly
before the candidate entry. Past reports, generations and snapshots remain
archived. New versions change evidence provenance, not formula weights or
qualification thresholds.

The existing entry-edge score remains:

```text
0.35 * positive_prior_MFE
- 0.45 * abs(negative_prior_MAE)
- prior_round_trip_cost_pct
- 0.20 * min(prior_loss_streak, 6) / 6
- 0.35 * prior_bad_regime_indicator
```

Its score gate, minimum samples and positive prior economics checks are
unchanged. It is an unproven shadow hypothesis. The AEVE generation config also
remains unchanged. Neither score is described as an optimal formula.

For any later candidate family, publish its input fields, parameters, discovery
period, untouched chronological holdout, dependence grouping, boundary purge
and cost stress rule before observing that holdout. Version and archive any
subsequent revision; do not reset or reuse a consumed holdout.

## Decision standard

The existing winner screen requires at least 50 qualified trades and 25
dependence windows before computing a candidate lower bound. Four-hour crypto
and daily cash windows are dependence proxies, not certified independent market
episodes. Adequate calendar coverage, overlap/correlation review and separate
chronological validation are still required.

Compare the frozen candidate and factual baseline using genuine, matched
observations. Require positive post-cost dollar and risk-normalized results,
appropriate uncertainty, sufficient independent episodes, chronological
drawdown/tail-risk and excursion coverage, and untouched future validation.
Without matched observations, paired delta R, avoided loss, VaR/CVaR and
MFE/MAE comparisons remain unavailable.

Promotion remains NONE; execution impact remains NONE. Failed governance,
quote quality, absent expected edge, insufficient samples or negative net
expectancy cannot be bypassed to obtain a desired result. All paper-only and
broker/live safety controls remain unchanged.

