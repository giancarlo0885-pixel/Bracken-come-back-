# Paper entry aliases share protective gates

The execution router already accepts BUY, STRONG_BUY, ACCUMULATE and LONG as
entry actions. Two paper safeguards classified only BUY/ACCUMULATE: mature losing
research confirmation and same-symbol loss/re-entry cooldown. STRONG_BUY/LONG
could therefore reach the ordinary BUY route without those safeguards.

`execution_actions.EXECUTABLE_ENTRY_ACTIONS` now names exactly the router's existing
four actions. Routing, research confirmation and churn filtering reuse that set.
This does not add accepted actions or authorize any trade. HOLD, SELL, EXIT, CLOSE
and the advisory spelling STRONG BUY remain outside the execution entry set.
Existing economic thresholds, quote/forecast/model/risk gates and protective exits
are unchanged. This is independent of #464's candidate-identity confirmation fix.

Six regression cases failed on the original code: the two omitted aliases skipped
first/duplicate-observation confirmation, a recent post-cost losing exit cooldown,
and installed churn filtering before downstream processing. Tests also retain the
non-entry boundary. No production fill, profitability change, historical rewrite,
trading-variable change or live activation is established by these tests.
