# Winner Edge research v1

Research-only discovery and forward evaluation. No imports into order execution,
no position sizing, no changes to the existing trading formula or risk gates.

For each exact `(market, entry strategy, entry regime, entry pattern)` cohort:

`r_i = canonical round-trip net P&L / allocated entry notional`

`score = lower one-sided episode-bootstrap confidence bound of mean(r_i)`

This is the cost-aware version of `P(win)*average win - P(loss)*average loss`.
Wins drive discovery, but all verified outcomes enter the expectation. Fees and
slippage must already be included in net P&L; do not subtract them twice.
Discovery alpha is `0.05 / number of searched cohorts`. At least 50 trades and
25 dependence windows plus a positive lower bound are required to mark a
research candidate. These are evidence criteria, not execution thresholds.
Four-hour crypto / daily cash windows are dependence proxies; independence is
not guaranteed. Closely related partial closes must retain shared time windows.

## Reproducible run

Supply a JSON array with one verified completed round trip per `trade_id`:

```json
[{"trade_id":"example", "market":"crypto", "strategy":"oracle_council_v3",
  "regime":"trend_up__high_vol", "entry_pattern":"resistance_test",
  "entry_time":"2026-10-01T10:00:00Z", "exit_time":"2026-10-01T10:30:00Z",
  "entry_evidence_time":"2026-10-01T09:59:00Z", "entry_notional":100,
  "net_pnl":0.12, "fifo_complete":true, "fill_fee_evidence_verified":true,
  "entry_cohort_verified":true}]
```

The example is synthetic schema documentation, not Oracle performance.
`entry_cohort_verified` requires immutable entry snapshots, including strategy,
regime and pattern. An exit-time label cannot be substituted. Fee verification
requires canonical fills and FIFO reconciliation. Missing facts remain missing.
Raw runtime logs and existing bounded report details do not satisfy this input
contract. A full verified export/adapter is still needed for production history.

```bash
python paper_winner_edge_research.py verified_round_trips.json \
  --cutoff 2026-10-01T00:00:00Z --as-of 2026-10-09T17:00:00Z
```

Choose and freeze the cutoff before looking at validation outcomes. The training
set contains only trades closed before cutoff. Split-straddling episode windows
are purged. Later completed trades cannot alter discovery scores or selected
cohorts. Report includes baseline/selected economics, rejected winners, avoided
losses, and exclusions. No candidate is automatically promoted.

This analyzes executed, completed trades only. It cannot measure opportunities
that Oracle never traded or future results that have not occurred. Such signals
need a separate verified forward sampler. Promotion needs untouched chronological
validation, drawdown/tail-risk review, multiple conditions and exact-head CI.
