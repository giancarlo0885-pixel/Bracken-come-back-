from __future__ import annotations

"""Canonical market-intelligence intake and bounded Oracle Brain retrieval.

The bridge gives every observation a stable identity, explicit provenance,
freshness, confidence, and affected-market metadata. Retrieved intelligence may
raise surveillance/catalyst priority, but it never creates an order, changes
position size, bypasses Council V3, or grants execution authority.
"""

from datetime import datetime, timezone
import hashlib
import json
import math
import re
from threading import RLock
import time
from typing import Any, Iterable

from database import save_intelligence_event


_HIGH_QUALITY_REPORTING_TERMS = (
    "reuters",
    "associated press",
    "ap news",
    "bloomberg",
    "financial times",
    "wall street journal",
    "wsj",
)
_GLOBAL_CATEGORIES = {
    "macro_policy",
    "monetary_policy",
    "economic calendar",
    "economic",
    "geopolitical alerts",
    "geopolitical",
    "commodities",
    "supply_disruption",
    "crypto_market_structure",
}
_CATEGORY_CONTEXT: dict[str, dict[str, list[str]]] = {
    "macro_policy": {"asset_classes": ["stocks", "crypto", "bonds", "commodities"], "sectors": [], "themes": ["macro"]},
    "ai_technology": {"asset_classes": ["stocks"], "sectors": ["technology", "semiconductors"], "themes": ["ai"]},
    "space_technology": {"asset_classes": ["stocks"], "sectors": ["industrials", "aerospace", "defense"], "themes": ["space"]},
    "quantum_technology": {"asset_classes": ["stocks"], "sectors": ["technology"], "themes": ["quantum"]},
    "commodities": {"asset_classes": ["stocks", "commodities"], "sectors": ["energy", "materials"], "themes": ["commodities"]},
    "crypto_market_structure": {"asset_classes": ["crypto", "stocks"], "sectors": ["financials"], "themes": ["crypto"]},
    "regulatory": {"asset_classes": ["stocks", "crypto"], "sectors": [], "themes": ["regulation"]},
    "supply_disruption": {"asset_classes": ["stocks", "commodities"], "sectors": ["energy", "materials", "industrials"], "themes": ["supply-chain"]},
}
_VERIFICATION_WEIGHTS = {
    "verified": 1.0,
    "corroborated": 0.92,
    "reported": 0.55,
    "unverified": 0.0,
    "inference": 0.0,
}
_CACHE_LOCK = RLock()
_SOURCE_CACHE: tuple[float, list[dict[str, Any]]] = (0.0, [])
_SOURCE_CACHE_SECONDS = 60.0


def _clean(value: Any, *, limit: int = 500) -> str:
    return " ".join(str(value or "").split()).strip()[:limit]


def _slug(value: Any) -> str:
    return re.sub(r"[^a-z0-9]+", "-", _clean(value).lower()).strip("-") or "unknown"


def _number(value: Any, default: float = 0.0) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return number if math.isfinite(number) else default


def _bounded(value: Any, low: float = 0.0, high: float = 1.0, default: float = 0.0) -> float:
    return max(low, min(high, _number(value, default)))


