# Historical accounting reader precision repair

## Confirmed defects

The original fee-aware FIFO writer can record an opened quantity of `0.3`,
close `0.2` with status `PARTIAL`, then close `0.3 - 0.2` with quantity
`0.09999999999999998` and status `CLOSED`. The position lot is closed by the
writer's finite precision. Decimal reconstruction instead retained `2e-17`.
When another entry was subsequently sold, the read-only FIFO reconciler
consumed that remnant first and falsely rejected the new entry identity.

This reproduces even when all original canonical executions, orders, fills,
lot allocations and fees pass exact provenance validation. Tests using the
production fee-aware close function and atomic fill writer recover three valid
closes instead of two. Later winners, losers and breakeven outcomes are all
included; no profitable subset is selected.

Separately, the atomic writer accepts one-ULP quantity roundoff under its
existing relative quantity contract, while two reader upper bounds used exact
`>` comparisons. A committed allocation of `2.0000000000000004` against an
actual fill of `2` lost provenance despite agreement with the original writer.
Actual PostgreSQL writer/reader regressions reproduce both gains and losses.

These are verified software defects, not proof that they caused the reported
1,248 production identity exclusions or 1,776 unverified fill/fee exclusions.

## Narrow correction

- Derive terminal-close proof only from the validated original SELL ledger's
  `CLOSED` status. Current/future position-lot inventory cannot supply it.
  Clear this marker with all other caller annotations before validation.
- Normalize a reconstructed terminal residual only with exact entry/current
  exit identity, exact prior allocations, original terminal proof and the
  existing relative quantity tolerance. Notional and fee effects must each
  remain within the existing `0.000001` accounting currency tolerance.
- Preserve original matched quantities, prices, fees and P&L. Store the
  negligible residual in derived report diagnostics rather than inventing an
  additional sale or modifying canonical inventory. Handle a negligible excess
  sale remainder symmetrically before it reaches another lot.
- Align quantity upper bounds with the established relative contract, with
  currency and fee limits for both individual and cumulative over-allocation.
  Relative proximity alone cannot certify a material notional/fee discrepancy.
- Publish corrected derived reports under
  `canonical-fifo-exit-research-v4-terminal-precision`. Prior v3 reports remain
  stored. The existing prior-report fallback preserves frozen research
  selections; there is no automatic reranking, promotion or historical fill
  backfill.

Missing links, contradictory IDs, nonterminal or missing original status,
material residual inventory, unknown tiny entries and financially meaningful
quantity differences remain excluded. In particular, a relative quantity
difference worth $100 cannot acquire exact provenance in direct verifier
consumers, even if FIFO is not called.

## Validation

Fail-first evidence includes three pure FIFO and three actual PostgreSQL
terminal-close recoveries, plus four writer/reader quantity-boundary failures.
Rejection controls already passed against the original implementation.

After correction, local Python 3.11 validation passed 3,086 tests with 229
PostgreSQL-only cases skipped; the full configured disposable PostgreSQL
selection passed 317 tests. The focused accounting selection passed 98 tests.
Compilation and whitespace checks passed. Exact-head GitHub Actions results
are recorded in the draft PR.

Thirty new cases cover quantity bounds, terminal precision, rejection controls
and five actual PostgreSQL cases. Tests compare all six canonical source tables
before and after reconciliation. Test prices, original records and resulting
P&L are synthetic and are not production profitability evidence.

## Production disposition and remaining blockers

No original production rows were changed or queried directly during this work.
The connected Railway tools do not expose SQL execution/SSH; connector tunnel
discovery returned unavailable for this account. No database credentials were
requested or displayed. The production recovery count and revised canonical
profitability therefore remain unverified. A summary count cannot establish
which historical rows retain complete source proof.

After an approved release, the existing reconciliation schedule can calculate
the new derived report and disclose residual-normalization counts. Any further
historical recovery must inspect exact original relationships. The October 14
execution-link migration deliberately left legacy fill identities unknown; this
change does not reconstruct them from prices, timestamps or later snapshots.

Draft only: no merge, deployment, migration, schema/index change, mass update,
deletion, retention change, volume resize, ID/capacity reset, strategy/risk
threshold change or execution authorization. #521 and #522 are separate drafts;
#393 remains held and #312 is untouched. Paper-only safeguards remain intact.
No infrastructure expansion or billing change; the $20 target and the unverified
status of actual billing-cap enforcement remain unchanged.
