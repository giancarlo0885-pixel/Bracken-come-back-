from __future__ import annotations

from oracle_learning_market_scope import learning_market_filter

"""Research-only validation for Oracle Brain and paper challengers.

Measures rejected opportunities, forecast calibration, weak strategy/regime
cohorts, and paper-promotion evidence. Nothing here can approve, size, or
submit an order.
"""

import json
import math
from datetime import datetime, timezone
from typing import Any

COUNTERFACTUAL_HORIZON_MINUTES = 60
COUNTERFACTUAL_MOVE_THRESHOLD_PCT = 0.10
CALIBRATION_MIN_SAMPLES = 20
WEAKNESS_MIN_SAMPLES = 30
PROMOTION_MIN_SAMPLES = 200
PROMOTION_MIN_PROFIT_FACTOR = 1.05
PROMOTION_MAX_CALIBRATION_ERROR = 0.15
PROMOTION_MAX_DRAWDOWN_PCT = 20.0


def _exact_cost_outcome_sql(alias: str = "") -> str:
    """Entry linkage alone does not prove a complete post-cost trade outcome.

    Historical episodes remain in durable storage, but the old exit-ledger
    accounting must not support quantitative profitability or qualification.
    """
    prefix = f"{alias}." if alias else ""
    return (
        f"{prefix}provenance_status='exact'"
        f" AND {prefix}outcome_snapshot->>'accounting_version'='exact_round_trip_v1'"
        f" AND {prefix}outcome_snapshot->>'cost_provenance'='exact_lot'"
        f" AND {prefix}net_pnl > '-Infinity'::double precision"
        f" AND {prefix}net_pnl < 'Infinity'::double precision"
        f" AND {prefix}fees >= 0 AND {prefix}fees < 'Infinity'::double precision"
        f" AND {prefix}entry_time <= {prefix}exit_time AND {prefix}exit_time <= NOW()"
    )


def _chronological_drawdown(rows: list[dict[str, Any]]) -> float | None:
    """Settle simultaneous exits together, without inventing an intra-time order."""
    realized: dict[datetime, list[float]] = {}
    for row in rows:
        timestamp = row.get("exit_time")
        if not isinstance(timestamp, datetime):
            try:
                timestamp = datetime.fromisoformat(str(timestamp).replace("Z", "+00:00"))
            except ValueError:
                return None
        if timestamp.tzinfo is None:
            timestamp = timestamp.replace(tzinfo=timezone.utc)
        pnl = _f(row.get("net_pnl"), float("nan"))
        if not math.isfinite(pnl):
            return None
        realized.setdefault(timestamp.astimezone(timezone.utc), []).append(pnl)
    if not realized:
        return None
    equity = peak = drawdown = 0.0
    for timestamp in sorted(realized):
        equity += math.fsum(realized[timestamp])
        peak = max(peak, equity)
        drawdown = max(drawdown, peak - equity)
    return drawdown


def _f(v: Any, default: float = 0.0) -> float:
    try:
        x = float(v)
    except (TypeError, ValueError):
        return default
    return x if math.isfinite(x) else default


def _obj(v: Any) -> dict[str, Any]:
    if isinstance(v, dict):
        return v
    if isinstance(v, str):
        try:
            parsed = json.loads(v)
            return parsed if isinstance(parsed, dict) else {}
        except Exception:
            return {}
    return {}


def _deep_number(payload: dict[str, Any], keys: tuple[str, ...]) -> float | None:
    stack: list[Any] = [payload]
    while stack:
        item = stack.pop()
        if not isinstance(item, dict):
            continue
        for key in keys:
            if item.get(key) is not None:
                value = _f(item.get(key), float("nan"))
                if math.isfinite(value):
                    return value
        stack.extend(v for v in item.values() if isinstance(v, dict))
    return None


