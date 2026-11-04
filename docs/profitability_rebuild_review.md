# Profitability rebuild integration review

Base production:0eed51c35a6a35bcb641179d713ebb0dbf61e26b. This branch combines exactly the five reviewed proposals below; no branch was merged into main and no runtime flag was activated.

- #515 fa1c085878d8b5f726be25aeba5b0f273f69a401: all already executable entry aliases pass confirmation and cooldown guards.
- #516 4634091ec8bca6a241e16b62a6bc19606bbaa4b2: unchanged materialized learning cohorts avoid redundant row versions.
- #517 7f77ff938ee7b3a41905a309bd8a9aee84ed7107: implemented scoring families count once while all strategy cards remain visible.
- #518 7c3c53f385fc7c757eb28746a67cd48e8f51c00b: frozen, cost-aware crypto entry/sizing/checkpoint-exit challenger, using the existing prospective collector.
- #519 1dfbe91d6244b228d656c2fc805c0c0463415ddf: validated fills finalize without quote-expiry repricing; unique simulation-derived fill IDs and the existing primary key prevent replay across transaction isolation levels.

Only CI file-list placement and trailing whitespace were reconciled. Local combined validation:3061Pythonpassed,224skipped;312PostgreSQLpassed. Exact combined-head CI remains required before any release. Each standalone draft has passing exact-head CI; this does not itself approve a release or establish profit.

## Economic evidence and limits

A synthetic +0.8% forecast-target move is rejected against1.6433% modeled current friction and2.0542% buffered requirement; no positive edge is invented. Virtual research sizing is bounded by cash, risk, depth and verified minimums. Selected sizes are depth-priced separately. Checkpoint exits use actual observed300/900-second books, not hypothetical stop fills. Passive, entry-only, exit-only, combined and no-trade results share original entries; all missing/loss/abstention outcomes remain explicit.

An advisor fixture previously changed HOLD to BUY solely through duplicate factor names. The repair leaves both views HOLD. This is a score correction, not a profit measurement. Five unchanged refreshes now avoid all five duplicate tuple updates per cohort; at15minutes,up to96×stablecohortcount updates/day are avoided. Database reads remain; no monthly-dollar saving is asserted. The execution correction eliminates a reproduced post-fill expiry rollback; it does not create a profitable strategy.

The legacy Council/fast crypto policy remains the replacement target because qualified economics are negative and no validated forward edge has appeared. The data does not yet isolate entries versus exits. Neither legacy recovery nor the new challenger qualifies for promotion from the current evidence. Independent episodes, portfolio drawdown, VaR/CVaR, MFE/MAE and confidence remain unavailable for the new research until suitable observations exist; synthetic tests are not economic outcomes.

## Release boundaries

All code is review-only. No new strategy flag, autotrader, broker submission or live trading is enabled. The challenger stays default-off, execution authorityNONE, and its observations are not canonical trades. All original trades/fees/lots, Brain/AEVE evidence and retention protections remain. No destructive SQL, resize or historical reconstruction.

Review and explicit release authorization are still necessary. If a combined release is chosen, validate the exact commit, keep the pilot off initially, verify all service deployments and canonical evidence invariants, and authorize its separate bounded research activation only after checking remaining research capacity/storage. #392/#393 and all unrelated held work remain excluded. Do not remerge included PR deltas or PR#312.
