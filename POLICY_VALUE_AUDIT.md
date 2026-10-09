# Policy-value audit (research only)

An agent can earn positive returns by holding assets during rising markets even
when its selection of opportunities adds no value. This offline tool separates
three **equal-event-weighted, modeled** quantities, in basis points:

- **Deployment value:** policy return minus baseline return on identical events.
- **Composition benchmark:** expected deployment value after uniformly reassigning
  the same number of action changes inside each eligible pool.
- **Selection value:** deployment value minus that composition benchmark.

For example, buying half the events in a universe where every event returns
100 bps produces 50 bps of deployment value and 50 bps of composition value, but
zero selection value. This is an illustrative calculation, not Oracle performance.

The method is adapted from [Agent Policy-Value Audit, October 2, 2026](https://arxiv.org/abs/2610.04040).
It supports **fresh, fixed-notional LONG or FLAT decisions over a fixed horizon**.
LONG enters and exits within each independent scoring event; FLAT earns zero.
BUY/HOLD/SELL labels on existing positions cannot be substituted. Returns are
midpoint changes minus explicit modeled fees, spread, slippage and impact costs;
they are not realized execution returns or account P&L.

## Running it

```sh
python -m policy_value_audit events.json --as-of 2026-10-09T19:00:00Z
```

The command reads one bounded JSON file and prints a report with an input SHA-256.
It never connects to PostgreSQL, writes records, starts workers, changes policy,
or submits orders. It is not imported by the trading runtime. Reports always set
`execution_impact=NONE`, `promotion_evidence_ready=false`, and omit statistical
inference (`p_value` and `confidence_interval` are null).

## Required export contract

Top-level fields:

| Field | Required meaning |
|---|---|
| `schema_version` | `oracle-policy-value-events-v1` |
| `specification.universe_ref` | Reference to the full prospectively defined eligible-event manifest, including abstentions and losers |
| `specification.expected_event_count` | Unique event count from that manifest, checked against the supplied export |
| `specification.baseline_version`, `policy_version` | Frozen model/decision-rule identities |
| `specification.pool_definition` | Description of strata defined without future outcomes |
| `specification.specification_at` | Time this universe, horizon and pool design were fixed |
| `specification.horizon_seconds` | Positive integer; all observed endpoint pairs must span exactly this horizon |
| `events` | Complete event list; at most 25,000 input rows and 32 MiB via CLI |

Each event requires:

- `event_id`, `market` (`cash` or `crypto`), `symbol`, `venue`, `pool_id`,
  `episode_id`, and `decision_ref` linking both frozen decisions and their inputs.
- `baseline_action` and `policy_action`: `LONG` or `FLAT`.
- `decision_at`: when both decisions were available. `information_at`: latest
  availability time of information used by either decision. It must not follow
  `decision_at`. `pool_assigned_at` must be between specification and decision.
- `start_quote` and `end_quote`: each contains `market`, `symbol`, `venue`,
  `verified: true`, `source_ref`, `observed_at`, `received_at`, `bid`, and `ask`.
  Identity must match the event, both sides must be finite/positive/uncrossed,
  and observation <= receipt <= report cutoff. The starting observation must
  strictly follow the decision; tied timestamps are excluded because event
  ordering is unknown. Missing endpoints are not interpolated.
- `costs`: `model_ref`, `estimated_at`, and explicit nonnegative finite
  `fees_bps`, `spread_bps`, `slippage_bps`, `impact_bps`. The cost model must be
  available by the decision. Zero costs require explicit input; missing costs
  are never replaced by zero. Estimates should represent the full round trip
  on the same fixed-notional convention. Spread is deducted here, so do not
  substitute ask-to-bid returns or costs already included in fill prices.

All times must include a timezone. No live database adapter is supplied:
completed-trade ledgers alone omit unchosen opportunities and cannot establish
this manifest. The tool checks structure and chronology; it cannot independently
authenticate supplied timestamps, completeness declarations, or provider data.

## Interpretation and limitations

Reassignment pools are partitioned by market, symbol, venue, caller-provided
predecision stratum, and baseline action. Each must contain both changed and
unchanged decisions. Its exact benchmark is
`changed_fraction * mean(switch_payoff)`; no random seed or Monte Carlo sampling
is needed. Aggregate values weight pools by eligible event count, not equally.

Identical duplicate IDs count once. Conflicting IDs are excluded entirely.
Bad chronology, missing evidence and degenerate pools are counted separately.
Coverage is always reported; a result from a selected qualified subset may be
biased. Pool details and example exclusions are bounded to 100 each.

**A positive selection value is descriptive, not proof of skill.** Shared
episodes, overlapping assets, repeated model search, and nonexchangeable event
pools need additional statistical treatment. The audit does not model inventory,
cash competition, position sizing, endogenous impact, or sequential actions that
change future states. It cannot qualify a model for trading, alter Council/AEVE
promotion, or justify enabling live money. Verify an independently frozen event
export and its coverage before drawing any empirical conclusion.

Tests use only synthetic data, including exposure-only gains, adverse selection,
cost-driven reversals, exact enumeration of reassignment possibilities, and
provenance failures. No Oracle profitability result has been produced by adding
this tool.