def sync_counterfactuals(conn: Any, *, limit: int = 250, market: str | None = None) -> dict[str, int]:
    """Evaluate mature rejected/abstained decisions using later persisted prices."""
    scope, scope_params = learning_market_filter(market, "d.market")
    decisions = list(conn.execute(
        f"""SELECT id,market,symbol,payload,created_at
           FROM oracle_decision_audit d
           WHERE approved=FALSE
             AND created_at::timestamptz <= NOW()-(%s * INTERVAL '1 minute')
             AND NOT EXISTS (
                 SELECT 1 FROM oracle_counterfactual_outcomes c WHERE c.decision_id=d.id
             )
           {scope}
           ORDER BY created_at ASC LIMIT %s""",
        (COUNTERFACTUAL_HORIZON_MINUTES, *scope_params, max(1, int(limit))),
    ).fetchall())
    counts = {"evaluated": 0, "avoided_losses": 0, "missed_winners": 0, "flat": 0, "skipped": 0}
    for decision in decisions:
        payload = _obj(decision.get("payload"))
        entry_price = _deep_number(payload, ("price", "current_price", "entry_price", "mark_price"))
        if entry_price is None or entry_price <= 0:
            prior = conn.execute(
                """SELECT id,price FROM signals
                   WHERE market=%s AND symbol=%s AND created_at::timestamptz <= %s::timestamptz
                   ORDER BY created_at::timestamptz DESC LIMIT 1""",
                (decision.get("market"), decision.get("symbol"), decision.get("created_at")),
            ).fetchone() or {}
            entry_price = _f(prior.get("price"))
        if entry_price <= 0:
            counts["skipped"] += 1
            continue
        future = conn.execute(
            """SELECT id,price,created_at FROM signals
               WHERE market=%s AND symbol=%s
                 AND created_at::timestamptz >= %s::timestamptz + (%s * INTERVAL '1 minute')
               ORDER BY created_at::timestamptz ASC LIMIT 1""",
            (decision.get("market"), decision.get("symbol"), decision.get("created_at"), COUNTERFACTUAL_HORIZON_MINUTES),
        ).fetchone() or {}
        horizon_price = _f(future.get("price"))
        if horizon_price <= 0 or not future.get("id"):
            counts["skipped"] += 1
            continue
        ret = ((horizon_price / entry_price) - 1.0) * 100.0
        if ret >= COUNTERFACTUAL_MOVE_THRESHOLD_PCT:
            outcome = "missed_winner"
            counts["missed_winners"] += 1
        elif ret <= -COUNTERFACTUAL_MOVE_THRESHOLD_PCT:
            outcome = "avoided_loss"
            counts["avoided_losses"] += 1
        else:
            outcome = "flat"
            counts["flat"] += 1
        conn.execute(
            """INSERT INTO oracle_counterfactual_outcomes(
                   decision_id,market,symbol,decision_time,horizon_minutes,entry_price,
                   horizon_price,return_pct,outcome_class,source_signal_id,execution_impact
               ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,'NONE')
               ON CONFLICT(decision_id) DO NOTHING""",
            (decision.get("id"),decision.get("market"),decision.get("symbol"),decision.get("created_at"),
             COUNTERFACTUAL_HORIZON_MINUTES,entry_price,horizon_price,ret,outcome,future.get("id")),
        )
        counts["evaluated"] += 1
    return counts


def _calibration_buckets(rows: list[dict[str, Any]]) -> dict[tuple[str, float], list[tuple[float, float | None, float]]]:
    buckets: dict[tuple[str, float], list[tuple[float, float | None, float]]] = {}
    for row in rows:
        features = _obj(row.get("feature_snapshot"))
        probability = _deep_number(features, ("probability_of_profit","probability","win_probability"))
        edge = _deep_number(features, ("expected_edge_pct","expected_value_pct","net_expected_value_pct","edge_pct"))
        realized_return = _f(row.get("return_pct"), float("nan"))
        if probability is None or not math.isfinite(realized_return):
            continue
        if 1.0 < probability <= 100.0:
            probability /= 100.0
        if probability < 0 or probability > 1:
            continue
        floor = min(0.9, math.floor(probability * 10.0) / 10.0)
        buckets.setdefault((str(row.get("market") or "unknown"), floor), []).append(
            (probability, edge, realized_return)
        )
    return buckets


