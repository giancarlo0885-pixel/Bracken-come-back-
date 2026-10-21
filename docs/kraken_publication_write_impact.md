# Kraken publication write-impact review — 2026-10-21

Disposition: **HOLD**. The cadence correction in #401 is reproduced and tested,
but this review does not approve a production merge or deployment. #392/#393
remain held independently. No production SQL, configuration, migration,
maintenance, evidence deletion or deployment was performed for this review.

## Read-only production observations

Railway reported the PostgreSQL volume's configured `sizeMB=81920`, with
`DISK_USAGE_GB=53.380022272` at approximately 03:22 UTC. These are the platform's
reported units; no filesystem free-space or per-table/WAL size was directly
queried. The prior 24-hour disk samples ranged from 53.311586304 to
53.380022272 GB. That range is not attributed to this undeployed change and is
not a permanent-evidence growth forecast.

The one-hour average CPU usage was 0.451 CPU against a reported limit of 24;
average memory was 1.468 GB against a reported 24 GB limit. The 24-hour memory
maximum was 5.766 GB. These coarse metrics show no obvious CPU/memory saturation
but do not measure individual query, network, scheduling or commit latency.
The service is configured with a PostgreSQL 18 image; its actual server settings
and version were not queried from production.

The 03:00–03:24 UTC crypto-worker log window contained eight resets: one expired
local-book reset and seven incoming timestamp rejections. Six preceding health
payloads took **12.337–23.221 seconds** between their capture timestamp and Python
log-record creation. This delay occurs before downstream Railway log ingestion.
It spans the synchronous publication path; database execution, network wait,
history-lock wait and worker scheduling are not separately measured. No returned
warning in that window named storage failure, checksum failure or crossed book.

Reconnects rose 61→69, while stale rejections rose 59→70. The stale counter mixes
per-market expirations with rejected incoming items: four expired books plus
seven incoming-item failures account for eleven increments. It is not an outage
count. #401's faster schedule does not itself remove synchronous publication
stalls or justify accepting stale buffered events.

## Disposable local measurements

The benchmark used the actual `STATE_DDL` and `write_state`, with a captured
four-market public health payload. JSON serialization was 5,128 bytes;
`pg_column_size(payload)` was 1,867 bytes. Copied timestamps and update counters
changed between writes; prices and other public snapshot fields were retained.
This is a representative storage workload, not new market/trading evidence.

Separate localhost-only PostgreSQL 16.15 and 18.6 databases ran 2,000-upsert
baseline/proposed bursts and short real-paced five-second/one-second cases.
Burst cases have equal write counts and virtual source-time intervals, so their
latency difference is not a causal cadence comparison. Paced cases have only
three/fifteen observations and do not establish tail-latency percentiles.

| Local server | Case | Writes | p50 / p95 / max milliseconds | LSN WAL bytes/write |
| --- | --- | ---: | --- | ---: |
| 16.15 | Five-second burst | 2,000 | 0.373 / 0.600 / 4.498 | 2,122 |
| 16.15 | One-second burst | 2,000 | 0.328 / 0.502 / 1.376 | 1,969 |
| 16.15 | Five-second paced | 3 | 1.760 / 1.984 / 1.984 | 2,099 |
| 16.15 | One-second paced | 15 | 1.366 / 24.655 / 24.655 | 1,973 |
| 18.6 | Five-second burst | 2,000 | 0.297 / 0.438 / 1.734 | 2,122 |
| 18.6 | One-second burst | 2,000 | 0.314 / 0.395 / 6.114 | 1,969 |
| 18.6 | Five-second paced | 3 | 1.551 / 1.568 / 1.568 | 2,117 |
| 18.6 | One-second paced | 15 | 1.344 / 3.295 / 3.295 | 1,977 |

All ordinary cases retained one logical row and a 32,768-byte relation, with no
measured relation growth. All updates were HOT; estimated dead tuples were 0–2.
No autovacuum or checkpoint cycle was observed in the collected statistics, so
these results do not establish steady-state cleanup or checkpoint cost. Warm
`EXPLAIN (ANALYZE, BUFFERS, WAL)` probes attributed 1,824–2,112 WAL bytes to each
statement, with zero full-page images; commits are excluded from those probes.
LSN deltas include commits and any other cluster activity. Statistics counters
are asynchronous and must not be substituted for those deltas.

A separate synthetic PostgreSQL 18 repeatable-read reader pinned an old snapshot
while 2,000 updates ran. The relation still held one current logical row, but
allocated storage grew **32,768→4,153,344 bytes**, including 4,104,192 bytes of heap;
estimated dead tuples reached 2,000 and HOT updates were 1,500/2,000. Rolling the
reader back did not immediately shrink allocated files. This demonstrates
sensitivity to old snapshots; it is not evidence that production has such a
reader. Only isolated disposable schemas were dropped after these measurements.

The nominal periodic ceiling changes 17,280→86,400 writes/day, plus lifecycle
writes. Applying the measured 1,969–2,122 bytes/write linearly to the additional
69,120 writes yields approximately **130–140 MiB/day of additional generated
WAL**. This is an assumption for review, **not permanent disk growth**. Full-page
images, compression, TOAST, actual indexes, old snapshots, other workloads,
replication/archive retention and vacuum/checkpoint cycles can change results.

## Remaining release evidence

1. Obtain separate bounded read-only production samples of singleton relation
   sizes, estimated dead/HOT tuples, autovacuum history, relevant settings and
   cluster WAL counters, with timestamps and reset times. Compare with actual
   volume usage; `pg_database_size` does not include all volume/WAL consumption.
2. Attribute the observed publication-path delays by phase. A three-second
   PostgreSQL statement timeout does not bound full client-side wall time.
   Fast isolated local writes cannot clear this observed production blocker.
3. Preserve one advisory-lock owner and atomic singleton updates during mixed
   versions. An older owner may retain five-second publication until handoff;
   deployment SHA alone does not establish the new cadence.
4. Preserve the existing two-second reader timeout, three-second writer timeout,
   ten-second reference freshness, two-second venue alignment, CRC32 validation,
   whole-session invalidation/resnapshot and reference-only authority. Do not
   invent a replacement utilization/latency threshold to force approval.
5. Require clean exact-head CI and explicit merge/deployment approval. This
   review does not clear Brain storage/provenance holds or establish profit.

The managed workspace still has no native-TCP destination grants and Railway's
connector exposes no SQL execution tool. A bounded read-only query was prepared
and validated against disposable PostgreSQL 16/18, but not run in production.
No production capacity or latency gate is declared passed.

The configured PostgreSQL integration selection additionally passed on disposable
PostgreSQL 18.6: **132 passed**. The bounded read-only observation query returned
seven result sets and ended idle after rollback on both local PostgreSQL 16 and
18. These checks are separate from production evidence and from exact-head CI.

PostgreSQL documents the distinctions between per-table estimates, cluster-wide
WAL statistics and maintenance counters in its
[statistics reference](https://www.postgresql.org/docs/18/monitoring-stats.html).
Railway volume metrics and configured capacity are platform observations, as
described in its [volume documentation](https://docs.railway.com/volumes/reference).
