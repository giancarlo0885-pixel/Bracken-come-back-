# Kraken publication phase timing — diagnostics only

This change addresses a confirmed attribution gap: six production health
payloads captured between 03:03 and 03:15 UTC on 2026-10-21 were not emitted as
Python log records until 12.337–23.221 seconds later, near incoming stale-data
resets. The delay precedes downstream Railway ingestion. Existing production
logs cannot distinguish time spent constructing a payload, waiting for the
history lock, calling the database client or emitting the health log.

The separate write-impact review is recorded in
[#401](https://github.com/giancarlo0885-pixel/Bracken-come-back-/pull/401).
That cadence change remains held. This diagnostics-only change is based directly
on main and retains its original five-second periodic publication schedule.
It does not depend on #392/#393 or install their evidence migrations.

## Measurement contract

The elected listener emits a compact `KRAKEN PUBLICATION TIMING` record at most
once per 30 seconds. It measures publication phases using monotonic time:

- Original state capture.
- History-lock acquisition and copying.
- Comparison construction.
- The existing `write_state` call.
- The existing health-log emission, when it occurs.
- Total elapsed publication time.

`phase_ms` uses the controlled keys `state_capture`, `history_lock_wait`,
`history_copy`, `comparisons`, `write_state` and `health_logging`. `total_ms`
covers publication before the timing-log call; `scope` identifies that boundary.
`status` is `SUCCESS` or `FAILED`, and `failure_stage` is a phase label or null.
These are duration/status fields, not quote verification or execution fields.

The record distinguishes completion from failure and identifies the failed phase
using controlled labels. An unexecuted phase remains unavailable rather than
being reported as a measured zero. Diagnostics are best-effort: failure to emit
them must not create a storage failure or replace an existing exception.
An attempted emission consumes the interval even if its log sink fails. A
publication failure inside the interval can therefore lack a new timing record;
the existing failure warnings remain in place. This is sampled attribution, not
a complete trace of every failure.

The write-call duration includes JSON encoding, client/server/network/commit
waits and time the worker is descheduled. It is **not a PostgreSQL server query
duration**. All phase durations include scheduling delays; they cannot by
themselves distinguish CPU work from suspension. The timing log cannot measure
time spent emitting itself. Any local capture timestamp is diagnostic metadata,
never a replacement exchange or receipt timestamp.
Health-log duration remains unavailable when the existing health-log interval
skips emission. Total elapsed time can also include control-flow and scheduling
work between phases; it need not equal the sum of the measured phase durations.

## Boundaries and release

Only logs receive the new timing fields. The persisted singleton payload and
original timestamps remain unchanged, with exactly the same database call and
advisory-lock ownership. No additional database writes, tables, migrations,
provider requests, message histories or connections are introduced. The existing
health log remains available. The new log contains no book prices, quantities,
raw packets, SQL, exception text, credentials or account identifiers.

This change preserves the original five-second cadence, ten-second source-age
limit, two-second venue alignment, two-second reader timeout, three-second
writer statement timeout, checksums, crossed-book rejection, storage-failure
handling and whole-session invalidation/fresh-snapshot recovery. Kraken remains
reference-only and the timing record has no execution authority.

No new configuration is required beyond the existing opted-in paper-only Kraken
listener. New timing logs are bounded by the 30-second interval; normal platform
log retention still applies. They do not enter canonical trading or Brain
evidence, prove decision-time consumption, qualify an old position or establish
profitability.

Mocked regression tests inject independent phase delays, write failures and
diagnostic failures. They verify attribution, bounded logging, unchanged writes
and timestamps, error propagation and stale-data rejection. Local tests cannot
establish which phase is slow in production.

Keep this as a separate draft pending exact-head CI and release review. No merge
or deployment is authorized by adding diagnostics. If separately approved for
deployment, inspect actual timing records before reconsidering #401. Do not
relax freshness, risk or model requirements to hide the observed interruptions.
#392/#393 remain held independently.
