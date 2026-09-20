from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta, timezone
import hashlib
import json
import math
import os
import random
import re
import statistics
import time
from typing import Any, Iterable


V3_VERSION = "brain-learning-v3"
COUNTERFACTUAL_HORIZONS_MINUTES = tuple(
    sorted({
        max(15, int(value.strip()))
        for value in os.getenv("ORACLE_BRAIN_COUNTERFACTUAL_HORIZONS", "60,240,1440").split(",")
        if value.strip()
    })
)
COUNTERFACTUAL_MOVE_PCT = max(0.05, float(os.getenv("ORACLE_BRAIN_COUNTERFACTUAL_MOVE_PCT", "0.50")))
DRIFT_Z_THRESHOLD = max(1.0, float(os.getenv("ORACLE_BRAIN_DRIFT_Z_THRESHOLD", "2.0")))
SIMILARITY_MIN = min(0.95, max(0.30, float(os.getenv("ORACLE_BRAIN_SIMILARITY_MIN", "0.58"))))
SYNC_BUDGET_SECONDS = max(20.0, float(os.getenv("ORACLE_BRAIN_SYNC_BUDGET_SECONDS", "75")))
EXPERIMENT_TARGET_SAMPLES = max(15, int(os.getenv("ORACLE_BRAIN_EXPERIMENT_TARGET_SAMPLES", "30")))


def _num(value: Any, default: float = 0.0) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return number if math.isfinite(number) else default


def _json_obj(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return dict(value)
    if isinstance(value, str) and value.strip():
        try:
            decoded = json.loads(value)
        except Exception:
            return {}
        return dict(decoded) if isinstance(decoded, dict) else {}
    return {}


def _dt(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str) and value.strip():
        try:
            parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
        except ValueError:
            return None
    else:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _slug(value: Any) -> str:
    text = re.sub(r"[^a-z0-9]+", "-", str(value or "").strip().lower()).strip("-")
    return text or "unknown"


def _digest(*parts: Any, length: int = 24) -> str:
    raw = "|".join(str(part or "") for part in parts)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:length]


def _canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True, default=str)


def feature_schema_hash(features: Any) -> str:
    data = _json_obj(features)
    keys = sorted(str(key) for key in data.keys())
    return hashlib.sha256(("|".join(keys)).encode("utf-8")).hexdigest()[:24]


def feature_value_hash(features: Any) -> str:
    return hashlib.sha256(_canonical_json(_json_obj(features)).encode("utf-8")).hexdigest()[:24]


def _as_percent_points(value: Any) -> float:
    number = _num(value, 0.0)
    return number * 100.0 if abs(number) <= 1.0 else number


def wilson_interval(wins: int, total: int, z: float = 1.959963984540054) -> tuple[float | None, float | None]:
    if total <= 0:
        return None, None
    p = wins / total
    z2 = z * z
    denom = 1.0 + z2 / total
    center = (p + z2 / (2.0 * total)) / denom
    spread = z * math.sqrt((p * (1.0 - p) / total) + z2 / (4.0 * total * total)) / denom
    return max(0.0, center - spread), min(1.0, center + spread)


def deterministic_bootstrap_interval(
    values: Iterable[float],
    *,
    confidence: float = 0.95,
    reps: int = 400,
    seed_text: str = "oracle-brain",
) -> tuple[float | None, float | None]:
    clean = [float(value) for value in values if math.isfinite(float(value))]
    if not clean:
        return None, None
    if len(clean) == 1:
        return clean[0], clean[0]
    rng = random.Random(int(hashlib.sha256(seed_text.encode("utf-8")).hexdigest()[:16], 16))
    means: list[float] = []
    n = len(clean)
    for _ in range(max(100, int(reps))):
        means.append(sum(clean[rng.randrange(n)] for _ in range(n)) / n)
    means.sort()
    alpha = max(0.001, min(0.20, 1.0 - confidence))
    lo_i = max(0, min(len(means) - 1, int((alpha / 2.0) * len(means))))
    hi_i = max(0, min(len(means) - 1, int((1.0 - alpha / 2.0) * len(means)) - 1))
    return means[lo_i], means[hi_i]


def statistical_summary(values: Iterable[float], *, seed_text: str) -> dict[str, Any]:
    clean = [float(value) for value in values if math.isfinite(float(value))]
    n = len(clean)
    if not clean:
        return {
            "samples": 0,
            "mean": None,
            "median": None,
            "win_rate": None,
            "expectancy_ci_low": None,
            "expectancy_ci_high": None,
            "win_rate_ci_low": None,
            "win_rate_ci_high": None,
            "robust_polarity": "insufficient",
        }
    wins = sum(1 for value in clean if value > 0)
    mean = statistics.fmean(clean)
    median = statistics.median(clean)
    lo, hi = deterministic_bootstrap_interval(clean, seed_text=seed_text)
    win_lo, win_hi = wilson_interval(wins, n)
    if n < 20:
        robust = "insufficient"
    elif lo is not None and lo > 0:
        robust = "positive"
    elif hi is not None and hi < 0:
        robust = "negative"
    else:
        robust = "uncertain"
    return {
        "samples": n,
        "mean": round(mean, 8),
        "median": round(median, 8),
        "win_rate": round(wins / n, 6),
        "expectancy_ci_low": None if lo is None else round(lo, 8),
        "expectancy_ci_high": None if hi is None else round(hi, 8),
        "win_rate_ci_low": None if win_lo is None else round(win_lo, 6),
        "win_rate_ci_high": None if win_hi is None else round(win_hi, 6),
        "robust_polarity": robust,
    }


