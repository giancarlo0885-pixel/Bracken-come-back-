# Evidence efficiency and prediction-report integrity — 2026-11-24

## Verified production context (read-only)

Main and the three application deployments are at
`8e68852b63f8d074102ef1563dc3978c559c8aa7` (#520). Railway reports
nine registered services, five running replicas, no pending changes or recent
failures. This review did not restart them. Four auxiliary services showing
"online" have zero running replicas.

The 21:25 UTC crypto log reports 15,000 original Kraken evidence rows, 15,000
matched Brain receipts and no unmatched receipts. The bounded source-ID limit
is full. New primary research persistence correctly stops before provider/model
work. This is an application evidence limit, not proof of exhausted disk.
PostgreSQL's recent metric reports approximately 53.80 GB disk usage. These
observations do not independently reconcile canonical trades or certify
decision-time Brain consumption.

The prospective pilot reports 764 distinct observations, 544 unavailable
endpoints, and 368 matched entries across two UTC-day dependence proxies.
The matched passive-900-second control totals approximately -$140.8828 after
modeled costs, with PF zero. Entry-only, combined and no-trade policies report
zero modeled P&L. Those aggregates do not establish selected-trade counts,
positive expectancy or independent market episodes. They are research
simulations, not canonical fills or actual realized profits. Missing endpoints
remain missing; no prices or historical links were reconstructed.

## Corrections

Brain receipt totals and the displayed sample previously came from three
separate READ COMMITTED statements. A real disposable-PostgreSQL regression
commits a new original and its matching Brain source between statements. The
old reader reports an unmatched original even though both originals are
correctly matched. One statement now computes expected/matched totals and
the latest ten matched samples from a single snapshot. Every existing full
JSON, source-table, evidence-ID, source-ref and authority predicate remains.
Only IDs and match flags are materialized; sample display fields are read for
the final ten originals. Capacity and footprint are separate observations and
are not represented as sharing the receipt snapshot.

The matched challenger report previously admitted contradictory evidence:
a frozen selected entry paired with NO_TRADE endpoints, or an abstention
paired with trading outcomes. Such pairs are excluded. New counters distinguish
matched selected entries, abstentions, modeled trades and no-trade opportunities.
Breakeven selected trades still count as trades. Per-opportunity expectancy
continues to include legitimate abstentions; it is never relabeled as
per-trade expectancy. Production incidence of contradictory pairs is unverified.

## Validation and measured limits

- Before correction: one concurrent-ingestion PostgreSQL regression failed;
  seven mismatch/empty-store controls passed. Ten new policy-report regressions
  failed against the original evaluator.
- After correction: 90 focused tests passed; full Python suite 3,071 passed /
  232 skipped; configured disposable PostgreSQL suite 320 passed. Compilation
  and whitespace checks passed. GitHub exact-head results belong in the PR.
- Local PostgreSQL 16 benchmark: 15,000 synthetic original/Brain rows over all
  three bounded evidence tables, with 4 KiB of pseudorandom JSON per original
  to exercise TOAST. Six alternating old/new iterations; first iteration
  discarded, medians over five remaining runs, no concurrent test workload.
- All matched: old 972.99 ms, new 280.02 ms (about 71% lower elapsed time).
  With one missing and one conflicting receipt: old 977.78 ms, new 230.01 ms
  (about 76% lower). Counts and samples match exactly. EXPLAIN ANALYZE reports
  no temporary block writes in either case. This is not a production latency
  measurement, statistical performance guarantee or whole-database benchmark.

## Capacity, prediction quality and the $20 target

No ID reset, cap increase, schema change, migration, deletion, retention change,
historical rewrite, volume resize, paid provider or new service is introduced.
Existing evidence and pending-outcome processing remain intact. #393 and other
held changes are not bundled. No live or paper-entry authorization changes.

The next capacity decision must extract useful quality measurements from the
existing frozen observations, quantify unknown/missed outcomes and preserve
the full loser/abstention cohort before allocating another bounded collection
window. This patch does not restore source slots or start a new pilot epoch.
More records alone cannot establish a profitable edge. Recalibration or a new
strategy requires independent, chronologically separated post-cost validation;
no threshold is relaxed here.

One-hour observed averages: PostgreSQL about 1.20 GB memory / 0.122 CPU;
crypto worker 0.49 GB / 0.208 CPU; web 0.079 GB / 0.0039 CPU; stock idle worker
0.0094 GB / negligible CPU; flow lab 0.109 GB / 0.0008 CPU. This short window
does not establish monthly usage or billing. Removing the already idle stock
worker cannot plausibly produce large memory savings from these measurements.

The $20/month hosting target remains a constraint, not a verified billing cap.
Actual invoices, current workspace credits, Agent charges and external-provider
charges were not audited. No measured dollar saving or $20 bill guarantee is
claimed. Reduce redundant work before buying capacity; assess any proposed
infrastructure change against actual billing and representative query metrics.

Disposition: draft changes only. No merge, deployment, configuration change,
production SQL, strategy promotion or forced trade. Required paper-only,
broker-disabled and live-disarmed safeguards remain unchanged.
