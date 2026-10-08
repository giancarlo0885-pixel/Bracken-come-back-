"""Independent, read-only FIFO reconciliation of persisted paper execution prices.

Fill prices already contain simulated slippage. Only explicit entry/exit fees are
deducted here. Missing fills, timestamps and forward observations stay unknown.
No execution or policy-changing API is used by this module.
"""
from __future__ import annotations

from collections import defaultdict, deque
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
import json
import logging
import math
from typing import Any

log = logging.getLogger("paper-exit-research")
REPORT_VERSION = "canonical-fifo-exit-research-v2-fill-fees"
POST_EXIT_MINUTES = (15, 60, 240)
REPORT_DETAIL_LIMIT = 250
TOLERANCE = Decimal("0.000001")


def timestamp(value: Any) -> datetime | None:
    try:
        result = value if isinstance(value, datetime) else datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return result.astimezone(timezone.utc) if result.tzinfo is not None else None
    except (TypeError, ValueError, OverflowError):
        return None


def number(value: Any) -> Decimal | None:
    try:
        result = Decimal(str(value))
        return result if result.is_finite() else None
    except (InvalidOperation, ValueError, TypeError):
        return None


def observed_path(samples: list[dict[str, Any]], start: datetime, end: datetime,
                  reference: float, as_of: datetime) -> dict[str, Any]:
    valid = []
    for sample in samples:
        observed = timestamp(sample.get("observed_at"))
        price = number(sample.get("price"))
        if observed is not None and start < observed <= min(end, as_of) and price is not None and price > 0:
            valid.append((observed, float(price)))
    if not valid or reference <= 0:
        return {"status": "unavailable", "samples": 0, "mfe_pct": None, "mae_pct": None}
    returns = [(price / reference - 1) * 100 for _, price in valid]
    return {"status": "observed", "samples": len(valid),
            "horizon_elapsed": as_of >= end,
            "endpoint_observed": any(end - timedelta(minutes=1) <= t <= end for t, _ in valid),
            "first_observed_at": min(t for t, _ in valid).isoformat(),
            "last_observed_at": max(t for t, _ in valid).isoformat(),
            "mfe_pct": max(0.0, max(returns)), "mae_pct": min(0.0, min(returns))}


def signal_price_samples(rows: list[dict[str, Any]], as_of: datetime) -> dict[tuple[str, str], list[dict[str, Any]]]:
    """Recover observed post-exit prices from independently continuing scans."""
    result = defaultdict(list)
    for row in rows:
        payload = row.get("details") or {}
        if isinstance(payload, str):
            try:
                payload = json.loads(payload)
            except ValueError:
                continue
        if not isinstance(payload, dict):
            continue
        route = payload.get("market_data_route") or {}
        quote = {**(route if isinstance(route, dict) else {}), **payload}
        observed = timestamp(quote.get("quote_timestamp") or quote.get("source_quote_timestamp"))
        symbol = str(row.get("symbol") or "").upper()
        requested = str(quote.get("requested_symbol") or quote.get("symbol") or "").upper()
        price = number(row.get("price"))
        if (quote.get("quote_verified") is not True or not symbol or requested != symbol
                or observed is None or observed > as_of or price is None or price <= 0):
            continue
        result[(row["market"], symbol)].append({"observed_at": observed, "price": float(price),
                                               "source": "persisted_verified_signal_quote"})
    return result


