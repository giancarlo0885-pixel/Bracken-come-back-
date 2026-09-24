from __future__ import annotations

from dataclasses import dataclass, asdict
from datetime import datetime, timezone
import json
import math
import os
from typing import Any, Callable


FetchRows = Callable[[str, tuple[Any, ...]], list[dict[str, Any]]]


CORE_DOCTRINE = [
    {
        "key": "evidence_truth",
        "title": "Evidence before narrative",
        "body": "Missing, stale, unproven, or contradictory evidence remains explicit. Oracle never invents a value to complete a decision path.",
    },
    {
        "key": "provenance",
        "title": "Exact provenance before learning",
        "body": "Trade learning requires the actual entry-time decision, features, quote/forecast identity, and canonical lot or ledger linkage.",
    },
    {
        "key": "bounded_hybrid",
        "title": "Super Hybrid remains bounded",
        "body": "Hybrid confluence can shape paper quality but cannot independently approve execution or bypass downstream vetoes and capacity checks.",
    },
    {
        "key": "promotion",
        "title": "Promotion requires measured incremental value",
        "body": "New parameters begin in shadow or paper and earn influence only after sufficient post-cost, out-of-sample evidence.",
    },
    {
        "key": "live_separation",
        "title": "Research never arms live money",
        "body": "Research, visualization, learning, and paper optimization do not enable broker submission or live trading.",
    },
]


@dataclass(frozen=True)
class BrainEntry:
    id: int | None
    brain_key: str
    category: str
    title: str
    body: str
    evidence_type: str
    evidence_ref: str | None
    confidence: float
    status: str
    execution_impact: str
    supersedes_id: int | None
    metadata: dict[str, Any]
    created_at: Any

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _num(value: Any, default: float = 0.0) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return default
    return parsed if math.isfinite(parsed) else default