def _json_obj(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return dict(value)
    if isinstance(value, str) and value.strip():
        try:
            decoded = json.loads(value)
        except (TypeError, ValueError):
            return {}
        return dict(decoded) if isinstance(decoded, dict) else {}
    return {}


def _as_list(value: Any) -> list[str]:
    if value is None:
        return []
    values = value if isinstance(value, (list, tuple, set)) else [value]
    out: list[str] = []
    seen: set[str] = set()
    for item in values:
        text = _clean(item, limit=120)
        key = text.lower()
        if text and key not in seen:
            seen.add(key)
            out.append(text)
    return out


def _first(record: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        value = record.get(key)
        if value not in (None, "", [], {}):
            return value
    return None


def _official_provider(provider: str) -> bool:
    lowered = provider.lower()
    return (
        ".gov" in lowered
        or any(term in lowered for term in ("federal reserve", "u.s. treasury", "official"))
        or bool(re.search(r"\b(?:sec|cftc|bls|bea|nasa)\b", lowered))
    )


def _record_identity(record: dict[str, Any]) -> Any:
    explicit = _first(record, "event_key", "event_id", "id", "uuid")
    if explicit is not None:
        return explicit
    identity_keys = (
        "etf",
        "symbol",
        "ticker",
        "representative",
        "name",
        "transaction",
        "side",
        "transaction_code",
        "transaction_date",
        "filing_date",
        "filed_date",
        "event",
        "country",
        "date",
        "quarter",
        "year",
        "published_at",
        "source_url",
        "url",
        "link",
    )
    parts = [
        f"{key}:{_clean(record.get(key), limit=500).lower()}"
        for key in identity_keys
        if _clean(record.get(key), limit=500)
    ]
    if not parts:
        return None
    return "identity:" + hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()[:40]


def _record_title(category: str, record: dict[str, Any]) -> str:
    explicit = _first(record, "title", "event", "headline")
    if explicit:
        return _clean(explicit, limit=500)
    symbol = _clean(_first(record, "symbol", "ticker"), limit=32).upper()
    name = _clean(_first(record, "name", "company"), limit=160)
    etf = _clean(record.get("etf"), limit=32).upper()
    representative = _clean(record.get("representative"), limit=160)
    transaction = _clean(_first(record, "transaction", "side"), limit=32)
    event_date = _clean(_first(record, "date", "transaction_date", "filing_date", "filed_date"), limit=40)
    if etf and symbol:
        return _clean(f"{etf} holding: {symbol}" + (f" — {name}" if name else ""), limit=500)
    if representative and symbol:
        return _clean(f"{representative} {transaction or 'disclosure'}: {symbol}", limit=500)
    if symbol and transaction:
        return _clean(f"{symbol} insider {transaction}" + (f" — {name}" if name else ""), limit=500)
    if symbol and ("earning" in category.lower() or event_date):
        return _clean(f"{symbol} event" + (f" — {event_date}" if event_date else ""), limit=500)
    return _clean(name or record.get("summary") or category, limit=500)


def _provider_confidence(provider: str, verification_status: str) -> float:
    lowered = provider.lower()
    if _official_provider(provider):
        base = 0.96
    elif any(token in lowered for token in _HIGH_QUALITY_REPORTING_TERMS):
        base = 0.84
    elif any(token in lowered for token in ("finnhub", "eodhd", "nasdaq", "newsapi", "google")):
        base = 0.72
    else:
        base = 0.58
    if verification_status == "corroborated":
        return max(base, 0.88)
    if verification_status in {"unverified", "inference"}:
        return min(base, 0.35)
    return base


def _verification_status(provider: str, record: dict[str, Any]) -> str:
    explicit = _clean(record.get("verification_status"), limit=24).lower()
    if explicit in _VERIFICATION_WEIGHTS:
        return explicit
    if record.get("verified") is True:
        return "verified"
    provider_lower = provider.lower()
    if _official_provider(provider_lower):
        return "verified"
    return "reported" if provider.strip() else "unverified"


def _impact_score(record: dict[str, Any]) -> float:
    explicit = _first(record, "impact_score", "consequence_score", "catalyst_score", "score")
    if explicit is not None:
        return round(_bounded(explicit, 0.0, 100.0, 55.0), 2)
    importance = _clean(_first(record, "importance", "impact"), limit=32).lower()
    if importance in {"critical", "very high", "high", "3"}:
        return 85.0
    if importance in {"medium", "moderate", "2"}:
        return 65.0
    if importance in {"low", "1"}:
        return 45.0
    return 55.0


def _stable_event_key(
    *,
    provider: str,
    category: str,
    title: str,
    symbol: str | None,
    event_time: str | None,
    source_url: str | None,
    external_id: Any = None,
) -> str:
    if _clean(external_id, limit=200):
        return f"{_slug(provider)}:{_clean(external_id, limit=200)}"
    raw = "|".join(
        _clean(value, limit=2000).lower()
        for value in (provider, category, symbol, title, event_time, source_url)
    )
    return "bridge:" + hashlib.sha256(raw.encode("utf-8")).hexdigest()[:40]


def normalize_monitor_record(
    category: str,
    provider: str,
    record: dict[str, Any],
) -> dict[str, Any]:
    """Convert provider-specific records into Oracle's canonical fact envelope."""
    item = dict(record or {})
    clean_category = _clean(category or item.get("category") or "uncategorized", limit=120)
    category_key = _slug(clean_category).replace("-", "_")
    canonical_context = _CATEGORY_CONTEXT.get(category_key, {})
    clean_provider = _clean(provider or item.get("provider") or "unknown", limit=160)
    title = _record_title(clean_category, item)
    symbol = _clean(_first(item, "symbol", "ticker", "primary_symbol"), limit=32).upper() or None
    event_time = _clean(
        _first(
            item,
            "published_at",
            "event_time",
            "date",
            "datetime",
            "time",
            "transaction_date",
            "filing_date",
            "filed_date",
        ),
        limit=80,
    ) or None
    source_url = _clean(_first(item, "source_url", "url", "link"), limit=2000) or None
    expires_at = _clean(item.get("expires_at"), limit=80) or None
    verification = _verification_status(clean_provider, item)
    explicit_confidence = item.get("confidence")
    confidence = (
        _bounded(explicit_confidence, 0.0, 1.0)
        if explicit_confidence is not None
        else _provider_confidence(clean_provider, verification)
    )
    official_provider = _official_provider(clean_provider)
    if verification in {"unverified", "inference"} or (
        not source_url and verification == "verified" and not official_provider
    ):
        verification = "unverified"
        confidence = min(confidence, 0.35)

    affected_symbols = _as_list(_first(item, "affected_symbols", "symbol_candidates"))
    if symbol and symbol not in {value.upper() for value in affected_symbols}:
        affected_symbols.insert(0, symbol)
    affected_symbols = [value.upper() for value in affected_symbols]
    sectors = _as_list(_first(item, "affected_sectors", "sectors", "sector"))
    asset_classes = _as_list(_first(item, "asset_classes", "markets", "market"))
    themes = _as_list(_first(item, "themes", "tags"))
    for value in canonical_context.get("sectors", []):
        if value.lower() not in {item.lower() for item in sectors}:
            sectors.append(value)
    for value in canonical_context.get("asset_classes", []):
        if value.lower() not in {item.lower() for item in asset_classes}:
            asset_classes.append(value)
    for value in canonical_context.get("themes", []):
        if value.lower() not in {item.lower() for item in themes}:
            themes.append(value)

    metadata = {
        "affected_symbols": affected_symbols,
        "sectors": sectors,
        "asset_classes": asset_classes,
        "themes": themes,
        "impact_score": _impact_score(item),
        "direction": _clean(item.get("direction"), limit=24).lower() or "unknown",
        "verification_status": verification,
        "transmission_channels": _as_list(item.get("transmission_channels")),
        "catalysts": _as_list(item.get("catalysts")),
        "risks": _as_list(item.get("risks")),
        "narrative_traps": _as_list(item.get("narrative_traps")),
        "market_wide": bool(item.get("market_wide") or category_key in {
            _slug(value).replace("-", "_") for value in _GLOBAL_CATEGORIES
        }),
        "execution_impact": "NONE",
    }
    event_key = _stable_event_key(
        provider=clean_provider,
        category=clean_category,
        title=title,
        symbol=symbol,
        event_time=event_time,
        source_url=source_url,
        external_id=_record_identity(item),
    )
    details = {
        **item,
        "fact": _clean(_first(item, "verified_fact", "fact", "details", "summary"), limit=4000),
        "inference": _clean(item.get("inference"), limit=4000),
        **metadata,
    }
    return {
        "event_key": event_key,
        "category": clean_category,
        "provider": clean_provider,
        "symbol": symbol,
        "title": title,
        "details": details,
        "event_time": event_time,
        "source_url": source_url,
        "verification_status": verification,
        "confidence": round(confidence, 6),
        "expires_at": expires_at,
        "metadata": metadata,
        "execution_impact": "NONE",
    }


def ingest_monitor_record(category: str, provider: str, record: dict[str, Any]) -> dict[str, Any]:
    event = normalize_monitor_record(category, provider, record)
    persisted = save_intelligence_event(
        event["category"],
        event["provider"],
        event["title"],
        event["details"],
        event["symbol"],
        event["event_time"],
        event_key=event["event_key"],
        source_url=event["source_url"],
        verification_status=event["verification_status"],
        confidence=event["confidence"],
        expires_at=event["expires_at"],
        metadata=event["metadata"],
    )
    return {**event, "persistence": persisted}


def ingest_market_brief(packet: dict[str, Any], *, provider: str = "Monday Market Intelligence") -> dict[str, Any]:
    """Ingest a structured external brief without mixing fact and inference.

    The function is the stable contract for trusted automation or future API
    adapters. Source-free claims are retained as unverified research and carry
    zero ranking influence.
    """
    developments = packet.get("developments") or packet.get("events") or []
    if not isinstance(developments, list):
        raise ValueError("market-intelligence packet must contain a developments list")
    brief_id = _clean(packet.get("brief_id") or packet.get("generated_at") or datetime.now(timezone.utc).date(), limit=120)
    persisted: list[dict[str, Any]] = []
    for index, raw in enumerate(developments):
        if not isinstance(raw, dict):
            continue
        sources = raw.get("sources") if isinstance(raw.get("sources"), list) else []
        primary_source = next((source for source in sources if isinstance(source, dict)), {})
        source_urls = {
            _clean(source.get("url"), limit=2000)
            for source in sources
            if isinstance(source, dict) and _clean(source.get("url"), limit=2000)
        }
        record = {
            **raw,
            "event_id": f"brief:{brief_id}:{index}",
            "source_url": raw.get("source_url") or primary_source.get("url"),
            "published_at": raw.get("published_at") or primary_source.get("published_at") or packet.get("generated_at"),
            "verified_fact": raw.get("verified_fact") or raw.get("fact"),
            "verification_status": (
                "corroborated"
                if len(source_urls) >= 2
                else raw.get("verification_status") or ("reported" if source_urls else "unverified")
            ),
            "confidence": raw.get("confidence", 0.88 if len(source_urls) >= 2 else 0.72 if source_urls else 0.30),
            "brief_id": brief_id,
            "source_count": len(source_urls),
        }
        normalized = normalize_monitor_record(
            str(raw.get("category") or raw.get("theme") or "weekly_market_intelligence"),
            str(primary_source.get("provider") or raw.get("provider") or provider),
            record,
        )
        persisted.append(
            ingest_monitor_record(normalized["category"], normalized["provider"], normalized["details"])
        )
    return {
        "brief_id": brief_id,
        "developments_received": len(developments),
        "events_persisted": len(persisted),
        "event_keys": [item["event_key"] for item in persisted],
        "execution_impact": "NONE",
    }


def _market_asset_class(market: str) -> str:
    return "crypto" if _clean(market).lower() == "crypto" else "stocks"


def _source_relevance(source: dict[str, Any], *, symbol: str, market: str, sector: str) -> float:
    metadata = _json_obj(source.get("metadata"))
    normalized_symbol = symbol.upper()
    source_symbol = _clean(source.get("symbol"), limit=32).upper()
    affected_symbols = {item.upper() for item in _as_list(metadata.get("affected_symbols"))}
    if normalized_symbol and (source_symbol == normalized_symbol or normalized_symbol in affected_symbols):
        return 1.0
    normalized_sector = sector.lower()
    sectors = {item.lower() for item in _as_list(metadata.get("sectors"))}
    if normalized_sector and normalized_sector in sectors:
        return 0.76
    category = _slug(source.get("category")).replace("-", "_")
    global_categories = {_slug(value).replace("-", "_") for value in _GLOBAL_CATEGORIES}
    asset_classes = {item.lower() for item in _as_list(metadata.get("asset_classes"))}
    if (metadata.get("market_wide") is True or category in global_categories) and (
        not asset_classes or _market_asset_class(market) in asset_classes
    ):
        return 0.32
    return 0.0


def brain_context_from_sources(
    sources: Iterable[dict[str, Any]],
    *,
    symbol: str,
    market: str = "cash",
    sector: str = "",
    limit: int = 8,
) -> dict[str, Any]:
    """Build an auditable catalyst context; never infer a BUY/SELL direction."""
    relevant: list[dict[str, Any]] = []
    for raw in sources:
        source = dict(raw or {})
        relevance = _source_relevance(source, symbol=symbol, market=market, sector=sector)
        if relevance <= 0:
            continue
        metadata = _json_obj(source.get("metadata"))
        verification = _clean(
            metadata.get("verification_status") or source.get("verification_status") or "reported",
            limit=24,
        ).lower()
        verification_weight = _VERIFICATION_WEIGHTS.get(verification, 0.0)
        if metadata.get("ranking_eligible") is False:
            verification_weight = 0.0
        confidence = _bounded(source.get("confidence"), default=0.0)
        freshness = _bounded(source.get("freshness_score"), default=0.0)
        quality = _bounded(source.get("source_quality"), default=0.0)
        impact = _bounded(metadata.get("impact_score"), 0.0, 100.0, 55.0)
        influence = impact * confidence * freshness * quality * relevance * verification_weight
        relevant.append(
            {
                "source_key": source.get("source_key"),
                "provider": source.get("provider"),
                "category": source.get("category"),
                "symbol": source.get("symbol"),
                "title": source.get("title"),
                "source_ref": source.get("source_ref"),
                "observed_at": source.get("observed_at"),
                "verification_status": verification,
                "confidence": round(confidence, 6),
                "freshness_score": round(freshness, 6),
                "source_quality": round(quality, 6),
                "relevance": round(relevance, 4),
                "impact_score": round(impact, 2),
                "ranking_influence": round(influence, 2),
                "metadata": metadata,
            }
        )
    relevant.sort(
        key=lambda item: (
            item["ranking_influence"],
            item["confidence"],
            item["freshness_score"],
            str(item.get("observed_at") or ""),
        ),
        reverse=True,
    )
    selected = relevant[: max(1, int(limit))]
    eligible = [item for item in selected if item["ranking_influence"] > 0]
    strongest = max((item["ranking_influence"] for item in eligible), default=0.0)
    corroboration_bonus = min(12.0, max(0, len(eligible) - 1) * 2.0)
    catalyst_score = round(min(92.0, strongest + corroboration_bonus), 2)
    directions = {
        _clean(item["metadata"].get("direction"), limit=24).lower()
        for item in eligible
        if _clean(item["metadata"].get("direction"), limit=24).lower() in {"positive", "negative"}
    }
    needs_research = any(item["verification_status"] in {"unverified", "inference"} for item in selected) or len(directions) > 1
    return {
        "symbol": symbol.upper(),
        "market": market,
        "sector": sector,
        "catalyst_score": catalyst_score,
        "source_count": len(selected),
        "ranking_eligible_sources": len(eligible),
        "headlines": [str(item.get("title") or "") for item in selected if item.get("title")],
        "citations": [str(item.get("source_ref") or "") for item in selected if str(item.get("source_ref") or "").startswith("http")],
        "sources": selected,
        "needs_research": needs_research,
        "directional_trade_signal": "NONE",
        "ranking_impact": "BOUNDED_CATALYST_ONLY",
        "execution_impact": "NONE",
    }


def _recent_brain_sources() -> list[dict[str, Any]]:
    global _SOURCE_CACHE
    now = time.monotonic()
    with _CACHE_LOCK:
        cached_at, cached = _SOURCE_CACHE
        if cached and now - cached_at < _SOURCE_CACHE_SECONDS:
            return list(cached)
    try:
        from database import connect

        with connect() as conn:
            rows = conn.execute(
                """
                SELECT source_key,provider,category,symbol,title,source_ref,observed_at,
                       source_quality,freshness_score,confidence,status,metadata
                FROM oracle_brain_sources
                WHERE status='active' AND freshness_score >= 0.18 AND confidence >= 0.30
                ORDER BY observed_at DESC NULLS LAST, confidence DESC
                LIMIT 320
                """
            ).fetchall()
        sources = [dict(row) for row in rows]
    except Exception:
        return []
    with _CACHE_LOCK:
        _SOURCE_CACHE = (now, sources)
    return list(sources)


def brain_context_for_signal(
    symbol: str,
    *,
    market: str = "cash",
    sector: str = "",
    limit: int = 8,
    sources: Iterable[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    records = list(sources) if sources is not None else _recent_brain_sources()
    return brain_context_from_sources(records, symbol=symbol, market=market, sector=sector, limit=limit)


__all__ = [
    "brain_context_for_signal",
    "brain_context_from_sources",
    "ingest_market_brief",
    "ingest_monitor_record",
    "normalize_monitor_record",
]
