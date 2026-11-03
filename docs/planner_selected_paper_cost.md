# Selected paper pricing costs in planning

A verified Kraken paper candidate previously reached the optimizer with generic liquidity costs (about 0.20% in the observed logs), although final execution applies the selected paper adapter’s larger cost floor. The source adapter’s candidate wrapper now applies the existing final economics provenance function after authenticating its original quote. This preserves higher cost estimates, original gross edge and quote identity; raw reference and rejected quotes gain no authority. Final quote, model, exact forecast, liquidity and risk checks remain required.

Regression: the original code failed two assertions: selected-model minimum was absent and cost-quote linkage was missing. Tests cover selected costs, a higher prior cost, no input mutation and rejected quote isolation. The 1.643% test floor is fixture-specific, not measured production cost or profit.

No flags, schema, provider quota, live order capability or strategy thresholds change. This correction can reject more uneconomic plans; it does not guarantee fills or profit.