def _parse_timestamp(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        parsed = value
    else:
        text = str(value or "").strip()
        if not text:
            return None
        try:
            parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError:
            return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


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


def _safe_rows(
    fetch_rows: FetchRows,
    query: str,
    params: tuple[Any, ...] = (),
) -> list[dict[str, Any]]:
    try:
        return list(fetch_rows(query, params) or [])
    except Exception:
        return []


def _entry_from_row(row: dict[str, Any]) -> BrainEntry:
    return BrainEntry(
        id=int(row["id"]) if row.get("id") is not None else None,
        brain_key=str(row.get("brain_key") or ""),
        category=str(row.get("category") or ""),
        title=str(row.get("title") or ""),
        body=str(row.get("body") or ""),
        evidence_type=str(row.get("evidence_type") or ""),
        evidence_ref=str(row.get("evidence_ref") or "") or None,
        confidence=max(0.0, min(1.0, _num(row.get("confidence"), 0.0))),
        status=str(row.get("status") or "active"),
        execution_impact=str(row.get("execution_impact") or "NONE"),
        supersedes_id=int(row["supersedes_id"]) if row.get("supersedes_id") is not None else None,
        metadata=_json_obj(row.get("metadata")),
        created_at=row.get("created_at"),
    )


def runtime_safety_state() -> dict[str, Any]:
    execution_mode = str(os.getenv("EXECUTION_MODE", "paper") or "paper").strip().lower()
    broker_submission = str(os.getenv("ENABLE_BROKER_SUBMISSION", "false")).lower() == "true"
    live_armed = str(os.getenv("LIVE_TRADING_ARMED", "false")).lower() == "true"
    return {
        "execution_mode": execution_mode,
        "broker_submission_enabled": broker_submission,
        "live_trading_armed": live_armed,
        "research_execution_authority": "NONE",
        "safe_research_boundary": execution_mode == "paper" and not broker_submission and not live_armed,
    }


def build_oracle_brain_snapshot(fetch_rows: FetchRows) -> dict[str, Any]:
    entries = [
        _entry_from_row(row).to_dict()
        for row in _safe_rows(
            fetch_rows,
            """
            SELECT id,brain_key,category,title,body,evidence_type,evidence_ref,
                   confidence,status,execution_impact,supersedes_id,metadata,created_at
            FROM oracle_brain_entries
            WHERE status='active'
            ORDER BY category, created_at DESC, id DESC
            LIMIT 200
            """,
        )
    ]

    regime_rows = _safe_rows(
        fetch_rows,
        """
        SELECT market, strategy, regime, COUNT(*)::int AS samples,
               SUM(COALESCE(round_trip_net_pnl,net_pnl)) AS net_pnl,
               SUM(COALESCE(round_trip_fees,fees)) AS fees,
               AVG(COALESCE(round_trip_net_pnl,net_pnl)) AS expectancy,
               AVG(mfe_pct) FILTER (WHERE excursion_sample_count > 0) AS avg_mfe_pct,
               AVG(mae_pct) FILTER (WHERE excursion_sample_count > 0) AS avg_mae_pct,
               SUM(CASE WHEN excursion_sample_count > 0 THEN 1 ELSE 0 END)::int AS excursion_trades
        FROM paper_regime_trade_metrics
        GROUP BY market, strategy, regime
        ORDER BY samples DESC
        LIMIT 40
        """,
    )
    regimes: list[dict[str, Any]] = []
    for row in regime_rows:
        samples = int(_num(row.get("samples"), 0.0))
        expectancy = _num(row.get("expectancy"), 0.0)
        regimes.append(
            {
                "market": str(row.get("market") or "unknown"),
                "strategy": str(row.get("strategy") or "unknown"),
                "regime": str(row.get("regime") or "unknown"),
                "samples": samples,
                "net_pnl": round(_num(row.get("net_pnl")), 6),
                "fees": round(_num(row.get("fees")), 6),
                "expectancy": round(expectancy, 8),
                "avg_mfe_pct": None if row.get("avg_mfe_pct") is None else round(_num(row.get("avg_mfe_pct")), 6),
                "avg_mae_pct": None if row.get("avg_mae_pct") is None else round(_num(row.get("avg_mae_pct")), 6),
                "excursion_trades": int(_num(row.get("excursion_trades"), 0.0)),
                "mature": samples >= 30,
                "evidence_state": (
                    "positive"
                    if samples >= 30 and expectancy > 0
                    else "negative"
                    if samples >= 30 and expectancy < 0
                    else "insufficient"
                ),
            }
        )

    workers = _safe_rows(
        fetch_rows,
        """
        SELECT market,status,message,last_run,heartbeat,execution_mode
        FROM market_worker_status
        WHERE market IN ('cash','crypto')
        ORDER BY market
        """,
    )

    sources = _safe_rows(
        fetch_rows,
        """
        SELECT source_key,source_type,provider,category,symbol,title,source_ref,
               observed_at,source_quality,freshness_score,confidence,status,metadata
        FROM oracle_brain_sources
        ORDER BY observed_at DESC NULLS LAST,confidence DESC,freshness_score DESC
        LIMIT 80
        """,
    )
    for source in sources:
        source["metadata"] = _json_obj(source.get("metadata"))
        source["verification_status"] = str(
            source["metadata"].get("verification_status") or "reported"
        )
        source["ranking_eligible"] = bool(source["metadata"].get("ranking_eligible", False))
    source_count_rows = _safe_rows(
        fetch_rows,
        "SELECT COUNT(*)::int AS total FROM oracle_brain_sources",
    )
    source_total = int(_num(source_count_rows[0].get("total"), len(sources))) if source_count_rows else len(sources)
    episodes = _safe_rows(
        fetch_rows,
        """
        SELECT episode_key,trade_id,market,symbol,strategy,regime,entry_time,exit_time,
               net_pnl,fees,return_pct,mfe_pct,mae_pct,provenance_status,
               source_quality,freshness_score,confidence,feature_snapshot,outcome_snapshot,tags
        FROM oracle_brain_episodes
        WHERE provenance_status='exact'
        ORDER BY exit_time DESC NULLS LAST
        LIMIT 120
        """,
    )
    links = _safe_rows(
        fetch_rows,
        """
        SELECT source_key,target_key,relation,weight,evidence_count,confidence,
               last_observed_at,metadata
        FROM oracle_brain_links
        ORDER BY confidence DESC,evidence_count DESC,last_observed_at DESC
        LIMIT 120
        """,
    )
    contradictions = _safe_rows(
        fetch_rows,
        """
        SELECT contradiction_key,subject_key,prior_polarity,current_polarity,reason,
               severity,status,detected_at,metadata
        FROM oracle_brain_contradictions
        WHERE status='active'
        ORDER BY detected_at DESC
        LIMIT 40
        """,
    )
    research_queue = _safe_rows(
        fetch_rows,
        """
        SELECT topic_key,topic,market,strategy,regime,priority,reason,evidence,status,updated_at
        FROM oracle_brain_research_queue
        WHERE status IN ('queued','in_review')
        ORDER BY priority DESC,updated_at DESC
        LIMIT 40
        """,
    )
    learning_state = _safe_rows(
        fetch_rows,
        """
        SELECT pipeline_key,market,last_source_id,last_episode_exit_at,last_sync_at,last_result
        FROM oracle_brain_learning_state
        ORDER BY pipeline_key,market
        """,
    )

    now = datetime.now(timezone.utc)
    sync_rows: list[tuple[dict[str, Any], datetime]] = []
    for item in learning_state:
        parsed = _parse_timestamp(item.get("last_sync_at"))
        if parsed is not None:
            sync_rows.append((item, parsed))

    latest_sync = max((parsed for _, parsed in sync_rows), default=None)
    recent_rows: list[dict[str, Any]] = []
    if latest_sync is not None:
        recent_rows = [
            item
            for item, parsed in sync_rows
            if (latest_sync - parsed).total_seconds() <= 15 * 60
        ]

    new_sources = 0
    revised_sources = 0
    new_exact_episodes = 0
    lessons_updated = 0
    skipped_provenance = 0
    for item in recent_rows:
        result = _json_obj(item.get("last_result"))
        pipeline = str(item.get("pipeline_key") or "")
        if pipeline == "intelligence":
            new_sources += int(_num(result.get("new"), 0.0))
            revised_sources += int(_num(result.get("updated"), 0.0))
        elif pipeline == "episodes":
            new_exact_episodes += int(_num(result.get("new_exact_episodes"), 0.0))
            skipped_provenance += int(_num(result.get("skipped_missing_exact_provenance"), 0.0))
        elif pipeline == "brain_v2":
            lessons_updated += int(_num(result.get("lessons_updated"), 0.0))

    sync_age_seconds = None if latest_sync is None else max(0.0, (now - latest_sync).total_seconds())
    learned_this_cycle = new_sources + revised_sources + new_exact_episodes + lessons_updated
    if latest_sync is None:
        learning_status = "NOT SYNCED"
    elif sync_age_seconds is not None and sync_age_seconds > 45 * 60:
        learning_status = "STALE"
    elif learned_this_cycle > 0:
        learning_status = "LEARNING"
    else:
        learning_status = "SYNCED — NO NEW EVIDENCE"

    learning_activity = {
        "status": learning_status,
        "last_sync_at": latest_sync.isoformat() if latest_sync else None,
        "sync_age_seconds": None if sync_age_seconds is None else round(sync_age_seconds, 1),
        "pipelines_total": len(learning_state),
        "pipelines_recent": len(recent_rows),
        "new_sources": new_sources,
        "revised_sources": revised_sources,
        "new_exact_episodes": new_exact_episodes,
        "lessons_updated": lessons_updated,
        "skipped_missing_exact_provenance": skipped_provenance,
        "learned_this_cycle": learned_this_cycle,
        "execution_authority": "NONE",
    }

    retention_spans = {}
    for table, timestamp_column, key, predicate in (
        ("oracle_brain_entries", "created_at", "entries", ""),
        ("oracle_brain_sources", "observed_at", "sources", ""),
        ("oracle_brain_episodes", "exit_time", "episodes", " WHERE provenance_status='exact'"),
        ("oracle_brain_observations", "event_time", "observations", ""),
    ):
        stats = _safe_rows(
            fetch_rows,
            f"SELECT COUNT(*)::int AS total, MIN({timestamp_column}) AS oldest, MAX({timestamp_column}) AS newest FROM {table}{predicate}",
        )
        row = stats[0] if stats else {}
        retention_spans[key] = {
            "count": int(_num(row.get("total"), 0.0)),
            "oldest": row.get("oldest"),
            "newest": row.get("newest"),
        }
    sync_times = [row.get("last_sync_at") for row in learning_state if row.get("last_sync_at") is not None]
    retention_health = {
        "persistent_store": "PostgreSQL",
        "entries": retention_spans["entries"],
        "sources": retention_spans["sources"],
        "episodes": retention_spans["episodes"],
        "observations": retention_spans["observations"],
        "learning_pipelines": len(learning_state),
        "last_sync_at": max(sync_times) if sync_times else None,
        "read_only": True,
        "execution_authority": "NONE",
    }

    link_stats = _safe_rows(fetch_rows, "SELECT COUNT(*)::int AS total FROM oracle_brain_links")
    relationship_count = int(_num((link_stats[0] if link_stats else {}).get("total"), len(links)))

    growth = {
        "knowledge_units": (
            retention_spans["entries"]["count"]
            + retention_spans["sources"]["count"]
            + retention_spans["episodes"]["count"]
            + retention_spans["observations"]["count"]
            + relationship_count
        ),
        "durable_lessons": retention_spans["entries"]["count"],
        "observations": retention_spans["observations"]["count"],
        "intelligence_sources": retention_spans["sources"]["count"],
        "exact_outcomes": retention_spans["episodes"]["count"],
        "relationships": relationship_count,
        "oldest_observation": retention_spans["observations"]["oldest"],
        "newest_observation": retention_spans["observations"]["newest"],
        "last_learning_sync": retention_health["last_sync_at"],
        "execution_authority": "NONE",
    }

    experiments = [
        entry for entry in entries
        if entry.get("category") in {"experiment", "research", "promotion"}
    ]
    negative_mature = [
        item for item in regimes
        if item["mature"] and item["expectancy"] < 0
    ]
    positive_mature = [
        item for item in regimes
        if item["mature"] and item["expectancy"] > 0
    ]

    derived_lessons: list[dict[str, Any]] = []
    if negative_mature:
        worst = min(negative_mature, key=lambda item: item["expectancy"])
        derived_lessons.append(
            {
                "level": "warning",
                "title": "Mature negative regime evidence exists",
                "body": (
                    f"{worst['strategy']} / {worst['regime']} has "
                    f"{worst['samples']} samples and expectancy {worst['expectancy']:+.6f}. "
                    "Do not increase confidence or sizing for that cohort without new post-cost evidence."
                ),
                "source": "paper_regime_trade_metrics",
            }
        )
    if positive_mature:
        best = max(positive_mature, key=lambda item: item["expectancy"])
        derived_lessons.append(
            {
                "level": "info",
                "title": "Mature positive regime evidence exists",
                "body": (
                    f"{best['strategy']} / {best['regime']} has "
                    f"{best['samples']} samples and expectancy {best['expectancy']:+.6f}. "
                    "Treat this as evidence for deeper validation, not automatic promotion."
                ),
                "source": "paper_regime_trade_metrics",
            }
        )
    if contradictions:
        derived_lessons.append(
            {
                "level": "warning",
                "title": "Knowledge contradiction requires forward evidence",
                "body": (
                    f"{len(contradictions)} active contradiction(s) are preserved instead of silently overwriting "
                    "older evidence. Resolve them with new exact-provenance outcomes."
                ),
                "source": "oracle_brain_contradictions",
            }
        )

    # Read-only attribution diagnostics separate decision quality from realized luck.
    # They never feed execution; they only compare exact entry-time thesis fields
    # with later paper outcomes.
    attribution: list[dict[str, Any]] = []
    for item in episodes:
        features = _json_obj(item.get("feature_snapshot"))
        outcome = _json_obj(item.get("outcome_snapshot"))
        expected_edge = next(
            (_num(features[key]) for key in ("expected_edge_pct", "expected_value_pct", "edge_pct") if features.get(key) is not None),
            None,
        )
        probability = next(
            (_num(features[key]) for key in ("probability_of_profit", "probability", "win_probability") if features.get(key) is not None),
            None,
        )
        if probability is not None and 1.0 < probability <= 100.0:
            probability /= 100.0
        estimated_cost = next(
            (max(0.0, _num(features[key])) for key in ("estimated_cost_pct", "total_cost_pct", "cost_pct") if features.get(key) is not None),
            None,
        )
        actual_return = None if item.get("return_pct") is None else _num(item.get("return_pct"))
        thesis_positive = expected_edge is not None and expected_edge > 0
        outcome_positive = actual_return is not None and actual_return > 0
        if expected_edge is None or actual_return is None:
            attribution_state = "insufficient"
        elif thesis_positive and outcome_positive:
            attribution_state = "thesis_confirmed"
        elif thesis_positive and not outcome_positive:
            attribution_state = "thesis_failed"
        elif not thesis_positive and outcome_positive:
            attribution_state = "positive_outcome_without_positive_thesis"
        else:
            attribution_state = "negative_thesis_confirmed"
        attribution.append({
            "episode_key": item.get("episode_key"),
            "strategy": item.get("strategy"),
            "regime": item.get("regime"),
            "expected_edge_pct": expected_edge,
            "probability_of_profit": probability,
            "estimated_cost_pct": estimated_cost,
            "actual_return_pct": actual_return,
            "mfe_pct": item.get("mfe_pct"),
            "mae_pct": item.get("mae_pct"),
            "attribution_state": attribution_state,
            "entry_signal_id": outcome.get("entry_signal_id"),
        })

    attribution_counts: dict[str, int] = {}
    for item in attribution:
        state = str(item["attribution_state"])
        attribution_counts[state] = attribution_counts.get(state, 0) + 1

    stale_sources = [
        item for item in sources
        if str(item.get("status") or "") == "stale" or _num(item.get("freshness_score"), 1.0) < 0.18
    ]
    high_confidence_links = [
        item for item in links
        if _num(item.get("confidence"), 0.0) >= 0.70 and int(_num(item.get("evidence_count"), 0.0)) >= 3
    ]

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "read_only": True,
        "execution_authority": "NONE",
        "doctrine": CORE_DOCTRINE,
        "entries": entries,
        "experiments": experiments,
        "regime_economics": regimes,
        "workers": workers,
        "sources": sources,
        "episodes": episodes,
        "concept_links": links,
        "contradictions": contradictions,
        "research_queue": research_queue,
        "learning_state": learning_state,
        "learning_activity": learning_activity,
        "retention_health": retention_health,
        "growth": growth,
        "derived_lessons": derived_lessons,
        "outcome_attribution": attribution,
        "attribution_counts": attribution_counts,
        "safety": runtime_safety_state(),
        "summary": {
            "active_entries": len(entries),
            "experiments": len(experiments),
            "mature_negative_regimes": len(negative_mature),
            "mature_positive_regimes": len(positive_mature),
            "workers_observed": len(workers),
            "knowledge_sources": source_total,
            "stale_sources": len(stale_sources),
            "exact_episodes": len(episodes),
            "concept_links": len(links),
            "high_confidence_links": len(high_confidence_links),
            "active_contradictions": len(contradictions),
            "research_topics": len(research_queue),
        },
    }