def _normalize_title_tokens(title: Any) -> set[str]:
    tokens = re.findall(r"[a-z0-9]{3,}", str(title or "").lower())
    stop = {
        "the","and","for","with","from","that","this","into","after","before","about",
        "market","markets","stock","stocks","crypto","update","latest","says","said",
    }
    return {token for token in tokens if token not in stop}


def _jaccard(a: set[str], b: set[str]) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / max(1, len(a | b))


def begin_learning_run(market: str) -> str:
    run_key = f"run:{_slug(market)}:{_digest(datetime.now(timezone.utc).isoformat(), time.monotonic_ns(), length=20)}"
    from database import connect

    with connect() as conn:
        conn.execute(
            """
            INSERT INTO oracle_brain_learning_runs(run_key,market,stage,status,details,execution_impact)
            VALUES (%s,%s,'starting','running',%s::jsonb,'NONE')
            """,
            (run_key, market, json.dumps({"version": V3_VERSION})),
        )
    return run_key


def record_learning_stage(
    run_key: str,
    market: str,
    stage: str,
    *,
    status: str = "running",
    details: dict[str, Any] | None = None,
) -> None:
    from database import connect

    try:
        with connect() as conn:
            conn.execute(
                """
                INSERT INTO oracle_brain_learning_runs(run_key,market,stage,status,details,execution_impact)
                VALUES (%s,%s,%s,%s,%s::jsonb,'NONE')
                ON CONFLICT (run_key) DO UPDATE SET
                    stage=EXCLUDED.stage,
                    status=EXCLUDED.status,
                    details=oracle_brain_learning_runs.details || EXCLUDED.details
                """,
                (run_key, market, stage, status, json.dumps(details or {})),
            )
    except Exception:
        return


def finish_learning_run(
    run_key: str,
    market: str,
    *,
    status: str,
    elapsed_ms: int,
    details: dict[str, Any] | None = None,
) -> None:
    from database import connect

    try:
        with connect() as conn:
            conn.execute(
                """
                UPDATE oracle_brain_learning_runs
                SET stage='complete',status=%s,finished_at=NOW(),elapsed_ms=%s,
                    details=details || %s::jsonb
                WHERE run_key=%s AND market=%s
                """,
                (status, int(elapsed_ms), json.dumps(details or {}), run_key, market),
            )
    except Exception:
        return


def _cluster_unassigned_sources(conn: Any, *, limit: int = 160) -> tuple[int, int]:
    rows = list(
        conn.execute(
            """
            SELECT source_key,provider,category,symbol,title,observed_at,source_quality
            FROM oracle_brain_sources
            WHERE cluster_key IS NULL
            ORDER BY observed_at DESC NULLS LAST,ingested_at DESC
            LIMIT %s
            """,
            (max(1, int(limit)),),
        ).fetchall()
    )
    if not rows:
        return 0, 0

    existing = list(
        conn.execute(
            """
            SELECT cluster_key,canonical_title,symbol,category,providers,source_count,
                   provider_count,first_observed_at,last_observed_at
            FROM oracle_brain_source_clusters
            ORDER BY last_observed_at DESC NULLS LAST
            LIMIT 400
            """
        ).fetchall()
    )
    cluster_cache: list[dict[str, Any]] = [dict(row) for row in existing]
    touched: set[str] = set()

    for row in rows:
        title = str(row.get("title") or "")
        tokens = _normalize_title_tokens(title)
        symbol = str(row.get("symbol") or "").upper().strip() or None
        category = str(row.get("category") or "unknown")
        provider = str(row.get("provider") or "unknown")
        observed = _dt(row.get("observed_at")) or datetime.now(timezone.utc)

        best: dict[str, Any] | None = None
        best_score = 0.0
        for candidate in cluster_cache:
            if symbol and candidate.get("symbol") and str(candidate.get("symbol")).upper() != symbol:
                continue
            if category and candidate.get("category") and str(candidate.get("category")) != category:
                continue
            candidate_time = _dt(candidate.get("last_observed_at"))
            if candidate_time and abs((observed - candidate_time).total_seconds()) > 3 * 86400:
                continue
            score = _jaccard(tokens, _normalize_title_tokens(candidate.get("canonical_title")))
            if score > best_score:
                best_score = score
                best = candidate

        if best is not None and best_score >= 0.68:
            cluster_key = str(best["cluster_key"])
        else:
            fingerprint = "-".join(sorted(tokens)[:12]) or _slug(title)[:80]
            cluster_key = f"news:{_digest(symbol, category, fingerprint, observed.date().isoformat())}"
            best = {
                "cluster_key": cluster_key,
                "canonical_title": title,
                "symbol": symbol,
                "category": category,
                "providers": [],
                "source_count": 0,
                "provider_count": 0,
                "first_observed_at": observed,
                "last_observed_at": observed,
            }
            cluster_cache.append(best)

        providers = set(best.get("providers") or [])
        providers.add(provider)
        best["providers"] = sorted(providers)
        best["source_count"] = int(best.get("source_count") or 0) + 1
        best["provider_count"] = len(providers)
        best["last_observed_at"] = max(_dt(best.get("last_observed_at")) or observed, observed)
        best["first_observed_at"] = min(_dt(best.get("first_observed_at")) or observed, observed)
        touched.add(cluster_key)

        conn.execute(
            "UPDATE oracle_brain_sources SET cluster_key=%s WHERE source_key=%s",
            (cluster_key, row["source_key"]),
        )

    cluster_map = {item["cluster_key"]: item for item in cluster_cache if item["cluster_key"] in touched}
    for cluster in cluster_map.values():
        source_count = max(1, int(cluster.get("source_count") or 1))
        provider_count = max(1, len(set(cluster.get("providers") or [])))
        corroboration = min(1.0, max(0.0, ((provider_count - 1) / 3.0) * 0.7 + ((source_count - 1) / 5.0) * 0.3))
        conn.execute(
            """
            INSERT INTO oracle_brain_source_clusters(
                cluster_key,canonical_title,symbol,category,source_count,provider_count,
                providers,first_observed_at,last_observed_at,corroboration_score,
                metadata,execution_impact
            )
            VALUES (%s,%s,%s,%s,%s,%s,%s::jsonb,%s,%s,%s,%s::jsonb,'NONE')
            ON CONFLICT (cluster_key) DO UPDATE SET
                source_count=GREATEST(oracle_brain_source_clusters.source_count,EXCLUDED.source_count),
                provider_count=GREATEST(oracle_brain_source_clusters.provider_count,EXCLUDED.provider_count),
                providers=EXCLUDED.providers,
                first_observed_at=LEAST(oracle_brain_source_clusters.first_observed_at,EXCLUDED.first_observed_at),
                last_observed_at=GREATEST(oracle_brain_source_clusters.last_observed_at,EXCLUDED.last_observed_at),
                corroboration_score=EXCLUDED.corroboration_score,
                metadata=oracle_brain_source_clusters.metadata || EXCLUDED.metadata
            """,
            (
                cluster["cluster_key"],
                cluster["canonical_title"],
                cluster.get("symbol"),
                cluster.get("category"),
                source_count,
                provider_count,
                json.dumps(sorted(set(cluster.get("providers") or []))),
                cluster.get("first_observed_at"),
                cluster.get("last_observed_at"),
                corroboration,
                json.dumps({"dedupe_method": "title_token_jaccard_v1"}),
            ),
        )

    return len(rows), len(touched)


