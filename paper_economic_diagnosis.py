"""Accounting-grounded research diagnostics; never an execution or promotion gate."""
from collections import Counter
from decimal import Decimal, InvalidOperation
import math

from paper_winner_edge_research import prepare, summarize

VERSION = "paper-economic-diagnosis-v1"


def _number(value):
    if value is None or isinstance(value, bool):
        return None
    try:
        result = Decimal(str(value))
        return result if result.is_finite() and math.isfinite(float(result)) else None
    except (InvalidOperation, ValueError, TypeError, OverflowError):
        return None


def fee_only_break_even_return(entry_fee_rate, exit_fee_rate):
    """Long spot price return required by proportional fees alone, as a fraction.

    Unknown fees remain unknown. This excludes spread/impact already embedded
    in fills and is never an account fee lookup or an entry authorization.
    """
    entry, exit_ = _number(entry_fee_rate), _number(exit_fee_rate)
    if entry is None or exit_ is None or entry < 0 or not 0 <= exit_ < 1:
        return None
    result = float((1 + entry) / (1 - exit_) - 1)
    return result if math.isfinite(result) else None


def diagnose_economics(rows, *, market, as_of, coverage=None):
    """Use completed, accounting-qualified entry lots, including every loser.

    The caller supplies the original verified FIFO adapter output. Flags in an
    arbitrary log/JSON are not independently certified by this pure function.
    No new SQL, synthetic market observations, thresholds or sizing changes.
    """
    if market not in {"crypto", "cash"} or as_of.tzinfo is None:
        raise ValueError("An explicit market and timezone-aware as_of are required")
    selected = [r for r in rows if r.get("market") == market]
    valid, exclusions = prepare(selected, as_of)
    exclusions = Counter(exclusions)
    exclusions["other_market"] += len(rows) - len(selected)
    qualified = []
    for row in valid:
        entry_fee, exit_fee, gross, net, notional = (
            _number(row.get(k)) for k in
            ("entry_fees", "exit_fees", "gross_pnl", "net_pnl", "entry_notional"))
        if (any(v is None for v in (entry_fee, exit_fee, gross, net, notional))
                or entry_fee < 0 or exit_fee < 0 or notional <= 0
                or notional + gross <= 0):
            exclusions["missing_or_invalid_cost_components"] += 1
            continue
        fees = entry_fee + exit_fee
        if abs(gross - fees - net) > max(Decimal("0.000001"), abs(net) * Decimal("1e-9")):
            exclusions["gross_fee_net_identity_mismatch"] += 1
            continue
        normalized = [float(v / notional) for v in (gross, fees, net)]
        if not all(math.isfinite(v) for v in normalized):
            exclusions["nonfinite_cost_normalization"] += 1
            continue
        qualified.append({**row, "entry_notional": float(notional), "gross_return": normalized[0],
                          "fee_return": normalized[1], "net_return": normalized[2],
                          "gross_pnl": float(gross), "explicit_fees": float(fees)})
    summary = summarize(qualified)
    n = len(qualified)
    wins = [r["net_return"] for r in qualified if r["net_return"] > 0]
    losses = [-r["net_return"] for r in qualified if r["net_return"] < 0]
    mean_win = math.fsum(wins) / len(wins) if wins else None
    mean_loss = math.fsum(losses) / len(losses) if losses else None
    expectancy = ((len(wins) / n) * (mean_win or 0)
                  - (len(losses) / n) * (mean_loss or 0)) if n else None
    gross = math.fsum(r["gross_pnl"] for r in qualified) if n else None
    fees = math.fsum(r["explicit_fees"] for r in qualified) if n else None
    net = math.fsum(r["net_pnl"] for r in qualified) if n else None
    positive = math.fsum(r["net_pnl"] for r in qualified if r["net_pnl"] > 0)
    negative = -math.fsum(r["net_pnl"] for r in qualified if r["net_pnl"] < 0)
    notional = math.fsum(r["entry_notional"] for r in qualified) if n else None
    status = ("INSUFFICIENT_QUALIFIED_EVIDENCE" if not n else
              "NONPOSITIVE_FILL_PRICE_ECONOMICS" if gross <= 0 else
              "POSITIVE_FILL_PRICE_RETURN_ERASED_BY_FEES" if net <= 0 else
              "POSITIVE_OBSERVED_NET_NOT_PROMOTION_PROOF")
    return {
        "version": VERSION, "as_of": as_of.isoformat(), "market": market,
        "status": status, "input_entry_lots": len(rows), "qualified_entry_lots": n,
        "coverage": coverage, "exclusions": dict(exclusions),
        "accounting": {"gross_fill_price_pnl": gross, "explicit_fees": fees,
                       "net_pnl": net, "allocated_entry_notional": notional,
                       "gross_less_fees_identity": "net = gross_fill_price_pnl - explicit_fees",
                       "aggregate_return_on_allocated_notional": net / notional if n else None},
        "expectancy": {"mean_net_return": summary["mean_net_return"],
                       "win_loss_decomposition": expectancy,
                       "win_rate": len(wins) / n if n else None,
                       "loss_rate": len(losses) / n if n else None,
                       "flat_rate": (n-len(wins)-len(losses)) / n if n else None,
                       "mean_win_return": mean_win, "mean_loss_magnitude": mean_loss,
                       "break_even_win_rate_given_nonflat_outcomes":
                           mean_loss / (mean_win+mean_loss) if wins and losses else None,
                       "profit_factor_usd": positive / negative if negative > 0 else None,
                       "episode_windows": summary["episodes"],
                       "retrospective_lower_bound": summary["score_lower_bound"]},
        "fee_erased_positive_gross_lots": sum(r["gross_pnl"] > 0 and r["net_pnl"] <= 0 for r in qualified),
        "entry_vs_exit_cause": "UNRESOLVED_REQUIRES_FIXED_ENTRY_MATCHED_FORWARD_EXPERIMENT",
        "unexecuted_missed_opportunities": "NOT_MEASURED_BY_COMPLETED_TRADES",
        "full_brain_consumption_and_pricing_certification": "NOT_ESTABLISHED_BY_THIS_REPORT",
        "cost_semantics": "Gross uses actual paper fill prices and already embeds spread/slippage/impact; subtract only explicit fees once. Fee provenance is recorded paper simulation, not account-verified charges.",
        "statistical_semantics": "Equal-lot expectancy differs from notional-weighted return. Whole 4h crypto/day cash entry windows are dependence proxies, not guaranteed independence. Retrospective confidence is not untouched forward validation.",
        "promotion_action": "NONE", "execution_impact": "NONE",
    }
