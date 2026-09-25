from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import json
import math
import os
import re
from typing import Any, Iterable


BRAIN_LEARNING_SYNC_SECONDS = max(300, int(os.getenv("ORACLE_BRAIN_SYNC_SECONDS", "900")))
MIN_MATURE_SAMPLES = max(20, int(os.getenv("ORACLE_BRAIN_MATURE_SAMPLES", "30")))
_SOURCE_BATCH = max(25, int(os.getenv("ORACLE_BRAIN_SOURCE_BATCH", "250")))
_EPISODE_BATCH = max(25, int(os.getenv("ORACLE_BRAIN_EPISODE_BATCH", "250")))


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


def _digest(*parts: Any) -> str:
    raw = "|".join(str(part or "") for part in parts)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]


def _normalized_strategy_identity(value: Any) -> str:
    """Use the same stable strategy identity as paper economics.

    Dynamic Council rationale and always-on pulse text changes every scan. Brain
    memory must aggregate those observations under the same stable strategy key
    or mature evidence fragments into many pseudo-strategies.
    """
    try:
        from paper_strategy_economics import normalize_strategy_identity

        normalized = normalize_strategy_identity(value)
    except Exception:
        normalized = str(value or "").strip() or "unattributed"
    return str(normalized or "unattributed")[:160]


def freshness_score(
    observed_at: Any,
    *,
    now: datetime | None = None,
    half_life_days: float = 14.0,
) -> float:
    observed = _dt(observed_at)
    if observed is None:
        return 0.35
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    age_days = max(0.0, (current - observed).total_seconds() / 86400.0)
    half_life = max(0.25, float(half_life_days))
    return round(max(0.02, min(1.0, 0.5 ** (age_days / half_life))), 6)


def source_quality(provider: Any, category: Any = "") -> float:
    name = str(provider or "").strip().lower()
    category_text = str(category or "").strip().lower()
    if (
        ".gov" in name
        or any(token in name for token in ("federal reserve", "u.s. treasury", "official"))
        or re.search(r"\b(?:sec|cftc|fred|bls|bea|nasa)\b", name)
    ):
        return 0.97
    if any(token in name for token in ("nasdaq", "finnhub", "alpha vantage", "eodhd", "polygon", "iex", "quiver")):
        return 0.86
    if (
        any(token in name for token in ("reuters", "bloomberg", "financial times", "wall street journal"))
        or re.search(r"\b(?:ap|associated press|ap news)\b", name)
    ):
        return 0.84
    if any(token in name for token in ("newsapi", "google", "yahoo")):
        return 0.70
    if any(token in name for token in ("reddit", "social", "twitter", "x.com")):
        return 0.45
    if any(token in category_text for token in ("earnings", "economic", "macro", "filing")):
        return 0.68
    return 0.58


def source_half_life_days(category: Any) -> float:
    text = str(category or "").lower()
    if any(token in text for token in ("breaking", "news", "social", "flow")):
        return 3.0
    if any(token in text for token in ("earnings", "calendar", "event")):
        return 10.0
    if any(token in text for token in ("macro", "economic", "policy")):
        return 21.0
    return 14.0


_SOURCE_VERIFICATION_STATES = {"verified", "corroborated", "reported", "unverified", "inference"}


