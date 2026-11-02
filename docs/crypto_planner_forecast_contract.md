# Forecast requirements at the crypto planning boundary

Production on November 1 generated BTC/ETH/ADA/AAVE BUY proposals and candidate
allocations that the installed paper-pricing guard rejected with
`ENTRY_FORECAST_LINK_REQUIRED`. The primary cause of the missing forecasts in
sampled runtime logs was selective causal-model abstention; no lost or fabricated
forecast has been established by this correction.

A separate software mismatch was independently reproduced: with the actual Kraken
adapter installed, its final guard returned missing-forecast rejection while the
planner returned `qualified_for_capital=True` for the same signal and quote.
Paper core-learning qualification can make forecasts optional; the selected
Coinbase/Kraken pricing adapter unconditionally requires original signal/forecast
IDs. This mismatch wastes planning work and makes BUY/allocation telemetry appear
closer to completed execution than it is. A proposed allocation is not a fill.

The adapter now declares its existing forecast requirement when installing the
candidate source guard. Planning and final execution share the same presence
contract. The paper-learning qualifier checks it before either bounded/unbounded
qualification or success logging, including when installer order changes. Missing
IDs prevent capital qualification and allocation; they do not create IDs or
forecasts. Quote availability is kept separate from decision-evidence eligibility.

Presence is only a minimum requirement, not proof of a valid persisted forecast or
model approval. Exact forecast/signal linkage, receipt identity/freshness, CRC,
model governance, cost and risk checks remain mandatory downstream. This helper
cannot authorize a fill. Valid forecast-bearing candidates retain their existing
path. Cash and sources without this declared requirement keep existing behavior.
Original signals, quotes, canonical history, Brain/AEVE and retention are unchanged.
No trading flag, threshold, model, provider budget, subscription or market-coverage
ceiling changes. No database migration or additional recurring SQL is introduced.

Tests reproduce the actual installed-adapter mismatch before correction, cover
learning modes and wrapper order, preserve valid/cash/legacy paths, verify no
false qualification success log, and execute the existing optimizer to establish
that missing-forecast candidates receive no allocation or cash reservation.
Synthetic tests are not production fills or evidence of profitability.

This fixes impossible planning proposals. It does not make an abstaining or failed
model qualified, expand the active four-symbol feed, certify all crypto markets,
or establish that any new paper trade executed. Expanding coverage requires real
provider support and a bounded cost/quote-quality review. No safeguards are removed
in order to produce activity. Release requires exact-head CI, safety review and
explicit authorization. #392/#393 and the other held releases remain held.
