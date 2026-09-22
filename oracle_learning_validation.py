from __future__ import annotations

"""Research-only validation for Oracle Brain and paper challengers.

Measures rejected opportunities, forecast calibration, weak strategy/regime
cohorts, and paper-promotion evidence. Nothing here can approve, size, or
submit an order.
"""

import json
import math
from typing import Any

COUNTERFACTUAL_HORIZON_MINUTES = 60
COUNTERFACTUAL_MOVE_THRESHOLD_PCT = 0.10
CALIBRATION_MIN_SAMPLES = 20
WEAKNESS_MIN_SAMPLES = 30
PROMOTION_MIN_SAMPLES = 200
PROMOTION_MIN_PROFIT_FACTOR = 1.05
PROMOTION_MAX_CALIBRATION_ERROR = 0.15


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


def sync_counterfactuals(conn: Any, *, limit: int = 250) -> dict[str, int]:
    """Evaluate mature rejected/abstained decisions using later persisted prices."""
    decisions = list(conn.execute(
        """SELECT id,market,symbol,payload,created_at
           FROM oracle_decision_audit d
           WHERE approved=FALSE
             AND created_at::timestamptz <= NOW()-(%s * INTERVAL '1 minute')
             AND NOT EXISTS (
                 SELECT 1 FROM oracle_counterfactual_outcomes c WHERE c.decision_id=d.id
             )
           ORDER BY created_at ASC LIMIT %s""",
        (COUNTERFACTUAL_HORIZON_MINUTES, max(1, int(limit))),
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


def refresh_calibration(conn: Any) -> dict[str, int]:
    """Compare entry-time probability/edge with exact realized paper outcomes."""
    rows = list(conn.execute(
        """SELECT market,feature_snapshot,return_pct
           FROM oracle_brain_episodes
           WHERE provenance_status='exact' AND return_pct IS NOT NULL"""
    ).fetchall())
    buckets: dict[tuple[str, float], list[tuple[float, float | None, float]]] = {}
    for row in rows:
        features = _obj(row.get("feature_snapshot"))
        probability = _deep_number(features, ("probability_of_profit","probability","win_probability"))
        edge = _deep_number(features, ("expected_edge_pct","expected_value_pct","net_expected_value_pct","edge_pct"))
        if probability is None:
            continue
        if 1.0 < probability <= 100.0:
            probability /= 100.0
        if probability < 0 or probability > 1:
            continue
        floor = min(0.9, math.floor(probability * 10.0) / 10.0)
        buckets.setdefault((str(row.get("market") or "unknown"), floor), []).append(
            (probability, edge, _f(row.get("return_pct")))
        )
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


def refresh_weaknesses(conn: Any) -> dict[str, int]:
    """Detect mature negative strategy/regime combinations without changing gates."""
    rows = list(conn.execute(
        """SELECT market,strategy,regime,COUNT(*)::int AS samples,
                  AVG(net_pnl) AS expectancy,
                  SUM(CASE WHEN net_pnl>0 THEN net_pnl ELSE 0 END) AS gross_win,
                  ABS(SUM(CASE WHEN net_pnl<0 THEN net_pnl ELSE 0 END)) AS gross_loss,
                  AVG(mfe_pct) FILTER (WHERE mfe_pct IS NOT NULL) AS avg_mfe,
                  AVG(mae_pct) FILTER (WHERE mae_pct IS NOT NULL) AS avg_mae
           FROM oracle_brain_episodes
           WHERE provenance_status='exact'
           GROUP BY market,strategy,regime"""
    ).fetchall())
    negative = 0
    for row in rows:
        samples = int(row.get("samples") or 0)
        loss = _f(row.get("gross_loss"))
        pf = (_f(row.get("gross_win")) / loss) if loss > 0 else (999.0 if _f(row.get("gross_win")) > 0 else 0.0)
        expectancy = _f(row.get("expectancy"))
        state = "negative" if samples >= WEAKNESS_MIN_SAMPLES and (expectancy < 0 or pf <= 1.0) else "insufficient_or_nonnegative"
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
        """SELECT COUNT(*)::int AS samples,AVG(net_pnl) AS expectancy,
                  SUM(CASE WHEN net_pnl>0 THEN net_pnl ELSE 0 END) AS gross_win,
                  ABS(SUM(CASE WHEN net_pnl<0 THEN net_pnl ELSE 0 END)) AS gross_loss
           FROM oracle_brain_episodes WHERE provenance_status='exact' AND market=%s""",
        (market,),
    ).fetchone() or {}
    samples = int(stats.get("samples") or 0)
    loss = _f(stats.get("gross_loss"))
    pf = (_f(stats.get("gross_win")) / loss) if loss > 0 else 0.0
    cal = conn.execute(
        """SELECT SUM(samples*calibration_error)/NULLIF(SUM(samples),0) AS error
           FROM oracle_calibration_buckets WHERE market=%s AND samples >= %s""",
        (market,CALIBRATION_MIN_SAMPLES),
    ).fetchone() or {}
    calibration_error = _f(cal.get("error"), 1.0)
    reasons = []
    if samples < PROMOTION_MIN_SAMPLES: reasons.append("insufficient_exact_samples")
    if _f(stats.get("expectancy")) <= 0: reasons.append("nonpositive_expectancy")
    if pf < PROMOTION_MIN_PROFIT_FACTOR: reasons.append("profit_factor_below_floor")
    if calibration_error > PROMOTION_MAX_CALIBRATION_ERROR: reasons.append("calibration_error_above_ceiling")
    # Drawdown is deliberately not inferred from unordered aggregates.
    reasons.append("forward_drawdown_validation_required")
    eligible = False
    result = {
        "candidate_key": f"brain_paper_influence:{market}",
        "market": market,
        "samples": samples,
        "expectancy": _f(stats.get("expectancy")),
        "profit_factor": pf,
        "calibration_error": calibration_error,
        "max_drawdown": None,
        "eligible": eligible,
        "reasons": reasons,
        "execution_impact": "NONE",
    }
    conn.execute(
        """INSERT INTO oracle_paper_promotion_evidence(
               candidate_key,market,samples,expectancy,profit_factor,calibration_error,
               max_drawdown,eligible,reasons,evaluated_at,execution_impact
           ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb,NOW(),'NONE')
           ON CONFLICT(candidate_key) DO UPDATE SET
               samples=EXCLUDED.samples,expectancy=EXCLUDED.expectancy,
               profit_factor=EXCLUDED.profit_factor,calibration_error=EXCLUDED.calibration_error,
               max_drawdown=EXCLUDED.max_drawdown,eligible=FALSE,reasons=EXCLUDED.reasons,
               evaluated_at=NOW()""",
        (result["candidate_key"],market,samples,result["expectancy"],pf,calibration_error,None,False,json.dumps(reasons)),
    )
    return result


def sync_learning_validation(conn: Any, market: str) -> dict[str, Any]:
    counterfactuals = sync_counterfactuals(conn)
    calibration = refresh_calibration(conn)
    weaknesses = refresh_weaknesses(conn)
    promotion = evaluate_promotion_evidence(conn, market)
    return {
        "status": "ok",
        "counterfactuals": counterfactuals,
        "calibration": calibration,
        "weaknesses": weaknesses,
        "promotion": promotion,
        "execution_impact": "NONE",
    }


__all__ = ["sync_learning_validation","sync_counterfactuals","refresh_calibration","refresh_weaknesses","evaluate_promotion_evidence"]
