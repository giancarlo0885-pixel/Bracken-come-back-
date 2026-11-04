"""Existing execution entry semantics, shared by routing and protective guards.

Membership is only an action classification, never authorization to place a trade.
Do not include HOLD, exit actions or advisory labels the execution router rejects.
"""

EXECUTABLE_ENTRY_ACTIONS = frozenset({'BUY', 'STRONG_BUY', 'ACCUMULATE', 'LONG'})
