# Post-cost economic diagnosis

This is a research report, not a guaranteed-profit formula, strategy change or
permission to trade. It reuses the existing reconciled snapshot and report row;
it introduces no tables, provider requests or additional database queries.

## The equations

For a fully closed long BUY lot, including all allocated partial exits:

`gross_fill_pnl = sum(exit_quantity * exit_fill_price) - entry_quantity * entry_fill_price`

`net_pnl = gross_fill_pnl - allocated_entry_fees - sum(exit_fees)`

`net_return = net_pnl / allocated_entry_notional`

Fill prices already embed modeled spread, slippage and impact. Deducting those
again would invent losses. Gross fill P/L is not frictionless market alpha.
Explicit fees are recorded simulation costs, not exchange-account verified fees.

Using **all** qualified completed lots, including losses and flat outcomes:

`mean_net_return = P(win) * mean_win_return - P(loss) * mean_loss_magnitude`

`break_even_win_rate_given_nonflat_outcomes = mean_loss / (mean_win + mean_loss)`

The second expression is available only when both wins and losses exist. It is
a descriptive relationship, not a forecast probability or a strategy threshold.
Equal-lot expectancy and `sum(net_pnl)/sum(entry_notional)` are reported separately;
different sizing can make the dollar result negative despite a favorable win rate.

For long spot positions with proportional entry/exit fee rates alone:

`fee_only_break_even_price_return = (1 + entry_fee_rate)/(1 - exit_fee_rate) - 1`

At modeled rates of 0.006 on each side this is approximately **1.20724%**.
It excludes any additional spread/impact relative to the entry decision price.
Missing or invalid fee rates produce no break-even figure. This pure calculation
does not discover an account fee tier, change the current fee model or lower gates.

## Evidence and interpretation

`economic_diagnosis` is attached to the existing FIFO report before its detailed
close list is truncated. It uses the existing winner-research adapter's complete
BUY lots, exact fill/lot links, verified fee allocation and original cohort/time
evidence. It includes losers; selection of a winning cohort is not required.
Adapter and additional cost-identity exclusions are exposed. This is a qualified
subset, not a claim that all historical trades are reconciled or Brain-certified.

Partial exits count as one entry. Their gross P/L, allocated entry fees and exit
fees must be summed just like their net P/L. Missing components remain unavailable.
Original accounting rows are never rewritten.

The report distinguishes nonpositive fill-price economics from positive fill
returns erased by explicit fees. Neither proves a causal entry or exit defect.
That requires a separately predefined, matched-entry forward exit experiment with
equivalent original fills, costs and market episodes. Favorable post-exit extrema
are hindsight, not realizable exit prices.

The retrospective lower bound reuses existing whole-window bootstrap semantics.
Four-hour crypto/day cash windows are dependence proxies, not certified independent
episodes. Positive retrospective results do not replace a frozen discovery manifest,
untouched chronological validation, tail-risk review or multiple-search correction.
The existing frozen winner research is unchanged. No automatic promotion occurs.

Rejected/missed opportunities must be evaluated with their original decision-time
features and valid future observations in a separate sampler. Their hypothetical
outcomes cannot be added to realized trade P/L or inferred from log counts.

The Brain shows the persisted worker diagnosis and its update time. A deployed
page without a new worker report shows pending/unavailable, never synthetic zeros.
Existing provider, accounting, execution, model and live-disarmed gates remain.
