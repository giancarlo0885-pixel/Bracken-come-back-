# Expired public-research cooldown placeholder recovery — DRAFT

## Root cause in repository code

The current mark_provider_cooldown_db can insert a provider-wide ledger
row with daily_budget=0, requests_used=0, remaining_budget=0 and
data_mode=shared_cooldown before the first same-day reservation.
reserve_provider_budget_db later runs INSERT ... ON CONFLICT DO NOTHING
and preserves this marker. Even **after** the cooldown expires, the
normal remaining_budget check rejects every reservation as budget
exhausted. This is a source-code failure mode, not evidence yet that
production Coinbase currently has such a marker. The separate
read-only ledger diagnosis in PR #395 is required to establish that.

## Restricted candidate correction

Under the already-held PostgreSQL FOR UPDATE row lock, only when all
conditions below match, the reservation routine atomically replaces
the marker's zero configured allowance with the preexisting static
budget, then reserves its first request:

- provider is exactly Coinbase Public Research (2,400 per UTC day) or
  Kraken Public Research (1,200 per UTC day);
- capability is __provider__ and caller requests exactly that
  previously configured daily allowance;
- the same-day row has data_mode=shared_cooldown,
  entitlement=configured, daily_budget=0, requests_used=0,
  remaining_budget=0, plus a parseable expired cooldown_until;
- the conditional UPDATE matches exactly one unchanged row. Otherwise
  fail closed, do not make a reservation.

All spent positive-budget rows, partially spent/corrupt rows, other
providers/capabilities, explicitly configured zero allowances, active
cooldowns and failed database writes remain blocked. This does not
alter quoted exchange prices, accepted trading evidence, forecast or
decision logic, broker permissions, protection thresholds, historical
records or the configured public research daily limits.

## Rollout hold

- Verify exact-head unit and PostgreSQL concurrency/integration CI.
- Read the actual production quota row via the read-only diagnostics
  from PR #395 (do not claim cause before evidence).
- Explicit separate merge/deployment authorization is required.
- Do not enable new trading routes, broker submissions or real-money
  trading. Existing PAXG restrictions, crossed-book rejection and
  immutable Brain-accounting holds remain in place.

Changing this row only restores research access after its original
cooldown; it does not prove Coinbase external API availability, feed
freshness, cross-venue alignment, paper-execution validity or positive
post-cost trading expectancy.
