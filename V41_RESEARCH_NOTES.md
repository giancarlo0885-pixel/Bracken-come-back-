# V41 Crypto Predictive-Power Research Notes

V41 addresses the production result where the v40 causal model passed temporal-leakage checks but remained below the unchanged Oracle predictive-performance gates.

Research-informed design choices:

- Preserve the 15-minute horizon. Recent matched out-of-sample research reports pervasive directional reversal in crypto at approximately 15-minute horizons, while also finding the gross edge can be smaller than ordinary spot round-trip costs. Oracle therefore treats reversal as a predictive expert, not an execution authorization.
- Use heterogeneous experts instead of one fixed polarity. V41 compares reversal, range/volume-conditioned reversal, momentum, regularized logistic, inverted logistic, and climatology candidates.
- Select dynamically with nested historical evidence. Candidate selection uses a recent validation block that is entirely before the outer decision timestamp and purges an embargo equal to the forecast horizon.
- Optimize selector choice on Brier loss rather than raw directional accuracy. This preserves probability calibration discipline and matches Oracle's existing governance metrics.
- Add causal OHLC and volume features. Current research on Bitcoin direction forecasting finds OHLC inputs, particularly intrabar price information, can add information beyond close-only series.
- Remain regime-adaptive without future state information. V41's candidate performance is weighted toward recent resolved history; no contemporaneous future regime labels are used.

Sources reviewed during implementation:

- Kitron & Wengrowicz (2026), "Short-horizon mean reversion in cryptocurrency markets: a matched cross-market measurement," arXiv:2608.21888.
- Sossi-Rojas, Velarde & Zieba (2025), "A Machine Learning Approach For Bitcoin Forecasting," arXiv:2504.18206.
- Saviozzi (2026 revision), "Dynamic Predictor Selection for Financial Time Series Using Contextual Multi-Armed Bandits in a Reinforcement Learning Framework," SSRN 5097520.
- Kim (2026), "Carrying regime uncertainty forward in cryptocurrency tail-risk forecasting," Finance Research Letters / SSRN 6387397.

No Oracle readiness threshold, risk limit, capital limit, quote gate, or broker-submission setting is relaxed by V41.