def _refresh_provider_reputation(conn: Any) -> int:
    providers = list(
        conn.execute(
            """
            SELECT s.provider,
                   AVG(s.source_quality) AS base_quality,
                   COUNT(*)::int AS total_events,
                   COUNT(*) FILTER (
                       WHERE c.provider_count >= 2 AND c.corroboration_score >= 0.25
                   )::int AS corroborated_events
            FROM oracle_brain_sources s
            LEFT JOIN oracle_brain_source_clusters c ON c.cluster_key=s.cluster_key
            WHERE COALESCE(s.provider,'') <> ''
            GROUP BY s.provider
            """
        ).fetchall()
    )
    for row in providers:
        total = max(0, int(row.get("total_events") or 0))
        corroborated = max(0, int(row.get("corroborated_events") or 0))
        base = max(0.0, min(1.0, _num(row.get("base_quality"), 0.5)))
        ratio = corroborated / total if total else 0.0
        reputation = max(0.10, min(0.99, base * 0.82 + ratio * 0.18))
        conn.execute(
            """
            INSERT INTO oracle_brain_provider_reputation(
                provider,base_quality,total_events,corroborated_events,reputation_score,
                updated_at,metadata,execution_impact
            )
            VALUES (%s,%s,%s,%s,%s,NOW(),%s::jsonb,'NONE')
            ON CONFLICT (provider) DO UPDATE SET
                base_quality=EXCLUDED.base_quality,
                total_events=EXCLUDED.total_events,
                corroborated_events=EXCLUDED.corroborated_events,
                reputation_score=EXCLUDED.reputation_score,
                updated_at=NOW(),
                metadata=oracle_brain_provider_reputation.metadata || EXCLUDED.metadata
            """,
            (
                row["provider"],
                base,
                total,
                corroborated,
                reputation,
                json.dumps({"corroboration_ratio": round(ratio, 6)}),
            ),
        )
        conn.execute(
            """
            UPDATE oracle_brain_sources
            SET adaptive_reputation=%s
            WHERE provider=%s
            """,
            (reputation, row["provider"]),
        )
    return len(providers)


def _counterfactual_cursor(conn: Any, market: str) -> int:
    row = conn.execute(
        """
        SELECT last_source_id FROM oracle_brain_learning_state
        WHERE pipeline_key='counterfactual_capture' AND market=%s
        """,
        (market,),
    ).fetchone() or {}
    return int(_num(row.get("last_source_id"), 0))


