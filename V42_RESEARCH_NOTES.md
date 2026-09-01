# V42 Crypto Sign-Transition Model

V42 is a response to production evidence from v41: the nested selector improved calibration materially but remained at 50.0% aggregate directional accuracy with negative Brier skill. Temporal leakage remained clean.

The key research observation used by V42 is that the measured short-horizon cryptocurrency mean-reversion effect is concentrated in **direction/sign transitions**, not in forecasting move magnitude. V42 therefore removes hand-weighted magnitude features from the core probability model and estimates whether the next 15-minute return is up conditional on the sign of the preceding 15-minute return.

## Model structure

- Input required for the predictive core: Close only.
- Horizon: 3 x 5-minute bars = 15 minutes.
- Labels: next-horizon up/down sign.
- State: prior-horizon up/down sign.
- Candidate estimators:
  - climatology/base-rate probability,
  - conditional sign-transition probability over long, recent, and short windows,
  - symmetric reversal/continuation probability over long, recent, and short windows.
- Probabilities use Bayesian shrinkage to avoid extreme estimates from small regime samples.
- Model-window selection uses an inner historical validation block and recency-weighted Brier loss.
- An embargo equal to the forecast horizon separates the inner fitting and validation sets.
- If no transition estimator has positive inner Brier skill versus climatology, V42 returns the historical base-rate probability instead of forcing a directional edge.
- The outer Oracle walk-forward and temporal future-mutation probe remain unchanged.

## Governance

V42 does not lower or bypass any Oracle threshold. Existing minimum directional accuracy, Brier skill, ECE, baseline-beating, sample-count, regime-count, temporal-leakage, quote, liquidity, sizing, portfolio, reserve, drawdown, and broker-validation gates remain authoritative.

Primary research basis reviewed during this upgrade:

- Kitron & Wengrowicz (2026), *Short-horizon mean reversion in cryptocurrency markets: a matched cross-market measurement*, arXiv:2608.21888. The study reports pervasive crypto reversal at approximately 15-minute horizons and finds the effect is largely directional/sign-based rather than magnitude-based.
- Sossi-Rojas, Velarde & Zieba (2025), *A Machine Learning Approach For Bitcoin Forecasting*, arXiv:2504.18206, reviewed for feature-design context.
- Saviozzi (2026 revision), *Dynamic Predictor Selection for Financial Time Series Using Contextual Multi-Armed Bandits in a Reinforcement Learning Framework*, reviewed for dynamic predictor-selection principles.
