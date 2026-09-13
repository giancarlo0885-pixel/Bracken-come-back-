# Schwager Technical Framework Integration

This integration translates concepts emphasized in Jack D. Schwager's *Getting Started in Technical Analysis* into deterministic Oracle evidence features.

Implemented evidence:

- Trend state and trend strength.
- Trading-range support and resistance.
- Upside/downside breakout detection.
- Failed-breakout detection as explicit contrary evidence.
- RSI/MACD oscillator confirmation.
- ATR/structure-based stop context.
- Range/ATR objective context and reward/risk measurement.
- Durable Market Memory features so paper economics can learn which combinations actually work.

Safety and validation policy:

- The framework does not place orders.
- It does not override Oracle BUY/SELL/HOLD decisions.
- It does not weaken execution, liquidity, quote, forecast, risk, concentration, reserve, margin, drawdown, broker, or governance gates.
- Strong Schwager patterns may label entry provenance for learning, but action and score remain unchanged until paper evidence validates a promotion rule.
- Any future scoring or sizing influence should be introduced only through walk-forward/paper validation and strategy-economics evidence.
