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
        SELECT strategy, regime, COUNT(*)::int AS samples,
               SUM(net_pnl) AS net_pnl,
               SUM(fees) AS fees,
               AVG(net_pnl) AS expectancy,
               AVG(mfe_pct) FILTER (WHERE excursion_sample_count > 0) AS avg_mfe_pct,
               AVG(mae_pct) FILTER (WHERE excursion_sample_count > 0) AS avg_mae_pct,
               SUM(CASE WHEN excursion_sample_count > 0 THEN 1 ELSE 0 END)::int AS excursion_trades
        FROM paper_regime_trade_metrics
        GROUP BY strategy, regime
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
               observed_at,source_quality,freshness_score,confidence,status,metadata,
               cluster_key,adaptive_reputation,corroboration_count
        FROM oracle_brain_sources
        ORDER BY confidence DESC,freshness_score DESC,observed_at DESC NULLS LAST
        LIMIT 80
        """,
    )
    episodes = _safe_rows(
        fetch_rows,
        """
        SELECT episode_key,trade_id,market,symbol,strategy,regime,entry_time,exit_time,
               net_pnl,fees,return_pct,mfe_pct,mae_pct,provenance_status,
               source_quality,freshness_score,confidence,outcome_snapshot,tags,
               model,model_version,strategy_version,feature_schema_hash,feature_value_hash
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
    source_clusters = _safe_rows(
        fetch_rows,
        """
        SELECT cluster_key,canonical_title,symbol,category,source_count,provider_count,
               providers,first_observed_at,last_observed_at,corroboration_score,metadata
        FROM oracle_brain_source_clusters
        ORDER BY corroboration_score DESC,last_observed_at DESC NULLS LAST
        LIMIT 60
        """,
    )
    provider_reputation = _safe_rows(
        fetch_rows,
        """
        SELECT provider,base_quality,total_events,corroborated_events,reputation_score,updated_at,metadata
        FROM oracle_brain_provider_reputation
        ORDER BY reputation_score DESC,total_events DESC
        LIMIT 60
        """,
    )
    counterfactuals = _safe_rows(
        fetch_rows,
        """
        SELECT decision_audit_id,horizon_minutes,market,symbol,strategy,regime,
               recommendation,approved,reason,observed_at,entry_price,due_at,resolved_at,
               future_price,gross_return_pct,estimated_cost_pct,net_return_pct,
               classification,feature_schema_hash
        FROM oracle_brain_counterfactuals
        ORDER BY observed_at DESC,id DESC
        LIMIT 160
        """,
    )
    drift_events = _safe_rows(
        fetch_rows,
        """
        SELECT event_key,market,strategy,regime,baseline_samples,recent_samples,
               baseline_expectancy,recent_expectancy,baseline_win_rate,recent_win_rate,
               z_score,sign_flip,drift_detected,severity,status,detected_at,last_observed_at,metadata
        FROM oracle_brain_drift_events
        WHERE status='active' AND drift_detected=TRUE
        ORDER BY CASE severity WHEN 'high' THEN 3 WHEN 'medium' THEN 2 ELSE 1 END DESC,
                 last_observed_at DESC
        LIMIT 60
        """,
    )
    working_memory = _safe_rows(
        fetch_rows,
        """
        SELECT memory_key,market,symbol,kind,topic,payload,importance,updated_at,expires_at
        FROM oracle_brain_working_memory
        WHERE expires_at>NOW()
        ORDER BY importance DESC,updated_at DESC
        LIMIT 80
        """,
    )
    brain_experiments = _safe_rows(
        fetch_rows,
        """
        SELECT experiment_key,market,strategy,regime,hypothesis,trigger_type,baseline_cutoff,
               target_samples,observed_samples,status,priority,evidence,result,created_at,
               updated_at,ready_at
        FROM oracle_brain_experiments
        WHERE status IN ('collecting','ready_for_review')
        ORDER BY priority DESC,updated_at DESC
        LIMIT 80
        """,
    )
    learning_runs = _safe_rows(
        fetch_rows,
        """
        SELECT run_key,market,started_at,finished_at,stage,status,elapsed_ms,details
        FROM oracle_brain_learning_runs
        ORDER BY started_at DESC
        LIMIT 40
        """,
    )

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

    stale_sources = [
        item for item in sources
        if str(item.get("status") or "") == "stale" or _num(item.get("freshness_score"), 1.0) < 0.18
    ]
    high_confidence_links = [
        item for item in links
        if _num(item.get("confidence"), 0.0) >= 0.70 and int(_num(item.get("evidence_count"), 0.0)) >= 3
    ]
    missed_winners = [item for item in counterfactuals if item.get("classification") == "missed_winner"]
    avoided_losses = [item for item in counterfactuals if item.get("classification") == "avoided_loss"]
    completed_counterfactuals = [
        item for item in counterfactuals
        if item.get("classification") in {"missed_winner", "avoided_loss", "neutral"}
    ]
    ready_experiments = [item for item in brain_experiments if item.get("status") == "ready_for_review"]
    failed_runs = [item for item in learning_runs if item.get("status") == "failed"]
    recent_run = learning_runs[0] if learning_runs else None

    if drift_events:
        derived_lessons.append(
            {
                "level": "warning",
                "title": "Recent evidence drift detected",
                "body": (
                    f"{len(drift_events)} cohort(s) show a material recent-vs-historical shift. "
                    "Treat older evidence as less transferable until forward paper experiments resolve the drift."
                ),
                "source": "oracle_brain_drift_events",
            }
        )
    if completed_counterfactuals:
        derived_lessons.append(
            {
                "level": "info",
                "title": "Abstentions are now producing learning evidence",
                "body": (
                    f"Among the recent resolved abstention horizons shown here, {len(avoided_losses)} avoided losses "
                    f"and {len(missed_winners)} missed winners were observed. Use both sides to calibrate gates."
                ),
                "source": "oracle_brain_counterfactuals",
            }
        )

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
        "source_clusters": source_clusters,
        "provider_reputation": provider_reputation,
        "counterfactuals": counterfactuals,
        "drift_events": drift_events,
        "working_memory": working_memory,
        "brain_experiments": brain_experiments,
        "learning_runs": learning_runs,
        "derived_lessons": derived_lessons,
        "safety": runtime_safety_state(),
        "summary": {
            "active_entries": len(entries),
            "experiments": len(experiments),
            "mature_negative_regimes": len(negative_mature),
            "mature_positive_regimes": len(positive_mature),
            "workers_observed": len(workers),
            "knowledge_sources": len(sources),
            "stale_sources": len(stale_sources),
            "exact_episodes": len(episodes),
            "concept_links": len(links),
            "high_confidence_links": len(high_confidence_links),
            "active_contradictions": len(contradictions),
            "research_topics": len(research_queue),
            "source_clusters": len(source_clusters),
            "providers_scored": len(provider_reputation),
            "counterfactuals_resolved": len(completed_counterfactuals),
            "avoided_losses": len(avoided_losses),
            "missed_winners": len(missed_winners),
            "active_drift_events": len(drift_events),
            "working_memory_items": len(working_memory),
            "active_brain_experiments": len(brain_experiments),
            "ready_brain_experiments": len(ready_experiments),
            "learning_run_failures": len(failed_runs),
            "latest_learning_run_status": None if recent_run is None else recent_run.get("status"),
            "latest_learning_run_stage": None if recent_run is None else recent_run.get("stage"),
            "latest_learning_run_ms": None if recent_run is None else recent_run.get("elapsed_ms"),
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
