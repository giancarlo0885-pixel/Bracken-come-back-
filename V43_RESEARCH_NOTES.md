# V43 Selective Crypto Forecasting

Production v42 materially improved the short-horizon crypto model but did not clear Oracle governance: 831 out-of-sample observations produced 50.42% aggregate directional accuracy, 1.87% ECE, approximately +0.02% Brier skill, and clean temporal-leakage checks. BTC individually reached 52.69% directional accuracy and beat the configured baselines, showing that the sign-transition representation recovered useful structure, but the aggregate edge remained too weak.

V43 applies selective prediction rather than forcing a directional forecast into low-information intervals.

## Research basis

- Jung (2026), *A Filtered-Label Calibrated XGBoost Framework with Walk-Forward Validation for Robust Bitcoin Direction Prediction*, uses filtered labels, probability calibration, and strict walk-forward validation; the study emphasizes robustness checks because many apparent Bitcoin edges disappear under stricter testing and realistic costs.
- Bysik & Ślepaczuk (2026), *Machine Learning-Based Bitcoin Trading Under Transaction Costs: Evidence From Walk-Forward Forecasting*, finds naive sign strategies fail after costs while cost-aware forecast filtering can restore performance in selected configurations.
- Recent selective-classification research explicitly allows a model to abstain when confidence is weak, trading prediction coverage for lower selective error.
- 2026 short-horizon cryptocurrency research continues to show that properly purged evaluation and realistic trading costs are necessary because genuine microstructure information can still be too weak to monetize at retail fee levels.

## V43 method

1. Fit the v42 Bayesian sign-transition candidate estimators only on resolved historical labels.
2. Reserve an inner historical validation slice separated from fitting data by an embargo equal to the 15-minute forecast horizon.
3. For each transition estimator, evaluate confidence-coverage levels of 100%, 80%, 65%, 50%, and 40%.
4. Compare probability forecasts against the same accepted subset's climatology using recency-weighted Brier loss.
5. A candidate/coverage combination is eligible only if the inner accepted subset has positive Brier skill and at least 52% directional accuracy.
6. Select the best eligible combination by Brier skill, with accuracy and coverage only as weak tie-breakers.
7. Emit the current forecast only if its probability confidence clears the selected historical threshold; otherwise abstain.
8. Oracle's outer walk-forward evaluation scores only emitted predictions and keeps its existing sample-count, directional-accuracy, Brier-skill, ECE, baseline, regime-count, and temporal-leakage requirements unchanged.

V43 changes no capital limit, risk threshold, quote verification rule, broker validation, or broker-submission setting.