def refresh_calibration(conn: Any, *, market: str | None = None) -> dict[str, int]:
    """Compare entry-time probability/edge with complete post-cost outcomes."""
    scope, scope_params = learning_market_filter(market, "market")
    rows = list(conn.execute(
        f"""SELECT market,feature_snapshot,return_pct
           FROM oracle_brain_episodes
           WHERE {_exact_cost_outcome_sql()} AND return_pct IS NOT NULL{scope}""", scope_params
    ).fetchall())
    buckets = _calibration_buckets(rows)
    written = 0
    for (market, floor), values in buckets.items():
        n = len(values)
        predicted = sum(x[0] for x in values) / n
        win_rate = sum(1 for x in values if x[2] > 0) / n
        edges = [x[1] for x in values if x[1] is not None]
        avg_edge = sum(edges) / len(edges) if edges else None
        avg_return = sum(x[2] for x in values) / n
        conn.execute(
            """INSERT INTO oracle_calibration_buckets(
                   market,probability_floor,probability_ceiling,samples,predicted_probability,
                   realized_win_rate,calibration_error,avg_expected_edge_pct,
                   avg_realized_return_pct,updated_at,execution_impact
               ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,NOW(),'NONE')
               ON CONFLICT(market,probability_floor,probability_ceiling) DO UPDATE SET
                   samples=EXCLUDED.samples,predicted_probability=EXCLUDED.predicted_probability,
                   realized_win_rate=EXCLUDED.realized_win_rate,calibration_error=EXCLUDED.calibration_error,
                   avg_expected_edge_pct=EXCLUDED.avg_expected_edge_pct,
                   avg_realized_return_pct=EXCLUDED.avg_realized_return_pct,
                   updated_at=NOW()""",
            (market,floor,min(1.0,floor+0.1),n,predicted,win_rate,abs(predicted-win_rate),avg_edge,avg_return),
        )
        written += 1
    return {"buckets": written, "episodes_considered": len(rows)}


def refresh_weaknesses(conn: Any, *, market: str | None = None) -> dict[str, int]:
    """Detect mature negative strategy/regime combinations without changing gates."""
    scope, scope_params = learning_market_filter(market, "market")
    rows = list(conn.execute(
        f"""SELECT market,strategy,regime,COUNT(*)::int AS samples,
                  AVG(net_pnl) AS expectancy,
                  SUM(CASE WHEN net_pnl>0 THEN net_pnl ELSE 0 END) AS gross_win,
                  ABS(SUM(CASE WHEN net_pnl<0 THEN net_pnl ELSE 0 END)) AS gross_loss,
                  AVG(mfe_pct) FILTER (WHERE mfe_pct IS NOT NULL) AS avg_mfe,
                  AVG(mae_pct) FILTER (WHERE mae_pct IS NOT NULL) AS avg_mae
           FROM oracle_brain_episodes
           WHERE {_exact_cost_outcome_sql()}{scope}
           GROUP BY market,strategy,regime""", scope_params
    ).fetchall())
    negative = 0
    for row in rows:
        samples = int(row.get("samples") or 0)
        loss = _f(row.get("gross_loss"))
        pf = (_f(row.get("gross_win")) / loss) if loss > 0 else None
        expectancy = _f(row.get("expectancy"))
        state = "negative" if samples >= WEAKNESS_MIN_SAMPLES and (expectancy <= 0 or (pf is not None and pf <= 1.0)) else "insufficient_or_nonnegative"
        if state == "negative":
            negative += 1
        key = f"{row.get('market')}:{row.get('strategy')}:{row.get('regime')}"
        conn.execute(
            """INSERT INTO oracle_validation_weaknesses(
                   weakness_key,market,strategy,regime,samples,expectancy,profit_factor,
                   avg_mfe_pct,avg_mae_pct,state,updated_at,execution_impact
               ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,NOW(),'NONE')
               ON CONFLICT(weakness_key) DO UPDATE SET
                   samples=EXCLUDED.samples,expectancy=EXCLUDED.expectancy,
                   profit_factor=EXCLUDED.profit_factor,avg_mfe_pct=EXCLUDED.avg_mfe_pct,
                   avg_mae_pct=EXCLUDED.avg_mae_pct,state=EXCLUDED.state,updated_at=NOW()""",
            (key,row.get("market"),row.get("strategy"),row.get("regime"),samples,expectancy,pf,
             row.get("avg_mfe"),row.get("avg_mae"),state),
        )
    return {"cohorts": len(rows), "negative_mature": negative}


