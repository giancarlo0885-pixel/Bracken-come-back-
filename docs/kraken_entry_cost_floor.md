# Kraken final-entry paper cost floor

Current main preserves an optimizer's round-trip estimate through the final
paper entry gate. Production logs on October 31 quoted about 0.20% from
`liquidity_capacity`, but the selected Kraken adapter's original receipts specify
modeled fees of 0.006 (0.60%) per side. Its spread field is also a ratio, whereas
strategy economics consumes percentage points. Source review and genuine synthetic
adapter receipts reproduce the mismatch; five regression cases fail before this
correction. This does not establish that an actual bad entry passed all the other
execution/model/forecast/risk gates, nor explain all historical losses.

For an already supplied Kraken paper-pricing quote, the final guard now calculates
a top-of-book break-even lower bound using `_simulate_fill_explicit_fee`, the same
model used for Kraken fills. For a flat reference market, the gross move needed
at break-even is:

`buy_fill_price * (1 + entry_fee_rate) / (sell_fill_price * (1 - exit_fee_rate)) - 1`

The result is converted from a ratio to percentage points. Fill prices include
current bid/ask spread and the existing modeled adverse slippage/latency once;
fees are separately charged once. No account fee verification is claimed.
Displayed depth can worsen VWAP beyond top-of-book, so this is a lower bound,
not an exact full-depth future cost or a guarantee of exit liquidity. No future
quote is inferred or supplied as execution evidence.

The gate uses the larger of the existing estimate and this bound. It never adds
an explicit total to the bound, lowers a conservative estimate, changes the gross
forecast edge, changes economics multipliers, or edits the source signal/receipt.
A recognized Kraken pricing model with missing/conflicting cost identity fails
closed before missing-edge exploration. Other providers retain their existing
semantics. Optimizer telemetry can still show its original lower planning estimate;
the enforced correction is at final entry, not a rewrite of historical planning.

This helper confers no execution authority: receipt authentication/freshness,
checksums, model approval, original forecast linkage and risk/portfolio controls
remain separate mandatory guards. Missing Brain-consumption proof is not repaired.
#392/#393 stay held. No live orders, canonical history changes, capacity extension,
configuration change, new service or recurring database work is introduced.

Tests cover optimizer/quant/signal/default provenance, separately charged fees,
spread units, preservation of a higher cost, unchanged signal/quote objects,
blocked optimistic entries, ambiguous identity and missing-edge exploration.
The full PostgreSQL suite also exercises existing atomic pricing receipts,
canonical fills, fees and rollback with synthetic data in local disposable DBs.
No profitable forward result or production deployment is established by tests.
