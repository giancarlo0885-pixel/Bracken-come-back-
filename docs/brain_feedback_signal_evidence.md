# Brain feedback used in paper-trade candidates

Both stock and crypto workers already install `event_opportunity_runtime`.
Its analysis wrapper calls `outcome_memory_for_signal` and attaches
`brain_outcome_adjustment`/`brain_outcome_context` to the live signal. Opportunity
radar and Oracle intelligence consume the adjustment for bounded ranking. Mature
reconciled positive/negative cohorts can change ranking; immature evidence has
zero influence. Forecast, quote, model, cost, portfolio and risk checks remain
independent. Research forecasts and news are not fill approvals.

A recording defect lost the exact runtime assessment: `OracleSignal.to_dict()`
uses dataclass `asdict`, which excludes attributes attached after construction.
`market_worker._signal_payload` now explicitly copies those two Brain fields
using `signal_feedback_evidence`. The assessment is deep-copied as finite JSON,
bounded to 8KiB, and accompanied by a content hash. Invalid or oversized contexts
are explicitly UNAVAILABLE; absent feedback is not invented. Missing/nonfinite
adjustment stays unknown. Serialization does not recompute or change ranking,
action, score, model, thresholds, allocation, execution settings or old evidence.

Scope is `AVAILABLE_AT_SIGNAL_PERSISTENCE`. This is an ordinary signal-payload
assessment snapshot, not an immutable original Brain state, exact episode-ID
receipt, proof of decision consumption, completed fill, or profitable outcome.
The hash detects content disagreement only when compared with a trusted copy;
it does not make a mutable database record immutable. #392/#393 stay held.

No table/migration or new data fetch is added. Existing signal persistence,
transaction and retention rules apply. A failed signal commit cannot reach the
normal execution candidate handoff. Real local PostgreSQL regression checks the
original production signal writer, a separate read connection after commit and
full rollback on a commit failure. Synthetic tests cannot establish production
receipt/fill existence. Current inability to run direct production SQL remains
an explicit verification limit.

This fixes recording, not a missing integration: Brain influence was already
wired. It does not prove that using it improves trading. Evaluate independent
forward outcomes after costs before promoting a strategy or increasing influence.
