# Runtime integrity corrections

This patch corrects runtime observability and small-account paper concurrency without enabling brokerage submission or weakening any decision/risk gate.

- Yahoo Finance paper fallback remains eligible only under the existing paper-reference rules, but it is no longer labeled as provider verified in human-facing metadata/logs.
- Provider verification, paper-reference verification, and execution eligibility are emitted as separate fields.
- Every execution handoff receives a non-empty correlation ID.
- `small-account-paper` is capped at 10 simultaneous positions by default (configurable only through `SMALL_ACCOUNT_MAX_OPEN_POSITIONS`, clamped to 1..20); the legacy six-position expansion is disabled for this paper profile.
- Non-paper runtimes are not modified by the position cap.
- Live trading and broker submission settings are unchanged.