def intelligence_source_from_row(
    row: dict[str, Any],
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Translate one canonical intelligence event into durable Brain memory.

    Facts and inference remain separate in metadata. Confidence is bounded by
    source quality and freshness, while unverified/inference-only observations
    are explicitly retained for research with no ranking eligibility.
    """
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)

    source_id = int(_num(row.get("id"), 0))
    event_key = str(row.get("event_key") or f"legacy:{source_id}").strip()
    provider = str(row.get("provider") or "unknown").strip()
    category = str(row.get("category") or "uncategorized").strip()
    symbol = str(row.get("symbol") or "").upper().strip() or None
    details = _json_obj(row.get("details"))
    intake_metadata = _json_obj(row.get("metadata"))
    verification = str(
        row.get("verification_status")
        or intake_metadata.get("verification_status")
        or details.get("verification_status")
        or "reported"
    ).strip().lower()
    if verification not in _SOURCE_VERIFICATION_STATES:
        verification = "unverified"

    observed = _dt(row.get("event_time")) or _dt(row.get("created_at")) or current
    quality = source_quality(provider, category)
    if verification == "verified" and not str(row.get("source_url") or "").strip() and quality < 0.95:
        verification = "unverified"
    half_life = source_half_life_days(category)
    fresh = freshness_score(observed, now=current, half_life_days=half_life)
    intake_confidence = max(0.0, min(1.0, _num(row.get("confidence"), quality)))
    confidence = min(0.98, min(intake_confidence, quality) * (0.72 + 0.28 * fresh))
    if verification == "reported":
        confidence = min(confidence, 0.75)
    elif verification in {"unverified", "inference"}:
        confidence = min(confidence, 0.35)

    explicit_expiry = _dt(row.get("expires_at"))
    stale_after = explicit_expiry or observed + timedelta(days=half_life * 2.0)
    status = "stale" if stale_after <= current or fresh < 0.18 else "active"

    fact = str(
        details.get("fact")
        or details.get("verified_fact")
        or details.get("summary")
        or ""
    ).strip()
    inference = str(details.get("inference") or "").strip()
    body = fact or str(row.get("details") or "").strip() or None
    affected_symbols = intake_metadata.get("affected_symbols") or details.get("affected_symbols") or []
    if not isinstance(affected_symbols, list):
        affected_symbols = [affected_symbols]
    normalized_symbols = list(
        dict.fromkeys(
            value
            for value in [symbol, *(str(item or "").upper().strip() for item in affected_symbols)]
            if value
        )
    )
    metadata = {
        **details,
        **intake_metadata,
        "intelligence_event_id": source_id,
        "event_key": event_key,
        "verification_status": verification,
        "intake_confidence": round(intake_confidence, 6),
        "ingest_count": max(1, int(_num(row.get("ingest_count"), 1))),
        "affected_symbols": normalized_symbols,
        "fact": fact,
        "inference": inference,
        "ranking_eligible": verification in {"verified", "corroborated", "reported"},
        "execution_impact": "NONE",
    }
    return {
        "source_key": f"intel:{event_key}",
        "provider": provider,
        "category": category,
        "symbol": symbol,
        "title": str(row.get("title") or "Untitled intelligence").strip(),
        "body": body,
        "source_ref": str(row.get("source_url") or "").strip() or f"intelligence_events:{source_id}",
        "observed_at": observed,
        "source_quality": round(quality, 6),
        "freshness_score": round(fresh, 6),
        "confidence": round(max(0.0, confidence), 6),
        "stale_after": stale_after,
        "status": status,
        "metadata": metadata,
        "execution_impact": "NONE",
    }


def episode_from_row(row: dict[str, Any], *, now: datetime | None = None) -> dict[str, Any] | None:
    trade_id = str(row.get("trade_id") or "").strip()
    signal_id = str(row.get("entry_signal_id") or "").strip()
    feature_snapshot = _json_obj(row.get("feature_snapshot"))
    if not trade_id or not signal_id or not feature_snapshot:
        return None

    entry_price = _num(row.get("entry_price"))
    quantity = abs(_num(row.get("quantity")))
    basis = entry_price * quantity if entry_price > 0 and quantity > 0 else 0.0
    net_pnl = _num(row.get("net_pnl"))
    return_pct = (net_pnl / basis) * 100.0 if basis > 0 else None
    entry_time = _dt(row.get("entry_time"))
    exit_time = _dt(row.get("exit_time"))
    exit_price = _num(row.get("exit_price"))
    gross_pnl = _num(row.get("gross_pnl"), net_pnl + max(0.0, _num(row.get("fees"))))
    holding_seconds = (
        max(0.0, (exit_time - entry_time).total_seconds())
        if entry_time is not None and exit_time is not None
        else None
    )
    fresh = freshness_score(exit_time, now=now, half_life_days=45.0)
    confidence = min(0.99, 0.82 + fresh * 0.17)
    strategy = _normalized_strategy_identity(row.get("strategy"))
    regime = str(row.get("regime") or "unknown")
    market = str(row.get("market") or "unknown")
    symbol = str(row.get("symbol") or "unknown").upper()
    outcome = "positive" if net_pnl > 0 else "negative" if net_pnl < 0 else "flat"

    return {
        "episode_key": f"trade:{trade_id}",
        "trade_id": trade_id,
        "market": market,
        "symbol": symbol,
        "strategy": strategy,
        "regime": regime,
        "entry_time": entry_time,
        "exit_time": exit_time,
        "net_pnl": net_pnl,
        "fees": max(0.0, _num(row.get("fees"))),
        "return_pct": return_pct,
        "mfe_pct": None if row.get("mfe_pct") is None else _num(row.get("mfe_pct")),
        "mae_pct": None if row.get("mae_pct") is None else _num(row.get("mae_pct")),
        "provenance_status": "exact",
        "source_quality": 1.0,
        "freshness_score": fresh,
        "confidence": round(confidence, 6),
        "feature_snapshot": feature_snapshot,
        "outcome_snapshot": {
            "outcome": outcome,
            "gross_pnl": gross_pnl,
            "net_pnl": net_pnl,
            "fees": max(0.0, _num(row.get("fees"))),
            "entry_price": entry_price,
            "exit_price": exit_price if exit_price > 0 else None,
            "holding_seconds": holding_seconds,
            "mfe_pct": None if row.get("mfe_pct") is None else _num(row.get("mfe_pct")),
            "mae_pct": None if row.get("mae_pct") is None else _num(row.get("mae_pct")),
            "entry_signal_id": signal_id,
            "entry_decision_id": row.get("entry_decision_id"),
            "entry_forecast_id": row.get("entry_forecast_id"),
            "entry_quote_id": row.get("entry_quote_id"),
            "excursion_sample_count": int(_num(row.get("excursion_sample_count"), 0)),
        },
        "tags": [
            f"market:{_slug(market)}",
            f"symbol:{_slug(symbol)}",
            f"strategy:{_slug(strategy)}",
            f"regime:{_slug(regime)}",
            f"outcome:{outcome}",
        ],
    }


def regime_summary(rows: Iterable[dict[str, Any]]) -> dict[str, Any]:
    records = list(rows)
    count = len(records)
    if not count:
        return {
            "samples": 0,
            "expectancy": None,
            "win_rate": None,
            "net_pnl": 0.0,
            "fees": 0.0,
            "polarity": "insufficient",
            "freshness_score": 0.0,
            "confidence": 0.0,
        }
    pnls = [_num(row.get("net_pnl")) for row in records]
    expectancy = sum(pnls) / count
    wins = sum(1 for pnl in pnls if pnl > 0)
    freshness = sum(_num(row.get("freshness_score"), 0.0) for row in records) / count
    if count < MIN_MATURE_SAMPLES:
        polarity = "insufficient"
    elif expectancy > 0:
        polarity = "positive"
    elif expectancy < 0:
        polarity = "negative"
    else:
        polarity = "mixed"
    sample_confidence = min(1.0, count / float(max(MIN_MATURE_SAMPLES * 3, 1)))
    confidence = min(0.99, (0.58 + 0.40 * sample_confidence) * (0.75 + 0.25 * freshness))
    return {
        "samples": count,
        "expectancy": round(expectancy, 8),
        "win_rate": round(wins / count, 6),
        "net_pnl": round(sum(pnls), 8),
        "fees": round(sum(max(0.0, _num(row.get("fees"))) for row in records), 8),
        "polarity": polarity,
        "freshness_score": round(freshness, 6),
        "confidence": round(confidence, 6),
    }


def research_priority(samples: int, *, contradictory: bool = False, mature_mixed: bool = False) -> float:
    sample_gap = max(0, MIN_MATURE_SAMPLES - max(0, int(samples)))
    base = min(70.0, 20.0 + sample_gap * (50.0 / max(MIN_MATURE_SAMPLES, 1)))
    if contradictory:
        base = max(base, 92.0)
    elif mature_mixed:
        base = max(base, 78.0)
    return round(min(100.0, base), 2)


def _upsert_link(
    conn: Any,
    *,
    source_key: str,
    target_key: str,
    relation: str,
    weight: float,
    confidence: float,
    observed_at: datetime | None = None,
    metadata: dict[str, Any] | None = None,
) -> None:
    observed = observed_at or datetime.now(timezone.utc)
    conn.execute(
        """
        INSERT INTO oracle_brain_links(
            source_key,target_key,relation,weight,evidence_count,confidence,
            first_observed_at,last_observed_at,metadata,execution_impact
        )
        VALUES (%s,%s,%s,%s,1,%s,%s,%s,%s::jsonb,'NONE')
        ON CONFLICT (source_key,target_key,relation) DO UPDATE SET
            weight = (
                oracle_brain_links.weight * oracle_brain_links.evidence_count + EXCLUDED.weight
            ) / (oracle_brain_links.evidence_count + 1),
            evidence_count = oracle_brain_links.evidence_count + 1,
            confidence = LEAST(
                0.99,
                GREATEST(oracle_brain_links.confidence, EXCLUDED.confidence)
                + (1.0 - GREATEST(oracle_brain_links.confidence, EXCLUDED.confidence)) * 0.08
            ),
            last_observed_at = GREATEST(oracle_brain_links.last_observed_at, EXCLUDED.last_observed_at),
            metadata = oracle_brain_links.metadata || EXCLUDED.metadata
        """,
        (
            source_key,
            target_key,
            relation,
            max(-1.0, min(1.0, float(weight))),
            max(0.0, min(0.99, float(confidence))),
            observed,
            observed,
            json.dumps(metadata or {}),
        ),
    )


def _sync_intelligence_sources(conn: Any, *, limit: int = _SOURCE_BATCH) -> int:
    state = conn.execute(
        "SELECT last_source_id,last_sync_at FROM oracle_brain_learning_state WHERE pipeline_key='intelligence' AND market='global'"
    ).fetchone() or {}
    last_id = int(_num(state.get("last_source_id"), 0))
    previous_sync = _dt(state.get("last_sync_at")) or datetime(1970, 1, 1, tzinfo=timezone.utc)
    sync_started = datetime.now(timezone.utc)
    batch_limit = max(1, int(limit))
    new_rows = list(
        conn.execute(
            """
            SELECT id,event_key,category,provider,symbol,title,details,event_time,created_at,
                   source_url,verification_status,confidence,expires_at,metadata,ingest_count,
                   execution_impact
            FROM intelligence_events
            WHERE id > %s
            ORDER BY id ASC
            LIMIT %s
            """,
            (last_id, batch_limit),
        ).fetchall()
    )
    updated_rows = list(
        conn.execute(
            """
            SELECT id,event_key,category,provider,symbol,title,details,event_time,created_at,
                   source_url,verification_status,confidence,expires_at,metadata,ingest_count,
                   execution_impact,updated_at
            FROM intelligence_events
            WHERE id <= %s
              AND NULLIF(updated_at,'')::timestamptz > %s
              AND NULLIF(updated_at,'')::timestamptz <= %s
            ORDER BY NULLIF(updated_at,'')::timestamptz ASC,id ASC
            LIMIT %s
            """,
            (last_id, previous_sync, sync_started, batch_limit),
        ).fetchall()
    )
    rows = [*new_rows, *updated_rows]
    if not rows:
        conn.execute(
            """
            INSERT INTO oracle_brain_learning_state(pipeline_key,market,last_sync_at,last_result)
            VALUES ('intelligence','global',%s,%s::jsonb)
            ON CONFLICT (pipeline_key,market) DO UPDATE SET
                last_sync_at=EXCLUDED.last_sync_at,last_result=EXCLUDED.last_result
            """,
            (sync_started, json.dumps({"inserted": 0, "updated": 0})),
        )
        return 0

    inserted = 0
    max_id = last_id
    for row in rows:
        source_id = int(_num(row.get("id"), 0))
        max_id = max(max_id, source_id)
        source = intelligence_source_from_row(dict(row))
        provider = source["provider"]
        category = source["category"]
        symbol = source["symbol"]
        observed = source["observed_at"]
        quality = source["source_quality"]
        freshness = source["freshness_score"]
        confidence = source["confidence"]
        stale_after = source["stale_after"]
        source_key = source["source_key"]
        result = conn.execute(
            """
            INSERT INTO oracle_brain_sources(
                source_key,source_type,provider,category,symbol,title,body,source_ref,
                observed_at,source_quality,freshness_score,confidence,stale_after,status,
                metadata,execution_impact
            )
            VALUES (%s,'intelligence_event',%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb,'NONE')
            ON CONFLICT (source_key) DO UPDATE SET
                provider=EXCLUDED.provider,
                category=EXCLUDED.category,
                symbol=COALESCE(EXCLUDED.symbol,oracle_brain_sources.symbol),
                title=EXCLUDED.title,
                body=EXCLUDED.body,
                source_ref=EXCLUDED.source_ref,
                observed_at=EXCLUDED.observed_at,
                source_quality=EXCLUDED.source_quality,
                freshness_score=EXCLUDED.freshness_score,
                confidence=EXCLUDED.confidence,
                stale_after=EXCLUDED.stale_after,
                status=CASE WHEN oracle_brain_sources.status='retired' THEN 'retired' ELSE EXCLUDED.status END,
                metadata=oracle_brain_sources.metadata || EXCLUDED.metadata
            """,
            (
                source_key,
                provider,
                category,
                symbol,
                source["title"],
                source["body"],
                source["source_ref"],
                observed,
                quality,
                freshness,
                confidence,
                stale_after,
                source["status"],
                json.dumps(source["metadata"]),
            ),
        )
        inserted += max(0, int(getattr(result, "rowcount", 1) or 0))
        source_node = f"source:{source_key}"
        category_node = f"category:{_slug(category)}"
        _upsert_link(
            conn,
            source_key=source_node,
            target_key=category_node,
            relation="classified_as",
            weight=0.0,
            confidence=confidence,
            observed_at=observed,
            metadata={"provider": provider},
        )
        linked_symbols = list(source["metadata"].get("affected_symbols") or [])
        for linked_symbol in linked_symbols:
            _upsert_link(
                conn,
                source_key=source_node,
                target_key=f"symbol:{_slug(linked_symbol)}",
                relation="about",
                weight=0.0,
                confidence=confidence,
                observed_at=observed,
                metadata={"category": category},
            )
        _upsert_link(
            conn,
            source_key=f"provider:{_slug(provider)}",
            target_key=category_node,
            relation="supplies",
            weight=0.0,
            confidence=quality,
            observed_at=observed,
        )

    update_backlog_possible = len(updated_rows) >= batch_limit
    if update_backlog_possible:
        processed_updates = [_dt(row.get("updated_at")) for row in updated_rows]
        sync_watermark = max(
            [previous_sync, *(value for value in processed_updates if value is not None)]
        )
    else:
        sync_watermark = sync_started
    conn.execute(
        """
        INSERT INTO oracle_brain_learning_state(pipeline_key,market,last_source_id,last_sync_at,last_result)
        VALUES ('intelligence','global',%s,%s,%s::jsonb)
        ON CONFLICT (pipeline_key,market) DO UPDATE SET
            last_source_id=GREATEST(COALESCE(oracle_brain_learning_state.last_source_id,0),EXCLUDED.last_source_id),
            last_sync_at=EXCLUDED.last_sync_at,last_result=EXCLUDED.last_result
        """,
        (
            max_id,
            sync_watermark,
            json.dumps(
                {
                    "inserted": inserted,
                    "upserted": inserted,
                    "new": len(new_rows),
                    "updated": len(updated_rows),
                    "update_backlog_possible": update_backlog_possible,
                    "last_source_id": max_id,
                }
            ),
        ),
    )
    return inserted


def _sync_curated_crypto_history(conn: Any) -> int:
    """Persist deterministic crypto history as durable context, never as a price signal."""
    from crypto_history_memory import CATALOG_VERSION, EVENTS

    inserted = 0
    for event in EVENTS:
        observed = _dt(f"{event.event_date}T00:00:00+00:00")
        source_key = f"crypto_history:{event.event_id}"
        existing_source = conn.execute(
            "SELECT 1 FROM oracle_brain_sources WHERE source_key=%s LIMIT 1",
            (source_key,),
        ).fetchone()
        result = conn.execute(
            """
            INSERT INTO oracle_brain_sources(
                source_key,source_type,provider,category,symbol,title,body,source_ref,
                observed_at,source_quality,freshness_score,confidence,stale_after,status,
                metadata,execution_impact
            )
            VALUES (%s,'curated_history','primary-source catalog',%s,%s,%s,%s,%s,%s,0.94,1.0,0.94,NULL,'active',%s::jsonb,'NONE')
            ON CONFLICT (source_key) DO UPDATE SET
                title=EXCLUDED.title,
                body=EXCLUDED.body,
                source_ref=EXCLUDED.source_ref,
                metadata=EXCLUDED.metadata,
                source_quality=EXCLUDED.source_quality,
                freshness_score=1.0,
                confidence=EXCLUDED.confidence,
                status=CASE WHEN oracle_brain_sources.status='retired' THEN 'retired' ELSE 'active' END
            """,
            (
                source_key,
                event.category,
                event.assets[0] if event.assets else None,
                event.title,
                event.context,
                event.primary_source,
                observed,
                json.dumps(
                    {
                        "catalog_version": CATALOG_VERSION,
                        "event_id": event.event_id,
                        "assets": list(event.assets),
                        "tags": list(event.tags),
                        "durable_lesson": event.durable_lesson,
                        "context_only": True,
                        "influences_decision": False,
                    }
                ),
            ),
        )
        if not existing_source:
            inserted += max(0, int(getattr(result, "rowcount", 1) or 0))
            event_node = f"source:{source_key}"
            _upsert_link(
                conn,
                source_key=event_node,
                target_key=f"category:{_slug(event.category)}",
                relation="classified_as",
                weight=0.0,
                confidence=0.94,
                observed_at=observed,
                metadata={"context_only": True},
            )
            for asset in event.assets:
                _upsert_link(
                    conn,
                    source_key=event_node,
                    target_key=f"symbol:{_slug(asset)}",
                    relation="historical_context_for",
                    weight=0.0,
                    confidence=0.94,
                    observed_at=observed,
                    metadata={"context_only": True},
                )
    return inserted


def _sync_trade_episodes(conn: Any, market: str, *, limit: int = _EPISODE_BATCH) -> tuple[int, int, int]:
    rows = list(
        conn.execute(
            """
            SELECT m.trade_id,m.market,m.symbol,m.strategy,m.regime,m.entry_time,m.exit_time,
                   m.entry_price,m.exit_price,m.net_pnl,m.fees,m.mfe_pct,m.mae_pct,
                   m.excursion_sample_count,
                   t.quantity,t.entry_signal_id,t.entry_decision_id,t.entry_forecast_id,
                   t.entry_quote_id,t.feature_snapshot
            FROM paper_regime_trade_metrics m
            LEFT JOIN trade_ledger t ON t.trade_id=m.trade_id
            WHERE m.market=%s AND m.exit_time IS NOT NULL
            ORDER BY m.exit_time DESC
            LIMIT %s
            """,
            (market, max(1, int(limit))),
        ).fetchall()
    )
    inserted = 0
    new_episodes = 0
    skipped_provenance = 0
    latest_exit: datetime | None = None
    now = datetime.now(timezone.utc)
    for row in rows:
        episode = episode_from_row(dict(row), now=now)
        if episode is None:
            skipped_provenance += 1
            continue
        latest_exit = max(latest_exit or episode["exit_time"], episode["exit_time"]) if episode["exit_time"] else latest_exit
        existing_episode = conn.execute(
            "SELECT 1 FROM oracle_brain_episodes WHERE episode_key=%s LIMIT 1",
            (episode["episode_key"],),
        ).fetchone()
        conn.execute(
            """
            INSERT INTO oracle_brain_episodes(
                episode_key,trade_id,market,symbol,strategy,regime,entry_time,exit_time,
                net_pnl,fees,return_pct,mfe_pct,mae_pct,provenance_status,
                source_quality,freshness_score,confidence,feature_snapshot,outcome_snapshot,
                tags,execution_impact
            )
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,'exact',%s,%s,%s,%s::jsonb,%s::jsonb,%s::jsonb,'NONE')
            ON CONFLICT (episode_key) DO UPDATE SET
                strategy=EXCLUDED.strategy,
                regime=EXCLUDED.regime,
                net_pnl=EXCLUDED.net_pnl,
                fees=EXCLUDED.fees,
                return_pct=EXCLUDED.return_pct,
                mfe_pct=EXCLUDED.mfe_pct,
                mae_pct=EXCLUDED.mae_pct,
                freshness_score=EXCLUDED.freshness_score,
                confidence=EXCLUDED.confidence,
                outcome_snapshot=EXCLUDED.outcome_snapshot,
                tags=EXCLUDED.tags
            """,
            (
                episode["episode_key"],
                episode["trade_id"],
                episode["market"],
                episode["symbol"],
                episode["strategy"],
                episode["regime"],
                episode["entry_time"],
                episode["exit_time"],
                episode["net_pnl"],
                episode["fees"],
                episode["return_pct"],
                episode["mfe_pct"],
                episode["mae_pct"],
                episode["source_quality"],
                episode["freshness_score"],
                episode["confidence"],
                json.dumps(episode["feature_snapshot"]),
                json.dumps(episode["outcome_snapshot"]),
                json.dumps(episode["tags"]),
            ),
        )
        inserted += 1
        if not existing_episode:
            new_episodes += 1
            context_key = f"cohort:{_slug(market)}:{_slug(episode['strategy'])}:{_slug(episode['regime'])}"
            outcome = str(episode["outcome_snapshot"]["outcome"])
            observed = episode["exit_time"] or now
            _upsert_link(
                conn,
                source_key=f"strategy:{_slug(episode['strategy'])}",
                target_key=f"regime:{_slug(episode['regime'])}",
                relation="observed_in",
                weight=0.0,
                confidence=episode["confidence"],
                observed_at=observed,
                metadata={"market": market},
            )
            _upsert_link(
                conn,
                source_key=context_key,
                target_key=f"outcome:{outcome}",
                relation="produced",
                weight=1.0 if outcome == "positive" else -1.0 if outcome == "negative" else 0.0,
                confidence=episode["confidence"],
                observed_at=observed,
                metadata={"trade_id": episode["trade_id"]},
            )
            _upsert_link(
                conn,
                source_key=f"symbol:{_slug(episode['symbol'])}",
                target_key=f"regime:{_slug(episode['regime'])}",
                relation="experienced",
                weight=0.0,
                confidence=episode["confidence"],
                observed_at=observed,
                metadata={"strategy": episode["strategy"]},
            )

    conn.execute(
        """
        INSERT INTO oracle_brain_learning_state(pipeline_key,market,last_episode_exit_at,last_sync_at,last_result)
        VALUES ('episodes',%s,%s,NOW(),%s::jsonb)
        ON CONFLICT (pipeline_key,market) DO UPDATE SET
            last_episode_exit_at=GREATEST(oracle_brain_learning_state.last_episode_exit_at,EXCLUDED.last_episode_exit_at),
            last_sync_at=EXCLUDED.last_sync_at,last_result=EXCLUDED.last_result
        """,
        (
            market,
            latest_exit,
            json.dumps({
                "episodes_seen": inserted,
                "new_exact_episodes": new_episodes,
                "skipped_missing_exact_provenance": skipped_provenance,
            }),
        ),
    )
    return inserted, skipped_provenance, new_episodes


def _insert_regime_lesson(conn: Any, market: str, strategy: str, regime: str, summary: dict[str, Any]) -> int | None:
    polarity = str(summary["polarity"])
    if polarity not in {"positive", "negative"}:
        return None
    subject_key = f"regime:{_slug(market)}:{_slug(strategy)}:{_slug(regime)}"
    brain_key = f"evidence.{subject_key}"
    active = conn.execute(
        """
        SELECT id,metadata,created_at FROM oracle_brain_entries
        WHERE brain_key=%s AND status='active'
        ORDER BY id DESC LIMIT 1
        """,
        (brain_key,),
    ).fetchone() or {}
    prior_meta = _json_obj(active.get("metadata"))
    prior_samples = int(_num(prior_meta.get("samples"), 0))
    prior_polarity = str(prior_meta.get("claim_polarity") or "")
    sample_growth = int(summary["samples"]) - prior_samples
    meaningful_growth = sample_growth >= max(10, int(max(prior_samples, 1) * 0.25))
    polarity_flip = bool(prior_polarity and prior_polarity != polarity)

    if active and not polarity_flip and not meaningful_growth:
        return int(active["id"])

    prior_id = int(active["id"]) if active.get("id") is not None else None
    if prior_id is not None:
        conn.execute(
            "UPDATE oracle_brain_entries SET status='superseded' WHERE id=%s AND status='active'",
            (prior_id,),
        )

    title = f"{strategy} / {regime}: mature {polarity} paper evidence"
    body = (
        f"{summary['samples']} exact-provenance paper outcomes in {market} have "
        f"post-cost expectancy {summary['expectancy']:+.6f}, win rate {summary['win_rate']:.1%}, "
        f"net P&L {summary['net_pnl']:+.4f}, and fees {summary['fees']:.4f}. "
        "This is research evidence only; it does not grant execution or promotion authority."
    )
    metadata = {
        "subject_key": subject_key,
        "claim_polarity": polarity,
        "samples": summary["samples"],
        "expectancy": summary["expectancy"],
        "win_rate": summary["win_rate"],
        "net_pnl": summary["net_pnl"],
        "fees": summary["fees"],
        "freshness_score": summary["freshness_score"],
        "market": market,
        "strategy": strategy,
        "regime": regime,
        "source_quality": 1.0,
    }
    inserted = conn.execute(
        """
        INSERT INTO oracle_brain_entries(
            brain_key,category,title,body,evidence_type,evidence_ref,confidence,status,
            execution_impact,supersedes_id,metadata,created_at
        )
        VALUES (%s,'evidence',%s,%s,'paper_outcome','oracle_brain_episodes',%s,'active',
                'NONE',%s,%s::jsonb,NOW())
        RETURNING id
        """,
        (brain_key, title, body, summary["confidence"], prior_id, json.dumps(metadata)),
    ).fetchone() or {}
    current_id = int(inserted["id"]) if inserted.get("id") is not None else None

    if polarity_flip and prior_id is not None and current_id is not None:
        contradiction_key = f"flip:{subject_key}:{prior_id}:{current_id}"
        conn.execute(
            """
            INSERT INTO oracle_brain_contradictions(
                contradiction_key,subject_key,prior_entry_id,current_entry_id,
                prior_polarity,current_polarity,reason,severity,status,metadata,execution_impact
            )
            VALUES (%s,%s,%s,%s,%s,%s,%s,'high','active',%s::jsonb,'NONE')
            ON CONFLICT (contradiction_key) DO NOTHING
            """,
            (
                contradiction_key,
                subject_key,
                prior_id,
                current_id,
                prior_polarity,
                polarity,
                "Mature exact-provenance paper evidence changed polarity as new outcomes accumulated.",
                json.dumps({"samples": summary["samples"], "expectancy": summary["expectancy"]}),
            ),
        )
    return current_id


def _sync_regime_lessons_and_queue(conn: Any, market: str) -> tuple[int, int]:
    rows = list(
        conn.execute(
            """
            SELECT strategy,regime,net_pnl,fees,freshness_score
            FROM oracle_brain_episodes
            WHERE market=%s AND provenance_status='exact'
            ORDER BY exit_time DESC
            LIMIT 3000
            """,
            (market,),
        ).fetchall()
    )
    grouped: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for row in rows:
        key = (_normalized_strategy_identity(row.get("strategy")), str(row.get("regime") or "unknown"))
        grouped.setdefault(key, []).append(dict(row))

    lessons = 0
    queued = 0
    for (strategy, regime), cohort in grouped.items():
        summary = regime_summary(cohort)
        subject_key = f"regime:{_slug(market)}:{_slug(strategy)}:{_slug(regime)}"
        contradiction = conn.execute(
            """
            SELECT 1 FROM oracle_brain_contradictions
            WHERE subject_key=%s AND status='active'
            LIMIT 1
            """,
            (subject_key,),
        ).fetchone()
        if summary["polarity"] in {"positive", "negative"}:
            before = conn.execute(
                "SELECT id FROM oracle_brain_entries WHERE brain_key=%s AND status='active' LIMIT 1",
                (f"evidence.{subject_key}",),
            ).fetchone()
            after_id = _insert_regime_lesson(conn, market, strategy, regime, summary)
            if after_id is not None and (not before or int(before.get("id") or 0) != after_id):
                lessons += 1
            if not contradiction:
                contradiction = conn.execute(
                    """
                    SELECT 1 FROM oracle_brain_contradictions
                    WHERE subject_key=%s AND status='active'
                    LIMIT 1
                    """,
                    (subject_key,),
                ).fetchone()

        mature_mixed = summary["samples"] >= MIN_MATURE_SAMPLES and summary["polarity"] == "mixed"
        needs_more = summary["samples"] < MIN_MATURE_SAMPLES
        if needs_more or mature_mixed or contradiction:
            priority = research_priority(
                int(summary["samples"]),
                contradictory=bool(contradiction),
                mature_mixed=mature_mixed,
            )
            topic_key = f"cohort:{_slug(market)}:{_slug(strategy)}:{_slug(regime)}"
            reason = (
                "Active contradiction requires new forward evidence."
                if contradiction
                else "Mature evidence is mixed and needs discriminating features."
                if mature_mixed
                else f"Needs {max(0, MIN_MATURE_SAMPLES-int(summary['samples']))} more exact-provenance outcomes for mature evidence."
            )
            conn.execute(
                """
                INSERT INTO oracle_brain_research_queue(
                    topic_key,topic,market,strategy,regime,priority,reason,evidence,status,
                    created_at,updated_at,execution_impact
                )
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s::jsonb,'queued',NOW(),NOW(),'NONE')
                ON CONFLICT (topic_key) DO UPDATE SET
                    priority=EXCLUDED.priority,
                    reason=EXCLUDED.reason,
                    evidence=EXCLUDED.evidence,
                    status=CASE
                        WHEN oracle_brain_research_queue.status='retired' THEN 'retired'
                        ELSE 'queued'
                    END,
                    updated_at=NOW()
                """,
                (
                    topic_key,
                    f"{strategy} / {regime}",
                    market,
                    strategy,
                    regime,
                    priority,
                    reason,
                    json.dumps(summary),
                ),
            )
            queued += 1
        else:
            conn.execute(
                """
                UPDATE oracle_brain_research_queue
                SET status='resolved',updated_at=NOW()
                WHERE topic_key=%s AND status IN ('queued','in_review')
                """,
                (f"cohort:{_slug(market)}:{_slug(strategy)}:{_slug(regime)}",),
            )
    return lessons, queued


def _refresh_source_freshness(conn: Any) -> int:
    rows = list(
        conn.execute(
            """
            SELECT source_key,category,observed_at,source_quality,confidence,stale_after,status,metadata
            FROM oracle_brain_sources
            WHERE source_type='intelligence_event'
              AND status IN ('active','stale')
            ORDER BY observed_at DESC NULLS LAST
            LIMIT 2000
            """
        ).fetchall()
    )
    now = datetime.now(timezone.utc)
    changed = 0
    for row in rows:
        half_life = source_half_life_days(row.get("category"))
        score = freshness_score(row.get("observed_at"), now=now, half_life_days=half_life)
        stale_after = _dt(row.get("stale_after"))
        status = "stale" if score < 0.18 or (stale_after is not None and stale_after <= now) else "active"
        metadata = _json_obj(row.get("metadata"))
        verification = str(metadata.get("verification_status") or "reported").lower()
        quality = max(0.0, min(1.0, _num(row.get("source_quality"), 0.0)))
        intake_confidence = max(
            0.0,
            min(1.0, _num(metadata.get("intake_confidence"), row.get("confidence") or quality)),
        )
        confidence = min(0.98, min(intake_confidence, quality) * (0.72 + 0.28 * score))
        if verification == "reported":
            confidence = min(confidence, 0.75)
        elif verification in {"unverified", "inference"}:
            confidence = min(confidence, 0.35)
        conn.execute(
            """
            UPDATE oracle_brain_sources
            SET freshness_score=%s,
                confidence=%s,
                status=CASE WHEN status IN ('retired','superseded') THEN status ELSE %s END
            WHERE source_key=%s
            """,
            (score, confidence, status, row["source_key"]),
        )
        changed += 1
    return changed


def sync_brain_learning(market: str, *, source_limit: int = _SOURCE_BATCH, episode_limit: int = _EPISODE_BATCH) -> dict[str, Any]:
    """Consume canonical Oracle evidence into research-only durable memory.

    The learner has no return path into order approval, sizing, broker submission,
    or live-money controls. It is allowed to run only while the research safety
    boundary remains paper-only and live submission is disarmed.
    """
    from oracle_brain import runtime_safety_state

    safety = runtime_safety_state()
    if not safety["safe_research_boundary"]:
        return {
            "status": "skipped",
            "reason": "research safety boundary is not paper-only/disarmed",
            "market": market,
            "execution_impact": "NONE",
        }

    normalized_market = "cash" if str(market or "").lower() == "stock" else str(market or "").lower()
    if normalized_market not in {"cash", "crypto"}:
        raise ValueError("market must be cash or crypto")

    from database import connect

    with connect() as conn:
        from oracle_observation_bus import sync_observations

        try:
            observation_sync = sync_observations(conn)
        except Exception as exc:
            # Observation telemetry must never block the established Brain learner.
            observation_sync = {
                "status": "degraded",
                "inserted": 0,
                "by_source": {},
                "error": str(exc)[:240],
                "execution_impact": "NONE",
            }
        sources = _sync_intelligence_sources(conn, limit=source_limit) if normalized_market == "cash" else 0
        curated_history = _sync_curated_crypto_history(conn) if normalized_market == "crypto" else 0
        episodes, skipped, new_episodes = _sync_trade_episodes(conn, normalized_market, limit=episode_limit)
        lessons, queued = _sync_regime_lessons_and_queue(conn, normalized_market)
        refreshed = _refresh_source_freshness(conn) if normalized_market == "cash" else 0
        try:
            from oracle_learning_validation import sync_learning_validation
            validation = sync_learning_validation(conn, normalized_market)
        except Exception as exc:
            validation = {"status": "degraded", "error": str(exc)[:240], "execution_impact": "NONE"}
        try:
            from oracle_advanced_learning import sync_advanced_learning
            advanced_learning = sync_advanced_learning(conn, normalized_market)
        except Exception as exc:
            advanced_learning = {"status": "degraded", "error": str(exc)[:240], "execution_impact": "NONE"}
        result = {
            "status": "ok",
            "market": normalized_market,
            "observation_sync_status": observation_sync["status"],
            "observations_ingested": observation_sync["inserted"],
            "observation_sources": observation_sync["by_source"],
            "sources_ingested": sources,
            "curated_history_ingested": curated_history,
            "episodes_processed": episodes,
            "new_exact_episodes": new_episodes,
            "episodes_skipped_missing_exact_provenance": skipped,
            "lessons_updated": lessons,
            "research_topics_queued": queued,
            "source_freshness_refreshed": refreshed,
            "validation": validation,
            "advanced_learning": advanced_learning,
            "execution_impact": "NONE",
        }
        conn.execute(
            """
            INSERT INTO oracle_brain_learning_state(pipeline_key,market,last_sync_at,last_result)
            VALUES ('brain_v2',%s,NOW(),%s::jsonb)
            ON CONFLICT (pipeline_key,market) DO UPDATE SET
                last_sync_at=EXCLUDED.last_sync_at,last_result=EXCLUDED.last_result
            """,
            (normalized_market, json.dumps(result)),
        )
        return result


def retrieve_brain_context(
    *,
    market: str,
    symbol: str | None = None,
    strategy: str | None = None,
    regime: str | None = None,
    limit: int = 12,
) -> dict[str, Any]:
    """Read the most relevant durable research memory without affecting execution."""
    from database import connect

    clauses = ["market=%s", "provenance_status='exact'"]
    params: list[Any] = [market]
    if symbol:
        clauses.append("symbol=%s")
        params.append(str(symbol).upper())
    if strategy:
        clauses.append("strategy=%s")
        params.append(strategy)
    if regime:
        clauses.append("regime=%s")
        params.append(regime)
    params.append(max(1, int(limit)))

    with connect() as conn:
        episodes = list(
            conn.execute(
                f"""
                SELECT episode_key,trade_id,market,symbol,strategy,regime,exit_time,
                       net_pnl,fees,return_pct,mfe_pct,mae_pct,confidence,freshness_score
                FROM oracle_brain_episodes
                WHERE {' AND '.join(clauses)}
                ORDER BY confidence DESC,freshness_score DESC,exit_time DESC
                LIMIT %s
                """,
                tuple(params),
            ).fetchall()
        )
        source_params: list[Any] = []
        source_where = ["status='active'"]
        if symbol:
            source_where.append("symbol=%s")
            source_params.append(str(symbol).upper())
        source_params.append(max(1, int(limit)))
        sources = list(
            conn.execute(
                f"""
                SELECT source_key,provider,category,symbol,title,observed_at,
                       source_quality,freshness_score,confidence
                FROM oracle_brain_sources
                WHERE {' AND '.join(source_where)}
                ORDER BY confidence DESC,freshness_score DESC,observed_at DESC
                LIMIT %s
                """,
                tuple(source_params),
            ).fetchall()
        )
    return {
        "market": market,
        "symbol": symbol,
        "strategy": strategy,
        "regime": regime,
        "episodes": episodes,
        "sources": sources,
        "execution_impact": "NONE",
    }


__all__ = [
    "BRAIN_LEARNING_SYNC_SECONDS",
    "MIN_MATURE_SAMPLES",
    "episode_from_row",
    "freshness_score",
    "intelligence_source_from_row",
    "research_priority",
    "regime_summary",
    "retrieve_brain_context",
    "source_quality",
    "sync_brain_learning",
]
