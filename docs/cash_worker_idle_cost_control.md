# Cash-worker idle review

Draft #404 remains unreleased. `ENABLE_CASH_MARKET_RESEARCH` defaults to false;
this is an explicit cash-worker opt-in, not automatic detection of crypto-only mode.

## Confirmed defect

The original pause guard ran after imports and runtime installation. The paper
regime-economics and shadow-exit installers can initialize database schemas,
sample evidence and start background sampler threads before the guard sleeps.
The earlier structure-only test did not detect that behavior.

Move the guard before active runtime imports and installation. Preserve the
existing opted-in cash runtime. Do not alter trading flags, pricing authority,
strategy thresholds, retention, or canonical evidence.

## Validation (local, October 21, 2026)

- Thirteen behavioral cases execute the entrypoint: eight paused flag variants
  must sleep without importing any active worker/installers; five truthy variants
  must retain installers, readiness, audit and cash-worker execution.
- Against the original draft, all eight paused cases failed; five opted-in cases passed.
- Corrected suite: 2,558 passed, 108 skipped. Configured PostgreSQL suite: 132 passed.
- The first PostgreSQL attempt failed because the existing disposable local test
  container was stopped. Starting only that container restored the suite; no
  production database was accessed or modified.
- A four-second local subprocess observation blocked and counted socket connects
  and DNS lookups: zero attempts, one thread, no active-runtime import, 0.00 CPU
  seconds during observation, RSS stable at 16,412,672 bytes. A harness-only signal
  handler allowed inspection on shutdown; this does not test native signal handling.

These are short local measurements, not Railway cost or long-term workload results.
The sleeping service still consumes baseline process memory and service resources.

## Release holds

Before release, independently verify cash holdings, pending orders, exits,
valuation and other outstanding cash-worker responsibilities can safely pause.
The guard pauses monitoring and learning as well as entry scanning. No fresh
production inventory reconciliation was established in this review. Existing
records are preserved, but preserved data does not imply continued monitoring.

Review the default-false deployment behavior explicitly and measure actual
Railway resource impact. Keep this PR draft until those gates are resolved.
PRs #392/#393 and #401 retain their independent release holds. No merge,
deployment, quota change, database migration or trading-setting change is
part of this review. Execution remains paper-only with live trading disarmed.
