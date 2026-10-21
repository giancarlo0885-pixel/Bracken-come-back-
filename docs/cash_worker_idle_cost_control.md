# Cash opportunity scan cost control

Draft #404 remains unreleased. The previous whole-process pause is superseded:
it stopped scheduled GitHub intelligence ingestion, cash Brain synchronization,
valuation and position monitoring along with opportunity scans. Earlier idle-probe
measurements describe that superseded design, not this revision.

The worker now retains its existing bootstrap, installers, intelligence collection,
Brain synchronization, maintenance, valuation and position pulses. Only future fast
and deep opportunity scan launches can be suppressed. Already running work finishes.

`ENABLE_CASH_MARKET_RESEARCH` defaults to true, preserving current behavior.
Only explicit false/0/no/off requests a scan pause. Unknown values preserve scanning.
Crypto scans are unaffected. Before suppressing cash scans, a read-only check must
establish no nonzero or unknown cash positions and no unknown/nonterminal paper
orders. Missing tables, failed reads and ambiguous results preserve monitoring.
The check occurs at scan deadlines, not on every pulse. New holdings or outstanding
orders restore normal scans on the next deadline. This policy never authorizes a
trade; all existing entry/exit and quote controls remain mandatory.

Behavioral tests execute a worker cycle and verify intelligence, Brain and position
pulses remain scheduled with scans suppressed and enabled. Policy tests cover
configuration, inventory ambiguity and read failures. Disposable PostgreSQL tests
use temporary tables to verify real SQL, including unknown and negative positions,
nonterminal orders and crypto isolation. No production rows are changed.

This is a reduction in optional opportunity scanning, not an idle process. No
Railway cost reduction is claimed: bootstrap, threads, scheduled research,
position monitoring and database writes intentionally remain. A controlled rollout
and resource measurement require separate approval. No provider quotas, trading
flags, strategy thresholds, canonical evidence or retention settings are changed.

PRs #392/#393 and #401 retain their independent release holds. Keep this PR draft
until review and exact-head CI are complete. No merge or deployment is authorized.

Local validation, October 21: 2,567 unit tests passed, 116 skipped (including
eight new PostgreSQL-only cases); configured PostgreSQL suite 140 passed.
The policy file additionally passed all 30 cases against disposable PostgreSQL.
Compilation and diff checks passed. Fresh exact-head CI is required after push.
