# Website display integrity correction — 2026-10-20

The live read-only audit reproduced two UI defects: app.py treated any mention of
`net EV` as favorable economics and high execution-score text as current quote
quality, while native metric values had only 1.43:1 text/background contrast.
The stale/unverified-price warning remained visible; no unauthorized fill was
established. Some initial Dashboard/Brain/City captures were incomplete, while
later Dashboard and direct Brain rendering succeeded. There is no diagnosed
persistent outage or verified query-latency root cause in this correction.

The shared display helper now parses only one unambiguous numeric percentage,
keeps zero/negative/missing/ambiguous estimates explicit, and describes positive
modeled EV as recorded analysis rather than realized profit. Missing or blocked
current eligibility cannot turn it into supporting evidence. High execution-score
text cannot assert current liquidity or quote integrity. All three explanation
views pass the current decision record through existing freshness/provenance
checks. Historical setups are labeled recorded; fabricated default model support
and unconditional green checkmarks are removed.

Native metric values/labels and alert text receive explicit readable foregrounds
on the existing dark theme. Dashboard data collection has a visible loading
message; Brain/City snapshot collection has a spinner. These indicators do not
claim faster queries, manufacture empty financial results, change query logic or
cache data as live. Underlying latency remains an operational observation to
measure if it recurs.

Validation:
- 29 new regressions exercise negative/zero/malformed/conflicting EV, missing and
  stale/crossed/reference-only quote eligibility, unknown context, the actual
  decision-card renderer, and loading-before-query order in both visualization
  branches. No application bootstrap, SQL, provider requests or fills occur in
  those synthetic display tests.
- Full local Python suite: 2,532 passed, 108 skipped.
- Existing configured PostgreSQL regression selection: 132 passed on disposable
  local PostgreSQL, not production.
- Local headless Chromium used the actual app CSS and decision-card renderer with
  synthetic fixtures. Twenty metric label/value samples had minimum contrast
  9.327:1; mobile body/viewport widths both 390px. Negative stale BUY stayed WATCH,
  false quote/profit claims were absent, and no Streamlit exception panel appeared.
- Full Python compilation and diff whitespace checks passed. Exact-head CI and
  production rendering must be verified separately; local results do not establish
  deployment or perfect runtime health.

Scope: display only. No strategy/qualification threshold, model approval, broker
submission, quote freshness rule, financial calculation, database migration,
canonical evidence, retention policy or trading setting changes. Required paper
and disarmed controls remain intact. This branch is based directly on production
main and includes none of held PR #392/#393's schema or receipt changes.
