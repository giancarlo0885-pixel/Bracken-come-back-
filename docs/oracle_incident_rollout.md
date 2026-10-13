# Oracle inventory audit: staging-only rollout contract

This change introduces **pure diagnostic and payload foundations only**.
The production inventory adapter deliberately raises `NotImplementedError` and
is NOT a valid production inventory audit. Do not schedule it or treat an
unimplemented adapter as evidence that inventory is reconciled.

Before deployment:
1. Inspect the actual `positions`, `position_lots`, `trade_ledger` and
   `paper_fills` schema, including partial-close and fee semantics.
2. Implement the fail-closed adapter using a verified, read-only snapshot join.
   A missing table or ambiguous lot match must fail closed, never mean zero.
3. Provision read-only and incident-writer roles without dropping roles or
   revoking shared schema grants; verify negative write-permission tests.
4. Implement the durable outbox and leases with transactional deduplication.
   Never mark delivered before provider acceptance; retries are at-least-once.
5. Baseline legacy anomalies for 48 hours before enabling notifications.
6. Test webhook outages, duplicate workers, recovery, provider 429s and
   database restarts. Use exact-head CI and review before any merge.

Never connect notification code to execution paths. Preserve PAPER-only,
live-disarmed behavior and canonical accounting. No schema migration or
deployment is included in this change.
