# Kraken stale-book source-time diagnostics (non-execution)

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
