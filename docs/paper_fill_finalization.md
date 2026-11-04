# Finalize the validated fill without repricing it

The adapter validates current quotes at simulation and again at the existing final execution guard. Canonical persistence then previously called the same live simulation function a second time. If database work lasted beyond the five-second quote lifetime, an already accepted simulated fill rolled back solely because time elapsed during accounting.

The adapter now stores the frozen SimulatedFill and original quote alongside price/VWAP in its existing bounded 128-entry execution receipt cache. The canonical writer retrieves and validates that exact receipt instead of issuing a second trading calculation. It checks original quote identity, derived prices, side, order value, reference, fee model and quantity bound. Missing, altered or evicted receipts fail closed. No quote timestamp is renewed; book disconnection does not rewrite an earlier fill.

This accounting-only callback cannot simulate a new trade or authorize an entry. Initial simulation and final execution still use fresh quote/session checks. Model approval, exact forecast linkage, risk, capital and duplicate-execution gates remain unchanged. Trade, lot, fee and pricing-receipt writes remain one PostgreSQL transaction. No schema or historical accounting changes.

Regression tests use actual disposable PostgreSQL: a fresh validated BUY or SELL survives delayed accounting, while a stale packet cannot generate a second fill. Tampering and larger-than-simulated quantities roll back every row; concurrent receipt keys retain their own prices/costs; failure after evidence insertion still rolls back atomically. This fixes execution reliability, not strategy expectancy or profitable evidence.
