# Kraken shared reference delivery and source-time diagnostics

Production inspection on 2026-10-20 found two fail-closed `stale_book`
resets within one hour, at 18:56:10Z and 18:59:36Z, alongside
two `WebSocketConnectionClosedException` resets. Checksum failures
were zero. The existing health log did not identify which subscribed
source became stale and why. It is **not** evidence that a particular
asset was faulty or that stale books were traded.

Before the existing `stale_book` exception discards original books,
the elected Kraken listener now logs one compact `KRAKEN STALE BOOK
DIAGNOSTIC` record. It includes each unavailable market's
controlled display symbol, and the ages in seconds of the original
exchange event and local receive timestamps, distinguishing event
time expiry from receipt expiry. It logs no book price/size, raw
provider payload, credential, account identifier, or execution quote.
If metadata is malformed, it reports unknown timing rather than
guessing. Results are diagnostic only, not part of trade decisions.

All prior safeguards and strict staleness thresholds stay in place:
MAX_AGE=10s for Kraken; heartbeats never renew book evidence; any stale
subscribed book still closes the complete WebSocket session,
invalidates its books, and reconnects. The current diagnostic alone
does not justify weakening the freshness threshold, dropping a
lower-volume asset, or relabeling a reference quote as executable.

Validate in a later authorized production rollout by correlating the
reported symbol ages, network interruptions and cross-venue update
coverage. This draft has no effect until separately reviewed and
deployed. It makes no database migrations, trading changes or
historical evidence alterations.

## Shared publication cadence correction

The original listener published its shared singleton every five seconds while
the existing Coinbase comparison requires exchange timestamps within two
seconds. A deterministic session reproduction shows a current internal Kraken
book and current Coinbase book aligning, while the same Kraken reader's older
shared snapshot fails the unchanged comparison. A book being valid under its
ten-second source-age limit does not establish two-second venue alignment.

The elected listener now attempts periodic publication at most once per second,
measured from completion of the previous write. It never catches up by writing
each queued packet after a storage stall. Publisher scheduling uses monotonic
time only; original exchange-event and local-receipt timestamps are not renewed.
The source-age, checksum, crossed-book and two-second alignment limits stay
unchanged. Paper prices still require the separate validated Coinbase adapter
and all its original guards. Kraken retains reference-only, non-executing
authority.

Only the existing elected owner writes the same `id=1` singleton. There are no
new tables, appended tick records, reader connections to Kraken, migrations or
retention changes. The nominal periodic-write ceiling changes from 17,280 to
86,400 per day, plus existing startup/failure/handoff writes. That is up to
69,120 additional updates per day. Singleton row count does not bound physical
WAL, TOAST or dead-tuple storage; production write latency and autovacuum impact
remain unmeasured release gates. No storage-capacity hold on #392/#393 is cleared.

The 30-second health-log interval remains unchanged. The existing timeout and
autocommit settings, advisory-lock ownership and storage-failure invalidation
are preserved. Slow delivery, incoming stale events, quiet symbols and network
interruptions can still make references unavailable. This change cannot
guarantee continuous pricing or establish why every production reset occurred.

## Remaining recovery limitation and validation

A local four-market reproduction also shows that one expired book can reset
the shared session while the other books remain fresh. The existing complete
invalidation/fresh-snapshot policy remains unchanged in this patch. Safely
isolating that recovery requires a separately validated per-symbol resnapshot
protocol; accepting updates without required snapshots or relaxing staleness
is not an alternative. Production cumulative counters alone do not establish
which symbol triggered a reset.

Regression coverage exercises actual fake-wire sessions through shared
publication and reader revalidation, then the unchanged Coinbase alignment
checks. Dense wire traffic, slow storage, stale expiry and reference-only
authority remain covered. These are mocked protocol tests, not production
latency or live-feed validation. Exact-head CI and explicit review/approval
remain required before merging or deploying this draft.
