# Skip unchanged materialized setup rewrites

The advanced learner recomputes cohort metrics on every scheduled sync. Previously its setup-attribution upsert always created a new row version, even when no original episode or aggregate metric changed. This caused avoidable MVCC versions and associated write work for a quiet historical dataset.

The upsert now compares every field it updates with PostgreSQL row `IS DISTINCT FROM`, which handles NULL values explicitly. Identical rows retain their values, physical row version and `updated_at`; actual new or corrected outcomes still update exactly as before. Source scans and calculations are unchanged. The existing Brain learning-state `last_sync_at` continues recording successful syncs; cohort timestamps are not advertised as current quotes or fresh execution evidence.

Disposable PostgreSQL regressions failed on the original unconditional upsert and pass after this correction. They verify unchanged physical `xmin`/`ctid` and timestamp across five repeated refreshes, real metric/NULL changes, market isolation and source-episode preservation. No canonical or Brain evidence is deleted, rewritten for presentation, skipped from calculation or removed from retention protection.

At the default 900-second learner cadence, C unchanged cohorts previously caused up to 96*C unnecessary row updates per day. Those tuple rewrites are now zero while the inputs remain unchanged. This is a bounded write-amplification reduction, not a claim of zero SQL, zero lock/WAL overhead, measured Railway dollar savings or improved trading profitability. The observed fixture reduced five redundant cohort rewrites to zero; infrastructure savings require production measurement after a separately authorized release.