def _capture_counterfactuals(conn: Any, market: str, *, limit: int = 120) -> int:
    last_id = _counterfactual_cursor(conn, market)
    rows = list(
        conn.execute(
            """
            SELECT id,market,symbol,recommendation,approved,reason,payload,created_at
            FROM oracle_decision_audit
            WHERE market=%s AND id>%s
            ORDER BY id ASC
            LIMIT %s
            """,
            (market, last_id, max(1, int(limit))),
        ).fetchall()
    )
    if not rows:
        return 0

    captured = 0
    max_id = last_id
    for row in rows:
        audit_id = int(row["id"])
        max_id = max(max_id, audit_id)
        payload = _json_obj(row.get("payload"))
        recommendation = str(row.get("recommendation") or "").upper()
        approved = bool(row.get("approved"))
        if approved and recommendation == "BUY":
            continue
        entry_price = _num(payload.get("execution_price") or payload.get("price"), 0.0)
        observed = _dt(row.get("created_at"))
        if entry_price <= 0 or observed is None:
            continue
        features = _json_obj(payload.get("features"))
        quant = _json_obj(payload.get("quant"))
        radar = _json_obj(payload.get("radar"))
        strategy = str(
            payload.get("strategy")
            or radar.get("primary_setup")
            or payload.get("action")
            or "unknown"
        )
        regime = str(
            payload.get("market_regime")
            or payload.get("regime")
            or _json_obj(payload.get("memory")).get("market_regime")
            or "unknown"
        )
        estimated_cost_pct = max(
            0.0,
            _as_percent_points(
                quant.get("estimated_cost_pct")
                or quant.get("estimated_round_trip_cost_pct")
                or payload.get("estimated_cost_pct")
                or 0.0
            ),
        )
        schema_hash = feature_schema_hash(features)
        for horizon in COUNTERFACTUAL_HORIZONS_MINUTES:
            conn.execute(
                """
                INSERT INTO oracle_brain_counterfactuals(
                    decision_audit_id,horizon_minutes,market,symbol,strategy,regime,
                    recommendation,approved,reason,observed_at,entry_price,due_at,
                    estimated_cost_pct,feature_snapshot,feature_schema_hash,
                    decision_payload,execution_impact
                )
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s,%s::jsonb,'NONE')
                ON CONFLICT (decision_audit_id,horizon_minutes) DO NOTHING
                """,
                (
                    audit_id,
                    horizon,
                    market,
                    str(row.get("symbol") or ""),
                    strategy,
                    regime,
                    recommendation,
                    approved,
                    row.get("reason"),
                    observed,
                    entry_price,
                    observed + timedelta(minutes=horizon),
                    estimated_cost_pct,
                    json.dumps(features),
                    schema_hash,
                    json.dumps(payload),
                ),
            )
            captured += 1

    conn.execute(
        """
        INSERT INTO oracle_brain_learning_state(pipeline_key,market,last_source_id,last_sync_at,last_result)
        VALUES ('counterfactual_capture',%s,%s,NOW(),%s::jsonb)
        ON CONFLICT (pipeline_key,market) DO UPDATE SET
            last_source_id=GREATEST(COALESCE(oracle_brain_learning_state.last_source_id,0),EXCLUDED.last_source_id),
            last_sync_at=NOW(),
            last_result=EXCLUDED.last_result
        """,
        (market, max_id, json.dumps({"captured": captured, "last_audit_id": max_id})),
    )
    return captured


def _resolve_counterfactuals(conn: Any, market: str, *, limit: int = 100) -> dict[str, int]:
    rows = list(
        conn.execute(
            """
            SELECT cf.id,cf.symbol,cf.entry_price,cf.estimated_cost_pct,cf.due_at,
                   future.price AS future_price,future.created_at AS future_created_at
            FROM oracle_brain_counterfactuals cf
            LEFT JOIN LATERAL (
                SELECT s.price,s.created_at
                FROM signals s
                WHERE s.market=cf.market AND s.symbol=cf.symbol
                  AND s.created_at::timestamptz >= cf.due_at
                  AND s.created_at::timestamptz <= cf.due_at + INTERVAL '12 hours'
                  AND s.price > 0
                ORDER BY s.created_at::timestamptz ASC
                LIMIT 1
            ) future ON TRUE
            WHERE cf.market=%s AND cf.classification='pending' AND cf.due_at<=NOW()
            ORDER BY cf.due_at ASC
            LIMIT %s
            """,
            (market, max(1, int(limit))),
        ).fetchall()
    )
    counts = {"resolved": 0, "avoided_loss": 0, "missed_winner": 0, "neutral": 0, "unresolved": 0}
    for row in rows:
        future = _num(row.get("future_price"), 0.0)
        entry = _num(row.get("entry_price"), 0.0)
        if future <= 0 or entry <= 0:
            classification = "unresolved"
            gross = None
            net = None
        else:
            gross = ((future / entry) - 1.0) * 100.0
            net = gross - max(0.0, _num(row.get("estimated_cost_pct"), 0.0))
            if net >= COUNTERFACTUAL_MOVE_PCT:
                classification = "missed_winner"
            elif net <= -COUNTERFACTUAL_MOVE_PCT:
                classification = "avoided_loss"
            else:
                classification = "neutral"
        conn.execute(
            """
            UPDATE oracle_brain_counterfactuals
            SET resolved_at=COALESCE(%s::timestamptz,NOW()),
                future_price=%s,gross_return_pct=%s,net_return_pct=%s,
                classification=%s
            WHERE id=%s
            """,
            (
                row.get("future_created_at"),
                future if future > 0 else None,
                gross,
                net,
                classification,
                row["id"],
            ),
        )
        counts[classification] += 1
        counts["resolved"] += 1
    return counts