def clustered_expectancy_ci(rows: list[dict[str, Any]]) -> list[float | None]:
    """Resample whole dependence windows, keeping the trade-weighted estimand."""
    import numpy as np
    clusters = defaultdict(list)
    for row in rows:
        entry = datetime.fromisoformat(row["entry_time"])
        seconds = 86400 if row["market"] == "cash" else 14400
        clusters[int(entry.timestamp()) // seconds].append(row["net_pnl"])
    if len(clusters) < 10:
        return [None, None]
    sums = np.array([sum(values) for values in clusters.values()])
    sizes = np.array([len(values) for values in clusters.values()])
    rng = np.random.default_rng(42)
    means = []
    for _ in range(2000):
        indexes = rng.integers(0, len(sums), len(sums))
        means.append(float(sums[indexes].sum() / sizes[indexes].sum()))
    return [float(v) for v in np.quantile(means, [0.025, 0.975])]


def reconcile_fifo(fills: list[dict[str, Any]], metrics: dict[str, dict[str, Any]] | None = None,
                   prices: dict[tuple[str, str], list[dict[str, Any]]] | None = None,
                   *, as_of: datetime | None = None, require_fill_fee_evidence: bool = False) -> dict[str, Any]:
    """Rebuild BUY -> SELL matches in persisted event/id order without mutation."""
    metrics, prices = metrics or {}, prices or {}
    as_of = as_of or datetime.now(timezone.utc)
    unique, tainted, events = {}, set(), []
    diagnostics = defaultdict(int)
    for raw in fills:
        row = dict(raw)
        identity = str(row.get("trade_id") or "")
        key = (str(row.get("market") or ""), str(row.get("symbol") or ""))
        if identity in unique:
            if row == unique[identity]:
                diagnostics["duplicate_fill"] += 1
            else:
                diagnostics["conflicting_fill_identity"] += 1
                tainted.add(key)
                tainted.add((str(unique[identity].get("market") or ""), str(unique[identity].get("symbol") or "")))
            continue
        unique[identity] = row
        side = str(row.get("side") or "").upper()
        event_time = timestamp(row.get("entry_time") if side == "BUY" else row.get("exit_time"))
        price = number(row.get("entry_price") if side == "BUY" else row.get("exit_price"))
        qty, fee = number(row.get("quantity")), number(row.get("fees"))
        if (not identity or not all(key) or side not in {"BUY", "SELL"} or event_time is None
                or price is None or price <= 0 or qty is None or qty <= 0 or fee is None or fee < 0):
            diagnostics["invalid_fill_evidence"] += 1
            tainted.add(key)
            continue
        if event_time > as_of:
            diagnostics["future_fill"] += 1
            continue
        events.append((event_time, int(row.get("id") or 0), identity, key, side, price, qty, fee, row))
    lots = defaultdict(deque)
    closes = []
    for when, _, identity, key, side, price, qty, fee, row in sorted(events, key=lambda item: item[:3]):
        if key in tainted:
            diagnostics["tainted_symbol_fill"] += 1
            continue
        if side == "BUY":
            confirmed_fee = number(row.get("_canonical_fill_fee"))
            lots[key].append(dict(identity=identity, time=when, price=price, opened=qty,
                                  remaining=qty, fee=confirmed_fee if confirmed_fee is not None else fee,
                                  fee_verified=confirmed_fee is not None and abs(confirmed_fee-fee) <= TOLERANCE, row=row))
            continue
        remaining, gross, entry_fees, members = qty, Decimal(0), Decimal(0), []
        identity_matches = True
        entry_fees_verified = True
        while remaining > 0 and lots[key]:
            lot = lots[key][0]
            matched = min(remaining, lot["remaining"])
            gross += (price - lot["price"]) * matched
            entry_fees += lot["fee"] * matched / lot["opened"]
            entry_fees_verified &= lot["fee_verified"]
            declared_price = number(row.get("entry_price"))
            declared_time = timestamp(row.get("entry_time"))
            identity_matches &= declared_price == lot["price"] and declared_time == lot["time"]
            members.append({"buy_trade_id": lot["identity"], "sell_trade_id": identity,
                            "buy_fill_id": lot["row"].get("_canonical_fill_id"),
                            "quantity": float(matched), "entry_time": lot["time"].isoformat()})
            lot["remaining"] -= matched
            remaining -= matched
            if lot["remaining"] == 0:
                lots[key].popleft()
        if remaining > 0:
            diagnostics["unmatched_sell"] += 1
            tainted.add(key)  # Do not qualify later trades after an unknown inventory gap.
        confirmed_exit_fee = number(row.get("_canonical_fill_fee"))
        exit_fee = confirmed_exit_fee if confirmed_exit_fee is not None else fee
        fee_evidence_verified = entry_fees_verified and confirmed_exit_fee is not None
        fee_semantics = ("exit_only" if abs(fee-exit_fee) <= TOLERANCE else
                         "round_trip" if abs(fee-entry_fees-exit_fee) <= TOLERANCE else "unreconciled")
        net = gross - entry_fees - exit_fee if remaining == 0 else None
        declared_gross, declared_net = number(row.get("gross_pnl")), number(row.get("net_pnl"))
        gross_delta = gross - declared_gross if declared_gross is not None and net is not None else None
        # Reconcile the ledger's own declared fee convention separately. Older
        # SELL rows store total fees; verified fills determine actual exit fees.
        exit_net_delta = gross - fee - declared_net if declared_net is not None and net is not None else None
        metric = metrics.get(identity, {})
        metric_net = number(metric.get("round_trip_net_pnl"))
        metric_delta = net - metric_net if net is not None and metric_net is not None else None
        complete = bool(net is not None and identity_matches and gross_delta is not None
                        and abs(gross_delta) <= TOLERANCE and exit_net_delta is not None
                        and abs(exit_net_delta) <= TOLERANCE
                        and (not require_fill_fee_evidence or (fee_evidence_verified and fee_semantics != "unreconciled")))
        if not identity_matches:
            diagnostics["fifo_entry_identity_mismatch"] += 1
        if gross_delta is None or abs(gross_delta) > TOLERANCE:
            diagnostics["gross_accounting_unreconciled"] += 1
        if exit_net_delta is None or abs(exit_net_delta) > TOLERANCE:
            diagnostics["sell_net_accounting_unreconciled"] += 1
        if require_fill_fee_evidence and not fee_evidence_verified:
            diagnostics["unverified_canonical_fill_fees"] += 1
        if fee_evidence_verified and fee_semantics == "unreconciled":
            diagnostics["unreconciled_ledger_fee_semantics"] += 1
        entry = min((timestamp(m["entry_time"]) for m in members), default=None)
        observed = prices.get(key, [])
        closes.append({"trade_id": identity, "ledger_id": int(row.get("id") or 0), "market": key[0], "symbol": key[1],
                       "fifo_complete": complete, "entry_identity_matches": bool(identity_matches),
                       "fill_fee_evidence_verified": bool(fee_evidence_verified),
                       "exit_fill_id": row.get("_canonical_fill_id"),
                       "ledger_fee_semantics": fee_semantics,
                       "unmatched_quantity": float(remaining), "members": members,
                       "entry_time": entry.isoformat() if entry else None, "exit_time": when.isoformat(),
                       "hold_minutes": (when - entry).total_seconds() / 60 if entry else None,
                       "strategy": row.get("strategy") or "unknown", "regime": metric.get("regime") or "unknown",
                       "exit_reason": row.get("order_id") or "unknown",
                       "net_pnl": float(net) if net is not None else None,
                       "entry_fees": float(entry_fees), "exit_fees": float(exit_fee),
                       "gross_difference": float(gross_delta) if gross_delta is not None else None,
                       "exit_net_difference": float(exit_net_delta) if exit_net_delta is not None else None,
                       "round_trip_metric_difference": float(metric_delta) if metric_delta is not None else None,
                       "during_hold": observed_path(observed, entry, when, float(row["entry_price"]), as_of) if entry and number(row.get("entry_price")) else {"status": "unavailable"},
                       "post_exit": {str(minutes): observed_path(observed, when, when + timedelta(minutes=minutes), float(price), as_of)
                                     for minutes in POST_EXIT_MINUTES}})
    eligible = [r for r in closes if r["fifo_complete"]]
    cohorts = defaultdict(list)
    for row in eligible:
        cohorts[(row["strategy"], row["regime"], row["exit_reason"])].append(row)
    summaries = []
    for (strategy, regime, reason), rows in sorted(cohorts.items()):
        pnl = [r["net_pnl"] for r in rows]
        loss = -sum(x for x in pnl if x < 0)
        seconds = 86400 if rows[0]["market"] == "cash" else 14400
        episodes = {int(datetime.fromisoformat(r["entry_time"]).timestamp()) // seconds for r in rows}
        summaries.append({"strategy": strategy, "regime": regime, "exit_reason": reason,
                          "samples": len(rows), "episode_windows": len(episodes),
                          "overlap_disclosure": "Four-hour entry windows cluster trades; economic independence is not certified.",
                          "net_pnl": sum(pnl), "expectancy": sum(pnl) / len(pnl),
                          "profit_factor": sum(x for x in pnl if x > 0) / loss if loss > 0 else None,
                          "clustered_expectancy_ci": clustered_expectancy_ci(rows),
                          "worst_trade": min(pnl)})
    return {"version": REPORT_VERSION, "as_of": as_of.isoformat(),
            "source": "persisted PAPER trade_ledger execution prices; no extra slippage deduction",
            "observed_closes": len(closes), "qualified_fifo_closes": len(eligible),
            "fill_fee_validation_required": require_fill_fee_evidence,
            "net_pnl": sum(r["net_pnl"] for r in eligible),
            "sell_signal_losses": sum(r["exit_reason"] == "sell_signal" and r["net_pnl"] < 0 for r in eligible),
            "metric_discrepancies": sum(r["round_trip_metric_difference"] is not None and abs(r["round_trip_metric_difference"]) > float(TOLERANCE) for r in eligible),
            "diagnostics": dict(diagnostics), "cohorts": summaries, "closes": closes,
            "premature_exit_verdict": "insufficient_data", "execution_impact": "NONE"}


def attach_fill_fee_evidence(ledger: list[dict[str, Any]], paper_fills: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Require a unique persisted fill matching an entire execution's split closes.

    The legacy ledger lacks a paper fill ID. Ambiguous time/price/quantity matches
    remain unverified; a fill may never be reused across separate executions.
    """
    groups = defaultdict(list)
    for raw in ledger:
        row = dict(raw)
        row.pop("_canonical_fill_fee", None)
        event = timestamp(row.get("entry_time") if row["side"] == "BUY" else row.get("exit_time"))
        price = number(row.get("entry_price") if row["side"] == "BUY" else row.get("exit_price"))
        groups[(row["market"], row["symbol"], row["side"], event, price)].append(row)
    fill_index = defaultdict(list)
    for fill in paper_fills:
        price = number(fill.get("fill_price"))
        if price is not None:
            fill_index[(fill.get("market"), fill.get("symbol"), fill.get("side"), price)].append(fill)
    assignments, usage = {}, defaultdict(int)
    for key, rows in groups.items():
        market, symbol, side, event, price = key
        quantities = [number(r.get("quantity")) for r in rows]
        if event is None or price is None or any(q is None or q <= 0 for q in quantities):
            continue
        total = sum(quantities)
        candidates = []
        for fill in fill_index[(market, symbol, side, price)]:
            observed = timestamp(fill.get("created_at"))
            qty, fee = number(fill.get("quantity")), number(fill.get("fee_amount"))
            if (fill.get("fill_id") and observed is not None and 0 <= (observed-event).total_seconds() <= 60
                    and number(fill.get("fill_price")) == price and qty is not None
                    and abs(qty-total) <= max(Decimal("1e-18"), total*Decimal("1e-9"))
                    and fee is not None and fee >= 0):
                candidates.append(fill)
        if len(candidates) == 1:
            assignments[key] = candidates[0]
            usage[candidates[0]["fill_id"]] += 1
    for key, fill in assignments.items():
        if usage[fill["fill_id"]] != 1:
            continue
        total = sum(number(r["quantity"]) for r in groups[key])
        for row in groups[key]:
            row["_canonical_fill_fee"] = str(number(fill["fee_amount"]) * number(row["quantity"]) / total)
            row["_canonical_fill_id"] = fill["fill_id"]
    return [row for group in groups.values() for row in group]


def chronological_oos_report(experiments: list[dict[str, Any]], cutoff: datetime) -> dict[str, Any]:
    """Evaluate frozen-model forward treatment evidence; purge boundary overlaps."""
    from paper_shadow_exit_challenger import BrainCohortAnalyzerV4
    training_episodes = {str(r.get("episode_id")) for r in experiments
                         if timestamp(r.get("exit_time")) is not None and timestamp(r["exit_time"]) <= cutoff}
    valid, exclusions = [], defaultdict(int)
    for raw in experiments:
        row = dict(raw)
        entry, exit_time = timestamp(row.get("entry_time")), timestamp(row.get("exit_time"))
        trigger = row.get("trigger_snapshot") or {}
        if isinstance(trigger, str):
            try:
                trigger = json.loads(trigger)
            except ValueError:
                trigger = {}
        trigger_time = timestamp(trigger.get("challenger_trigger_at")) if isinstance(trigger, dict) else None
        if entry is None or exit_time is None or trigger_time is None or not entry <= trigger_time <= exit_time:
            exclusions["invalid_temporal_evidence"] += 1
        elif entry <= cutoff or str(row.get("episode_id")) in training_episodes:
            exclusions["pre_cutoff_or_overlapping_episode"] += 1
        elif row.get("challenger_exit_type") != "FORWARD_EVIDENCE_EXIT":
            exclusions["no_qualified_treatment"] += 1
        elif (not row.get("episode_id") or number(row.get("actual_realized_r_net")) is None
              or number(row.get("challenger_counterfactual_r_net")) is None):
            exclusions["invalid_outcome_evidence"] += 1
        else:
            valid.append(row)
    return {"cutoff": cutoff.isoformat(), "cutoff_basis": "persisted frozen-model epoch start",
            "exclusions": dict(exclusions),
            **BrainCohortAnalyzerV4(valid).evaluate_portfolio_safety_gate(),
            "avoided_loss_candidates": sum(float(r["actual_realized_r_net"]) < 0 and float(r["challenger_counterfactual_r_net"]) > float(r["actual_realized_r_net"]) for r in valid),
            "harmed_winner_candidates": sum(float(r["actual_realized_r_net"]) > 0 and float(r["challenger_counterfactual_r_net"]) < float(r["actual_realized_r_net"]) for r in valid),
            "promotion_action": "NONE", "execution_impact": "NONE"}


def ensure_reconciliation_schema(conn: Any) -> None:
    conn.execute("""CREATE TABLE IF NOT EXISTS paper_exit_research_reports(
        market TEXT NOT NULL,version TEXT NOT NULL,updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        report JSONB NOT NULL,PRIMARY KEY(market,version))""")


def source_query_diagnostics(conn: Any, market: str, model_version: str) -> dict[str, int]:
    rows = conn.execute("""SELECT reason,COUNT(*)::int AS count FROM (
        SELECT CASE WHEN m.trade_id IS NULL THEN 'missing_regime_metric'
            WHEN m.entry_time IS NULL OR m.exit_time IS NULL THEN 'missing_event_time'
            WHEN e.started_at IS NULL THEN 'missing_model_epoch'
            WHEN m.exit_time < e.started_at THEN 'before_model_epoch'
            WHEN COALESCE(m.cost_provenance,'') <> 'exact_lot' THEN 'unqualified_cost_provenance'
            WHEN m.round_trip_net_pnl IS NULL THEN 'missing_round_trip_pnl'
            ELSE 'eligible_source_close' END AS reason
        FROM trade_ledger l LEFT JOIN paper_regime_trade_metrics m ON m.trade_id=l.trade_id
        LEFT JOIN garibaldi_shadow_exit_epochs e ON e.model_version=%s
        WHERE l.market=%s AND l.side='SELL' AND l.broker_mode='PAPER' AND l.account_environment='PAPER'
        ) source GROUP BY reason""", (model_version, market)).fetchall()
    return {r["reason"]: int(r["count"]) for r in rows}


def json_safe(value: Any) -> Any:
    if isinstance(value, float) and not math.isfinite(value):
        return None
    if isinstance(value, dict):
        return {str(k): json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [json_safe(v) for v in value]
    return value


def emit_reconciliation(market: str) -> dict[str, Any]:
    from database import connect
    from paper_shadow_exit_challenger import active, MODEL_VERSION
    if not active():
        return {"active": False, "execution_impact": "NONE"}
    if market not in {"cash", "crypto"}:
        raise ValueError("invalid market")
    with connect() as conn:
        conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ")
        conn.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", (f"paper_exit_research:{market}",))
        previous = conn.execute("SELECT report FROM paper_exit_research_reports WHERE market=%s AND version=%s",
                                (market, REPORT_VERSION)).fetchone() or {}
        previous_report = previous.get("report") or {}
        if isinstance(previous_report, str):
            previous_report = json.loads(previous_report)
        # Full per-market FIFO history is required. An arbitrary row limit would
        # drop opening lots and manufacture reconciliation failures.
        fills = list(conn.execute("""SELECT id,trade_id,market,symbol,side,quantity,entry_price,
            exit_price,entry_time,exit_time,fees,gross_pnl,net_pnl,strategy,order_id
            FROM trade_ledger WHERE market=%s AND broker_mode='PAPER' AND account_environment='PAPER'
            AND side IN ('BUY','SELL')""", (market,)).fetchall())
        metrics = {r["trade_id"]: dict(r) for r in conn.execute(
            "SELECT trade_id,round_trip_net_pnl,regime FROM paper_regime_trade_metrics WHERE market=%s", (market,)).fetchall()}
        sells = sorted((r for r in fills if r["side"] == "SELL" and timestamp(r.get("entry_time"))),
                       key=lambda r: timestamp(r.get("exit_time")) or datetime.min.replace(tzinfo=timezone.utc))[-REPORT_DETAIL_LIMIT:]
        prices = defaultdict(list)
        if sells:
            samples = list(conn.execute("""SELECT symbol,observed_at,price FROM paper_regime_price_samples
                WHERE market=%s AND observed_at >= %s AND observed_at <= %s
                ORDER BY observed_at DESC,id DESC LIMIT 50001""",
                (market, min(timestamp(r["entry_time"]) for r in sells), datetime.now(timezone.utc))).fetchall())
            for sample in samples[:50000]:
                prices[(market, sample["symbol"])].append(dict(sample))
            signals = list(conn.execute("""SELECT market,symbol,price,details FROM signals
                WHERE market=%s AND NULLIF(created_at,'')::timestamptz >= %s
                  AND NULLIF(created_at,'')::timestamptz <= %s
                ORDER BY id DESC LIMIT 6000""", (market, min(timestamp(r["entry_time"]) for r in sells),
                                                   datetime.now(timezone.utc))).fetchall())
            for key, observations in signal_price_samples([dict(r) for r in signals], datetime.now(timezone.utc)).items():
                prices[key].extend(observations)
        else:
            samples = []
        factual_fills = list(conn.execute("SELECT fill_id,market,symbol,side,quantity,fill_price,fee_amount,created_at FROM paper_fills WHERE market=%s", (market,)).fetchall())
        fee_verified_ledger = attach_fill_fee_evidence([dict(r) for r in fills], [dict(r) for r in factual_fills])
        report = reconcile_fifo(fee_verified_ledger, metrics, prices, require_fill_fee_evidence=True)
        report["snapshot_semantics"] = "repeatable_read"
        watermark = max((int(r["id"]) for r in fills), default=0)
        previous_watermark = previous_report.get("ledger_watermark")
        newly_observed = [r for r in report["closes"] if previous_watermark is not None and r["ledger_id"] > previous_watermark]
        report["ledger_watermark"] = watermark
        report["since_previous_report"] = {"baseline_available": previous_watermark is not None,
            "new_closes": len(newly_observed) if previous_watermark is not None else None,
            "qualified_net_pnl": sum(r["net_pnl"] for r in newly_observed if r["fifo_complete"]) if previous_watermark is not None else None}
        report["price_sample_limit_reached"] = len(samples) > 50000
        report["source_query_diagnostics"] = source_query_diagnostics(conn, market, MODEL_VERSION)
        epoch = conn.execute("SELECT started_at FROM garibaldi_shadow_exit_epochs WHERE model_version=%s", (MODEL_VERSION,)).fetchone() or {}
        experiments = list(conn.execute("""SELECT * FROM garibaldi_shadow_experiments
            WHERE market=%s AND model_version=%s ORDER BY exit_time ASC""", (market, MODEL_VERSION)).fetchall())
        if timestamp(epoch.get("started_at")):
            report["chronological_oos"] = json_safe(chronological_oos_report([dict(r) for r in experiments], timestamp(epoch["started_at"])))
        # Keep one current report and a bounded detailed window per market.
        report["detail_limit"] = REPORT_DETAIL_LIMIT
        report["closes"] = report["closes"][-REPORT_DETAIL_LIMIT:]
        report["path_coverage"] = {str(minutes): {
            "detailed_closes": len(report["closes"]),
            "observed_paths": sum(r["post_exit"][str(minutes)]["status"] == "observed" for r in report["closes"]),
            "observed_endpoints": sum(r["post_exit"][str(minutes)].get("endpoint_observed") is True for r in report["closes"]),
        } for minutes in POST_EXIT_MINUTES}
        report["path_semantics"] = "Observed persisted snapshots only; continuous market coverage and fresh provider ticks are not certified."
        conn.execute("""INSERT INTO paper_exit_research_reports(market,version,report) VALUES (%s,%s,%s::jsonb)
            ON CONFLICT(market,version) DO UPDATE SET report=EXCLUDED.report,updated_at=NOW()""",
            (market, REPORT_VERSION, json.dumps(report, allow_nan=False)))
    log.info("PAPER FIFO RECONCILIATION | market=%s | closes=%s | qualified=%s | net_pnl=%.6f | sell_signal_losses=%s | metric_discrepancies=%s | diagnostics=%s | verdict=INSUFFICIENT_DATA | execution_impact=NONE",
             market, report["observed_closes"], report["qualified_fifo_closes"], report["net_pnl"],
             report["sell_signal_losses"], report["metric_discrepancies"], json.dumps(report["diagnostics"], sort_keys=True))
    log.info("SHADOW EXIT SOURCE COVERAGE | market=%s | exclusions=%s | new_since_baseline=%s | execution_impact=NONE",
             market, json.dumps(report["source_query_diagnostics"], sort_keys=True), json.dumps(report["since_previous_report"], sort_keys=True))
    return report