def record_brain_entry(
    *,
    brain_key: str,
    category: str,
    title: str,
    body: str,
    evidence_type: str,
    evidence_ref: str | None = None,
    confidence: float = 1.0,
    metadata: dict[str, Any] | None = None,
    supersedes_id: int | None = None,
) -> int:
    """Append a durable Oracle Brain lesson without granting execution authority.

    This function is intentionally not called by the trading workers. It exists
    for explicit engineering/research workflows and preserves the old row when
    a lesson is superseded.
    """
    key = str(brain_key or "").strip()
    category = str(category or "").strip().lower()
    title = str(title or "").strip()
    body = str(body or "").strip()
    evidence_type = str(evidence_type or "").strip().lower()
    if not key or not category or not title or not body or not evidence_type:
        raise ValueError("brain_key, category, title, body and evidence_type are required")
    confidence = max(0.0, min(1.0, _num(confidence, 1.0)))

    from database import connect

    with connect() as conn:
        if supersedes_id is not None:
            conn.execute(
                """
                UPDATE oracle_brain_entries
                SET status='superseded'
                WHERE id=%s AND status='active'
                """,
                (int(supersedes_id),),
            )
        inserted = conn.execute(
            """
            INSERT INTO oracle_brain_entries(
                brain_key,category,title,body,evidence_type,evidence_ref,
                confidence,status,execution_impact,supersedes_id,metadata,created_at
            )
            VALUES (%s,%s,%s,%s,%s,%s,%s,'active','NONE',%s,%s::jsonb,%s)
            RETURNING id
            """,
            (
                key,
                category,
                title,
                body,
                evidence_type,
                evidence_ref,
                confidence,
                supersedes_id,
                json.dumps(metadata or {}),
                datetime.now(timezone.utc),
            ),
        ).fetchone()
        if not inserted:
            raise RuntimeError("Oracle Brain insert did not return an id")
        return int(inserted["id"])


__all__ = [
    "BrainEntry",
    "CORE_DOCTRINE",
    "build_oracle_brain_snapshot",
    "record_brain_entry",
    "runtime_safety_state",
]