def evaluate_promotion_evidence(conn: Any, market: str) -> dict[str, Any]:
    """Research recommendation only; never promotes or changes execution behavior."""
    stats = conn.execute(
        f"""SELECT COUNT(*)::int AS samples,AVG(net_pnl) AS expectancy,
                  SUM(CASE WHEN net_pnl>0 THEN net_pnl ELSE 0 END) AS gross_win,
                  ABS(SUM(CASE WHEN net_pnl<0 THEN net_pnl ELSE 0 END)) AS gross_loss
           FROM oracle_brain_episodes WHERE {_exact_cost_outcome_sql()} AND market=%s""",
        (market,),
    ).fetchone() or {}
    samples = int(stats.get("samples") or 0)
    loss = _f(stats.get("gross_loss"))
    pf = (_f(stats.get("gross_win")) / loss) if loss > 0 else None
    # A legacy calibration cache may still contain exit-only-cost outcomes.
    # Recompute from canonical eligible episodes before making any claim.
    calibration_rows = list(conn.execute(
        f"""SELECT market,feature_snapshot,return_pct FROM oracle_brain_episodes
           WHERE {_exact_cost_outcome_sql()} AND market=%s AND return_pct IS NOT NULL""",
        (market,),
    ).fetchall())
    mature = [values for values in _calibration_buckets(calibration_rows).values()
              if len(values) >= CALIBRATION_MIN_SAMPLES]
    calibration_samples = sum(len(values) for values in mature)
    calibration_error = (
        sum(abs(sum(x[0] for x in values) - sum(x[2] > 0 for x in values))
            for values in mature) / calibration_samples
        if calibration_samples else None
    )
    pnl_rows = list(conn.execute(
        f"""SELECT exit_time,net_pnl FROM oracle_brain_episodes
           WHERE {_exact_cost_outcome_sql()} AND market=%s
           ORDER BY exit_time ASC,episode_key ASC""",
        (market,),
    ).fetchall())
    max_drawdown = _chronological_drawdown(pnl_rows)
    gross_profit = max(_f(stats.get("gross_win")), 0.0)
    max_drawdown_pct = (max_drawdown / gross_profit * 100.0) if gross_profit > 0 and max_drawdown is not None else None
    expectancy = _f(stats.get("expectancy"), float("nan"))
    expectancy = expectancy if samples and math.isfinite(expectancy) else None
    reasons = []
    if samples < PROMOTION_MIN_SAMPLES: reasons.append("insufficient_exact_samples")
    if expectancy is None: reasons.append("post_cost_expectancy_unavailable")
    elif expectancy <= 0: reasons.append("nonpositive_expectancy")
    if pf is None: reasons.append("profit_factor_unavailable")
    elif pf < PROMOTION_MIN_PROFIT_FACTOR: reasons.append("profit_factor_below_floor")
    if calibration_error is None: reasons.append("calibration_unavailable")
    elif calibration_error > PROMOTION_MAX_CALIBRATION_ERROR: reasons.append("calibration_error_above_ceiling")
    if max_drawdown_pct is None: reasons.append("drawdown_unavailable")
    elif max_drawdown_pct > PROMOTION_MAX_DRAWDOWN_PCT: reasons.append("drawdown_above_ceiling")
    eligible = not reasons
    result = {
        "candidate_key": f"brain_paper_influence:{market}",
        "market": market,
        "samples": samples,
        "expectancy": expectancy,
        "profit_factor": pf,
        "calibration_error": calibration_error,
        "max_drawdown": max_drawdown_pct,
        "eligible": eligible,
        "reasons": reasons,
        "execution_impact": "NONE",
        "accounting_version": "exact_round_trip_v1",
        "cost_provenance": "exact_lot",
    }
    conn.execute(
        """INSERT INTO oracle_paper_promotion_evidence(
               candidate_key,market,samples,expectancy,profit_factor,calibration_error,
               max_drawdown,eligible,reasons,evaluated_at,execution_impact
           ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb,NOW(),'NONE')
           ON CONFLICT(candidate_key) DO UPDATE SET
               samples=EXCLUDED.samples,expectancy=EXCLUDED.expectancy,
               profit_factor=EXCLUDED.profit_factor,calibration_error=EXCLUDED.calibration_error,
               max_drawdown=EXCLUDED.max_drawdown,eligible=EXCLUDED.eligible,reasons=EXCLUDED.reasons,
               evaluated_at=NOW()""",
        (result["candidate_key"],market,samples,result["expectancy"],pf,calibration_error,max_drawdown_pct,eligible,json.dumps(reasons)),
    )
    return result


def sync_learning_validation(conn: Any, market: str) -> dict[str, Any]:
    learning_market_filter(market)
    if market is None:
        raise ValueError("worker validation requires a market")
    counterfactuals = sync_counterfactuals(conn, market=market)
    calibration = refresh_calibration(conn, market=market)
    weaknesses = refresh_weaknesses(conn, market=market)
    promotion = evaluate_promotion_evidence(conn, market)
    return {
        "status": "ok",
        "market": market,
        "counterfactuals": counterfactuals,
        "calibration": calibration,
        "weaknesses": weaknesses,
        "promotion": promotion,
        "execution_impact": "NONE",
    }


__all__ = ["sync_learning_validation","sync_counterfactuals","refresh_calibration","refresh_weaknesses","evaluate_promotion_evidence"]