def _load_cohort_returns(conn: Any, market: str, *, limit: int = 5000) -> dict[tuple[str, str], list[dict[str, Any]]]:
    rows = list(
        conn.execute(
            """
            SELECT strategy,regime,return_pct,net_pnl,exit_time,feature_schema_hash
            FROM oracle_brain_episodes
            WHERE market=%s AND provenance_status='exact'
              AND return_pct IS NOT NULL
            ORDER BY exit_time DESC
            LIMIT %s
            """,
            (market, max(1, int(limit))),
        ).fetchall()
    )
    grouped: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[(str(row.get("strategy") or "unknown"), str(row.get("regime") or "unknown"))].append(dict(row))
    return grouped


def _drift_stat(recent: list[float], baseline: list[float]) -> tuple[float, float, float]:
    recent_mean = statistics.fmean(recent)
    baseline_mean = statistics.fmean(baseline)
    if len(recent) < 2 or len(baseline) < 2:
        return recent_mean, baseline_mean, 0.0
    recent_var = statistics.variance(recent)
    baseline_var = statistics.variance(baseline)
    se = math.sqrt((recent_var / len(recent)) + (baseline_var / len(baseline)))
    z = (recent_mean - baseline_mean) / se if se > 1e-12 else 0.0
    return recent_mean, baseline_mean, z


def _upsert_research_queue(
    conn: Any,
    *,
    topic_key: str,
    topic: str,
    market: str,
    strategy: str,
    regime: str,
    priority: float,
    reason: str,
    evidence: dict[str, Any],
) -> None:
    conn.execute(
        """
        INSERT INTO oracle_brain_research_queue(
            topic_key,topic,market,strategy,regime,priority,reason,evidence,status,
            created_at,updated_at,execution_impact
        )
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s::jsonb,'queued',NOW(),NOW(),'NONE')
        ON CONFLICT (topic_key) DO UPDATE SET
            priority=GREATEST(oracle_brain_research_queue.priority,EXCLUDED.priority),
            reason=EXCLUDED.reason,
            evidence=oracle_brain_research_queue.evidence || EXCLUDED.evidence,
            status=CASE WHEN oracle_brain_research_queue.status='retired' THEN 'retired' ELSE 'queued' END,
            updated_at=NOW()
        """,
        (topic_key, topic, market, strategy, regime, priority, reason, json.dumps(evidence)),
    )


def _create_or_update_experiment(
    conn: Any,
    *,
    market: str,
    strategy: str,
    regime: str,
    trigger_type: str,
    hypothesis: str,
    priority: float,
    evidence: dict[str, Any],
    latest_exit: datetime | None,
) -> str:
    experiment_key = f"experiment:{_slug(market)}:{_slug(strategy)}:{_slug(regime)}:{_slug(trigger_type)}"
    cutoff = latest_exit or datetime.now(timezone.utc)
    conn.execute(
        """
        INSERT INTO oracle_brain_experiments(
            experiment_key,market,strategy,regime,hypothesis,trigger_type,
            baseline_cutoff,target_samples,observed_samples,status,priority,evidence,
            result,created_at,updated_at,execution_impact
        )
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,0,'collecting',%s,%s::jsonb,'{}'::jsonb,NOW(),NOW(),'NONE')
        ON CONFLICT (experiment_key) DO UPDATE SET
            priority=GREATEST(oracle_brain_experiments.priority,EXCLUDED.priority),
            hypothesis=EXCLUDED.hypothesis,
            evidence=oracle_brain_experiments.evidence || EXCLUDED.evidence,
            updated_at=NOW()
        """,
        (
            experiment_key,
            market,
            strategy,
            regime,
            hypothesis,
            trigger_type,
            cutoff,
            EXPERIMENT_TARGET_SAMPLES,
            priority,
            json.dumps(evidence),
        ),
    )
    return experiment_key


