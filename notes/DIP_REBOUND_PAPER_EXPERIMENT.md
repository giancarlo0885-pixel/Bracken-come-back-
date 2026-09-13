# Dip/rebound paper experiment v1

The existing short-horizon reversal factor can favor a dip before a recovery is
observable. This experiment measures a separate, confirmed rebound hypothesis
and compares two prospectively defined exit policies on the same entry.

## Indicator and memory corrections

- RSI retains the existing simple rolling calculation. Positive gains with zero
  losses now return 100; losses with no gains return 0; a genuinely flat window
  returns 50. Incomplete or invalid windows remain unknown. No earlier RSI value
  is substituted for the current window.
- Entry pattern v1 captures completed, contiguous, timezone-aware OHLCV bars.
  It records RSI/recovery, dip depth and rebound in ATR units, volume ratio,
  Bollinger position, z-score, context regime, source interval and decision time.
  Momentum horizons are explicitly 5/20 **bars**, not 5/20 calendar days.
- Entry features survive signal JSON, compact execution signals, BUY lots and
  fee-aware FIFO closes. Recovery uses only the exact immutable entry signal ID.
  Existing captured values win over recovered values.
- New memory analogs must match schema, timeframe and regime, contain the
  observed features, and have closed before the current decision bar. Legacy
  or incompatible mixed-lot records cannot supply missing pattern evidence.

## Prospective hypothesis and exits

Use 64 completed 5m/15m candles. The last eight candles must contain a 1.5–5 ATR
dip and a 0.25–1.5 ATR recovery, with a trough 2–4 bars ago, trough RSI <=40,
RSI improvement >=3, two rising closes, a higher low and volume at least its
preceding 20-bar average. A declining pre-dip context is ineligible. These are
research parameters, not estimated probabilities or validated profitable rules.

The fill must remain within 0.75 ATR of the setup, after its decision candle,
and no more than two source intervals later. The stop is 0.25 ATR below the
observed trough; risk may not exceed 3% of the modeled entry. Target is the
lesser of the prior observed high or 2R. Require at least 1.2R reward and a
target move greater than 1.25 times modeled round-trip cost.

Each entry creates two separate research trials with an illustrative $50
notional and identical cost assumptions:

1. `atr_target_trail_v1`: invalidation stop, target, then a 1R trailing exit once
   an observed peak reaches +1R. Maximum holding period is 12 source bars.
2. `stop_time_control_v1`: the same invalidation stop and maximum holding period,
   with no target or profit trail.

Forward fills require Robinhood Crypto quotes with matching requested/provider
identity, valid bid/ask, the realtime capability and age <=60 seconds. Existing
quote eligibility also must pass. Oracle's shared paper-fill model applies
spread, fees, slippage and latency, plus an explicitly modeled 5 bps impact.
Both fills include costs, so return is exit-fill / entry-fill minus one; fees
are not deducted twice. Quote snapshots and fill components are retained.

## Operation and evaluation

`PAPER_DIP_REBOUND_EXPERIMENT=true` is the default, but requires paper autonomous
learning and disables itself if live trading or broker submission is armed.
It writes only `paper_dip_rebound_observations` and `paper_dip_rebound_trials`.
It cannot submit orders, mutate portfolio balances, or promote a strategy.

PostgreSQL locks and unique keys prevent duplicate entries and concurrent runs;
open trials and their observed peaks survive restarts. Up to ten symbols may
have open pairs. Open trials receive fast-scan priority. Stale/missing quotes
leave a trial unresolved until fresh evidence is available. Observations are
retained for seven days/up to 50,000 rows, closed trials for 90 days/up to 10,000
rows. Open trials are never removed by retention.

Worker logs emit `PAPER DIP REBOUND` events and five-minute summaries, including
entry rejection reasons, realized returns and completed paired exit comparisons.
Unresolved trials and unpaired closes cannot be counted as paired exit wins.

`dip_rebound_backtest.replay` supports historical evaluation using decisions on
completed candles, next-open entries and explicit modeled spreads. A candle
touching stop and target is charged the stop first; price gaps receive the worse
stop fill. Replay trails use only peaks from preceding bars, while forward
trials use preceding observed quotes. This sampling difference must be considered
when comparing replay and forward results. Remaining positions are unresolved,
not fabricated end-of-history realized gains.

The initial regression tests use synthetic fixtures to verify behavior, not to
claim a profitable edge. Local historical-data retrieval was unavailable:
Yahoo returned rate limits and the Coinbase request timed out. No historical
performance result is claimed. The deployed forward experiment is the next
source of real market observations; promotion remains NONE until sufficient
independent chronological evidence demonstrates positive returns after costs,
acceptable drawdown and improvement over the matched control across regimes.