def _analyze_uncertainty_drift_and_experiments(conn: Any, market: str) -> dict[str, int]:
    grouped = _load_cohort_returns(conn, market)
    counts = {"cohorts": 0, "uncertain": 0, "drift": 0, "experiments": 0}
    for (strategy, regime), rows in grouped.items():
        returns = [_num(row.get("return_pct"), float("nan")) for row in rows]
        returns = [value for value in returns if math.isfinite(value)]
        if not returns:
            continue
        counts["cohorts"] += 1
        latest_exit = _dt(rows[0].get("exit_time"))
        stat = statistical_summary(returns, seed_text=f"{market}|{strategy}|{regime}")
        topic_key = f"uncertainty:{_slug(market)}:{_slug(strategy)}:{_slug(regime)}"
        if stat["samples"] >= 20 and stat["robust_polarity"] == "uncertain":
            counts["uncertain"] += 1
            _upsert_research_queue(
                conn,
                topic_key=topic_key,
                topic=f"{strategy} / {regime}",
                market=market,
                strategy=strategy,
                regime=regime,
                priority=82.0,
                reason="Post-cost return uncertainty interval crosses zero; collect forward evidence before increasing conviction.",
                evidence={"statistics": stat, "version": V3_VERSION},
            )
            _create_or_update_experiment(
                conn,
                market=market,
                strategy=strategy,
                regime=regime,
                trigger_type="uncertainty",
                hypothesis="Forward exact-provenance paper outcomes will determine whether post-cost expectancy remains distinguishable from zero.",
                priority=82.0,
                evidence={"statistics": stat},
                latest_exit=latest_exit,
            )
            counts["experiments"] += 1
        elif stat["robust_polarity"] in {"positive", "negative"}:
            conn.execute(
                """
                UPDATE oracle_brain_research_queue
                SET status='resolved',updated_at=NOW()
                WHERE topic_key=%s AND status IN ('queued','in_review')
                """,
                (topic_key,),
            )

        if len(returns) >= 40:
            recent_n = min(30, max(15, len(returns) // 3))
            recent = returns[:recent_n]
            baseline = returns[recent_n:min(len(returns), recent_n + 90)]
            if len(baseline) >= 15:
                recent_mean, baseline_mean, z = _drift_stat(recent, baseline)
                recent_wins = sum(1 for value in recent if value > 0) / len(recent)
                baseline_wins = sum(1 for value in baseline if value > 0) / len(baseline)
                sign_flip = (recent_mean > 0 > baseline_mean) or (recent_mean < 0 < baseline_mean)
                drift = abs(z) >= DRIFT_Z_THRESHOLD or (sign_flip and abs(recent_mean - baseline_mean) >= 0.25)
                severity = "high" if abs(z) >= 3.0 or sign_flip else "medium" if drift else "low"
                event_key = f"drift:{_slug(market)}:{_slug(strategy)}:{_slug(regime)}"
                conn.execute(
                    """
                    INSERT INTO oracle_brain_drift_events(
                        event_key,market,strategy,regime,baseline_samples,recent_samples,
                        baseline_expectancy,recent_expectancy,baseline_win_rate,recent_win_rate,
                        z_score,sign_flip,drift_detected,severity,status,detected_at,last_observed_at,
                        metadata,execution_impact
                    )
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,NOW(),NOW(),%s::jsonb,'NONE')
                    ON CONFLICT (event_key) DO UPDATE SET
                        baseline_samples=EXCLUDED.baseline_samples,
                        recent_samples=EXCLUDED.recent_samples,
                        baseline_expectancy=EXCLUDED.baseline_expectancy,
                        recent_expectancy=EXCLUDED.recent_expectancy,
                        baseline_win_rate=EXCLUDED.baseline_win_rate,
                        recent_win_rate=EXCLUDED.recent_win_rate,
                        z_score=EXCLUDED.z_score,
                        sign_flip=EXCLUDED.sign_flip,
                        drift_detected=EXCLUDED.drift_detected,
                        severity=EXCLUDED.severity,
                        status=CASE WHEN EXCLUDED.drift_detected THEN 'active' ELSE 'resolved' END,
                        last_observed_at=NOW(),
                        metadata=oracle_brain_drift_events.metadata || EXCLUDED.metadata
                    """,
                    (
                        event_key,
                        market,
                        strategy,
                        regime,
                        len(baseline),
                        len(recent),
                        baseline_mean,
                        recent_mean,
                        baseline_wins,
                        recent_wins,
                        z,
                        sign_flip,
                        drift,
                        severity,
                        "active" if drift else "resolved",
                        json.dumps({"recent_window": recent_n, "threshold": DRIFT_Z_THRESHOLD}),
                    ),
                )
                if drift:
                    counts["drift"] += 1
                    drift_evidence = {
                        "recent_expectancy": round(recent_mean, 8),
                        "baseline_expectancy": round(baseline_mean, 8),
                        "z_score": round(z, 4),
                        "sign_flip": sign_flip,
                        "recent_samples": len(recent),
                        "baseline_samples": len(baseline),
                    }
                    _upsert_research_queue(
                        conn,
                        topic_key=event_key,
                        topic=f"{strategy} / {regime}",
                        market=market,
                        strategy=strategy,
                        regime=regime,
                        priority=95.0 if severity == "high" else 88.0,
                        reason="Recent exact-provenance outcomes materially diverge from the historical cohort.",
                        evidence=drift_evidence,
                    )
                    _create_or_update_experiment(
                        conn,
                        market=market,
                        strategy=strategy,
                        regime=regime,
                        trigger_type="drift",
                        hypothesis="Collect new forward paper outcomes to determine whether the recent regime shift persists.",
                        priority=95.0 if severity == "high" else 88.0,
                        evidence=drift_evidence,
                        latest_exit=latest_exit,
                    )
                    counts["experiments"] += 1

    active_contradictions = list(
        conn.execute(
            """
            SELECT subject_key,current_polarity,reason,metadata
            FROM oracle_brain_contradictions
            WHERE status='active'
            ORDER BY detected_at DESC
            LIMIT 100
            """
        ).fetchall()
    )
    for item in active_contradictions:
        meta = _json_obj(item.get("metadata"))
        market_value = str(meta.get("market") or market)
        if market_value != market:
            continue
        strategy = str(meta.get("strategy") or "unknown")
        regime = str(meta.get("regime") or "unknown")
        _create_or_update_experiment(
            conn,
            market=market,
            strategy=strategy,
            regime=regime,
            trigger_type="contradiction",
            hypothesis="New exact-provenance outcomes must resolve the conflicting mature evidence without overwriting either prior lesson.",
            priority=96.0,
            evidence={"subject_key": item.get("subject_key"), "reason": item.get("reason")},
            latest_exit=None,
        )
        counts["experiments"] += 1

    return counts


def _update_experiments(conn: Any, market: str) -> int:
    experiments = list(
        conn.execute(
            """
            SELECT experiment_key,strategy,regime,baseline_cutoff,target_samples,status
            FROM oracle_brain_experiments
            WHERE market=%s AND status IN ('collecting','ready_for_review')
            ORDER BY priority DESC,created_at ASC
            LIMIT 100
            """,
            (market,),
        ).fetchall()
    )
    updated = 0
    for exp in experiments:
        rows = list(
            conn.execute(
                """
                SELECT return_pct
                FROM oracle_brain_episodes
                WHERE market=%s AND strategy=%s AND regime=%s
                  AND provenance_status='exact' AND exit_time>%s AND return_pct IS NOT NULL
                ORDER BY exit_time ASC
                LIMIT %s
                """,
                (
                    market,
                    exp["strategy"],
                    exp["regime"],
                    exp["baseline_cutoff"],
                    max(1, int(exp["target_samples"]) * 3),
                ),
            ).fetchall()
        )
        values = [_num(row.get("return_pct"), float("nan")) for row in rows]
        values = [value for value in values if math.isfinite(value)]
        stats = statistical_summary(values, seed_text=str(exp["experiment_key"]))
        ready = len(values) >= int(exp["target_samples"])
        conn.execute(
            """
            UPDATE oracle_brain_experiments
            SET observed_samples=%s,
                status=CASE WHEN %s THEN 'ready_for_review' ELSE 'collecting' END,
                result=%s::jsonb,
                ready_at=CASE WHEN %s THEN COALESCE(ready_at,NOW()) ELSE ready_at END,
                updated_at=NOW()
            WHERE experiment_key=%s
            """,
            (len(values), ready, json.dumps(stats), ready, exp["experiment_key"]),
        )
        updated += 1
    return updated


def _refresh_working_memory(conn: Any, market: str) -> int:
    conn.execute("DELETE FROM oracle_brain_working_memory WHERE expires_at<NOW()")
    count = 0

    drift_rows = list(
        conn.execute(
            """
            SELECT event_key,strategy,regime,severity,recent_expectancy,baseline_expectancy,z_score
            FROM oracle_brain_drift_events
            WHERE market=%s AND status='active' AND drift_detected=TRUE
            ORDER BY CASE severity WHEN 'high' THEN 3 WHEN 'medium' THEN 2 ELSE 1 END DESC,
                     last_observed_at DESC
            LIMIT 20
            """,
            (market,),
        ).fetchall()
    )
    for row in drift_rows:
        importance = 0.95 if row.get("severity") == "high" else 0.85
        conn.execute(
            """
            INSERT INTO oracle_brain_working_memory(
                memory_key,market,kind,topic,payload,importance,created_at,updated_at,expires_at,execution_impact
            )
            VALUES (%s,%s,'drift',%s,%s::jsonb,%s,NOW(),NOW(),NOW()+INTERVAL '12 hours','NONE')
            ON CONFLICT (memory_key) DO UPDATE SET
                payload=EXCLUDED.payload,importance=EXCLUDED.importance,
                updated_at=NOW(),expires_at=EXCLUDED.expires_at
            """,
            (
                f"working:{row['event_key']}",
                market,
                f"{row['strategy']} / {row['regime']}",
                json.dumps(dict(row)),
                importance,
            ),
        )
        count += 1

    experiments = list(
        conn.execute(
            """
            SELECT experiment_key,strategy,regime,trigger_type,status,priority,observed_samples,target_samples,result
            FROM oracle_brain_experiments
            WHERE market=%s AND status IN ('collecting','ready_for_review')
            ORDER BY priority DESC,updated_at DESC
            LIMIT 20
            """,
            (market,),
        ).fetchall()
    )
    for row in experiments:
        conn.execute(
            """
            INSERT INTO oracle_brain_working_memory(
                memory_key,market,kind,topic,payload,importance,created_at,updated_at,expires_at,execution_impact
            )
            VALUES (%s,%s,'experiment',%s,%s::jsonb,%s,NOW(),NOW(),NOW()+INTERVAL '24 hours','NONE')
            ON CONFLICT (memory_key) DO UPDATE SET
                payload=EXCLUDED.payload,importance=EXCLUDED.importance,
                updated_at=NOW(),expires_at=EXCLUDED.expires_at
            """,
            (
                f"working:{row['experiment_key']}",
                market,
                f"{row['strategy']} / {row['regime']}",
                json.dumps(dict(row)),
                min(1.0, max(0.5, _num(row.get("priority"), 50.0) / 100.0)),
            ),
        )
        count += 1
    return count


def sync_v3_extensions(
    conn: Any,
    market: str,
    *,
    run_key: str | None = None,
    deadline_monotonic: float | None = None,
) -> dict[str, Any]:
    deadline = deadline_monotonic or (time.monotonic() + SYNC_BUDGET_SECONDS)
    result: dict[str, Any] = {
        "version": V3_VERSION,
        "market": market,
        "status": "ok",
        "execution_impact": "NONE",
    }

    def remaining() -> bool:
        return time.monotonic() < deadline

    if run_key:
        record_learning_stage(run_key, market, "source_corroboration")
    if remaining():
        clustered, clusters = _cluster_unassigned_sources(conn)
        providers = _refresh_provider_reputation(conn)
        result.update({"sources_clustered": clustered, "clusters_touched": clusters, "providers_scored": providers})
    else:
        result["status"] = "partial"
        return result

    if run_key:
        record_learning_stage(run_key, market, "counterfactual_learning")
    if remaining():
        captured = _capture_counterfactuals(conn, market)
        resolved = _resolve_counterfactuals(conn, market)
        result.update({"counterfactuals_captured": captured, "counterfactuals": resolved})
    else:
        result["status"] = "partial"
        return result

    if run_key:
        record_learning_stage(run_key, market, "uncertainty_and_drift")
    if remaining():
        analysis = _analyze_uncertainty_drift_and_experiments(conn, market)
        result.update(analysis)
    else:
        result["status"] = "partial"
        return result

    if run_key:
        record_learning_stage(run_key, market, "experiment_progress")
    if remaining():
        result["experiments_updated"] = _update_experiments(conn, market)
        result["working_memory_items"] = _refresh_working_memory(conn, market)
    else:
        result["status"] = "partial"

    return result


def retrieve_similar_brain_episodes(
    *,
    market: str,
    features: dict[str, Any],
    strategy: str | None = None,
    regime: str | None = None,
    limit: int = 25,
    require_same_schema: bool = True,
) -> dict[str, Any]:
    from database import connect
    from market_memory import feature_vector, setup_similarity

    current_vector = feature_vector(features)
    schema_hash = feature_schema_hash(features)
    clauses = ["market=%s", "provenance_status='exact'", "return_pct IS NOT NULL"]
    params: list[Any] = [market]
    if strategy:
        clauses.append("strategy=%s")
        params.append(strategy)
    if regime:
        clauses.append("regime=%s")
        params.append(regime)
    if require_same_schema:
        clauses.append("feature_schema_hash=%s")
        params.append(schema_hash)

    with connect() as conn:
        rows = list(
            conn.execute(
                f"""
                SELECT episode_key,trade_id,symbol,strategy,regime,return_pct,net_pnl,
                       mfe_pct,mae_pct,confidence,freshness_score,feature_snapshot,
                       feature_schema_hash,exit_time
                FROM oracle_brain_episodes
                WHERE {' AND '.join(clauses)}
                ORDER BY exit_time DESC
                LIMIT 750
                """,
                tuple(params),
            ).fetchall()
        )

    analogs: list[dict[str, Any]] = []
    for row in rows:
        vector = feature_vector(_json_obj(row.get("feature_snapshot")))
        similarity = setup_similarity(current_vector, vector)
        if similarity < SIMILARITY_MIN:
            continue
        analogs.append({**dict(row), "similarity": similarity})

    analogs.sort(key=lambda item: (item["similarity"], _num(item.get("confidence"), 0.0)), reverse=True)
    selected = analogs[:max(1, int(limit))]
    weights = [max(0.01, _num(item["similarity"]) ** 3 * max(0.25, _num(item.get("freshness_score"), 1.0))) for item in selected]
    returns = [_num(item.get("return_pct"), 0.0) for item in selected]
    if selected and sum(weights) > 0:
        weighted_mean = sum(value * weight for value, weight in zip(returns, weights)) / sum(weights)
        effective_n = (sum(weights) ** 2) / max(1e-9, sum(weight * weight for weight in weights))
        weighted_win = sum(weight for value, weight in zip(returns, weights) if value > 0) / sum(weights)
    else:
        weighted_mean = None
        effective_n = 0.0
        weighted_win = None
    ci_low, ci_high = deterministic_bootstrap_interval(
        returns,
        seed_text=f"similarity|{market}|{strategy}|{regime}|{schema_hash}",
    ) if returns else (None, None)

    return {
        "market": market,
        "strategy": strategy,
        "regime": regime,
        "feature_schema_hash": schema_hash,
        "schema_strict": require_same_schema,
        "analog_count": len(selected),
        "effective_sample_size": round(effective_n, 3),
        "weighted_expectancy_pct": None if weighted_mean is None else round(weighted_mean, 6),
        "weighted_win_rate": None if weighted_win is None else round(weighted_win, 6),
        "expectancy_ci_low": None if ci_low is None else round(ci_low, 6),
        "expectancy_ci_high": None if ci_high is None else round(ci_high, 6),
        "analogs": selected,
        "execution_impact": "NONE",
    }


__all__ = [
    "COUNTERFACTUAL_HORIZONS_MINUTES",
    "DRIFT_Z_THRESHOLD",
    "EXPERIMENT_TARGET_SAMPLES",
    "SIMILARITY_MIN",
    "SYNC_BUDGET_SECONDS",
    "V3_VERSION",
    "begin_learning_run",
    "deterministic_bootstrap_interval",
    "feature_schema_hash",
    "feature_value_hash",
    "finish_learning_run",
    "record_learning_stage",
    "retrieve_similar_brain_episodes",
    "statistical_summary",
    "sync_v3_extensions",
    "wilson_interval",
]
