from __future__ import annotations

"""Event-first opportunity discovery for GARIBALDI MARKET ORACLE.

Unlike the symbol-first news pass, this scanner starts with global market events.
It can retain high-value research opportunities even before a broker-tradable
symbol exists, then conservatively promote only explicitly identified symbols
that pass the existing verified-quote identity gate.
"""

from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
import hashlib
import json
import logging
import os
import re
from typing import Any
import xml.etree.ElementTree as ET

import requests

from database import connect, utc_now

log = logging.getLogger("event-opportunity-scanner")

EVENT_OPPORTUNITY_SCANNER_ENABLED = os.getenv("EVENT_OPPORTUNITY_SCANNER_ENABLED", "true").lower() == "true"
EVENT_OPPORTUNITY_SCAN_SECONDS = max(300, int(os.getenv("EVENT_OPPORTUNITY_SCAN_SECONDS", "900")))
EVENT_OPPORTUNITY_QUERIES_PER_CYCLE = max(1, min(8, int(os.getenv("EVENT_OPPORTUNITY_QUERIES_PER_CYCLE", "4"))))
EVENT_OPPORTUNITY_MAX_PER_QUERY = max(3, min(20, int(os.getenv("EVENT_OPPORTUNITY_MAX_PER_QUERY", "10"))))
EVENT_OPPORTUNITY_RETENTION_DAYS = max(2, int(os.getenv("EVENT_OPPORTUNITY_RETENTION_DAYS", "10")))
EVENT_OPPORTUNITY_PROMOTION_SCORE = max(50.0, min(100.0, float(os.getenv("EVENT_OPPORTUNITY_PROMOTION_SCORE", "72"))))
EVENT_OPPORTUNITY_CONTEXT_SCORE = max(40.0, min(100.0, float(os.getenv("EVENT_OPPORTUNITY_CONTEXT_SCORE", "58"))))
EVENT_OPPORTUNITY_TIMEOUT_SECONDS = max(5, min(30, int(os.getenv("EVENT_OPPORTUNITY_TIMEOUT_SECONDS", "15"))))

_QUERY_GROUPS: tuple[tuple[str, str], ...] = (
    ("IPO_LISTING", 'IPO OR "initial public offering" OR "public offer" OR "stock exchange listing" OR "share sale" when:2d'),
    ("M&A", 'acquisition OR merger OR takeover OR buyout OR "strategic acquisition" when:2d'),
    ("REGULATORY", 'approval OR regulator OR FDA OR SEC OR license OR tariff OR sanctions "market" when:2d'),
    ("CONTRACT_CAPEX", '"wins contract" OR "awarded contract" OR expansion OR refinery OR factory OR datacenter when:2d'),
    ("EARNINGS_GUIDANCE", '"raises guidance" OR "cuts guidance" OR "earnings beat" OR "earnings miss" OR outlook shares when:2d'),
    ("CAPITAL_RAISE", '"capital raise" OR "secondary offering" OR "rights issue" OR "bond sale" OR financing shares when:2d'),
    ("SUPPLY_DISRUPTION", 'shutdown OR outage OR strike OR "export ban" OR disruption OR shortage commodities market when:2d'),
    ("CRYPTO_MARKET_STRUCTURE", 'crypto ETF approval OR token listing OR exchange listing OR stablecoin law OR crypto regulation when:2d'),
)

_CATEGORY_PATTERNS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("IPO_LISTING", (r"\bipo\b", r"initial public offering", r"public offer", r"stock exchange listing", r"listing on (?:the )?(?:nyse|nasdaq|ngx|lse|tsx|asx)", r"share sale")),
    ("M&A", (r"\bacquisition\b", r"\bacquire[sd]?\b", r"\bmerger\b", r"\btakeover\b", r"\bbuyout\b")),
    ("REGULATORY", (r"fda approval", r"sec approval", r"regulator(?:y)? approval", r"license granted", r"court ruling", r"\btariff\b", r"\bsanctions?\b")),
    ("CONTRACT_CAPEX", (r"wins? (?:a )?contract", r"awarded (?:a )?contract", r"\bexpansion\b", r"new (?:plant|factory|refinery|datacenter|data center)", r"capacity expansion")),
    ("EARNINGS_GUIDANCE", (r"raises? guidance", r"cuts? guidance", r"earnings beat", r"earnings miss", r"raises? outlook", r"cuts? outlook")),
    ("CAPITAL_RAISE", (r"capital raise", r"secondary offering", r"rights issue", r"bond sale", r"private placement", r"follow-on offering")),
    ("SUPPLY_DISRUPTION", (r"\bshutdown\b", r"\boutage\b", r"\bstrike\b", r"export ban", r"supply disruption", r"production halt", r"pipeline disruption")),
    ("CRYPTO_MARKET_STRUCTURE", (r"crypto etf", r"spot (?:bitcoin|ether|ethereum) etf", r"token listing", r"exchange listing", r"stablecoin law", r"crypto regulation")),
)

_MARKET_TERMS = (
    "shares", "stock", "equity", "investor", "market", "exchange", "ipo", "listing",
    "revenue", "profit", "guidance", "contract", "approval", "refinery", "oil", "gas",
    "crypto", "bitcoin", "ethereum", "etf", "bond", "acquisition", "merger", "capital",
)
_HIGH_AUTHORITY_SOURCES = (
    "reuters", "bloomberg", "financial times", "wall street journal", "wsj", "associated press",
    "ap news", "cnbc", "sec", "fda", "nasdaq", "nyse", "ngx", "london stock exchange",
)
_RUMOR_TERMS = ("rumor", "rumour", "unconfirmed", "sources say", "reportedly considering", "may consider")
_DEFINITIVE_TERMS = (
    "approved", "signed", "completed", "awarded", "wins", "won", "opens", "open on",
    "set to", "will launch", "will list", "files for", "priced at", "raises guidance",
)
_SCALE_TERMS = ("largest", "biggest", "record", "major", "billion", "bn", "mega", "landmark")

_EXPLICIT_TICKER_PATTERNS = (
    re.compile(r"\b(?:NASDAQ|NYSE|AMEX|NYSEARCA)\s*[:\-]\s*([A-Z][A-Z0-9.\-]{0,9})\b"),
    re.compile(r"\$([A-Z]{1,6})\b"),
)


@dataclass(frozen=True)
class EventOpportunity:
    event_id: str
    category: str
    title: str
    entity_name: str
    primary_symbol: str
    symbol_candidates: list[str]
    source: str
    url: str
    published_at: str
    detected_at: str
    score: float
    source_quality: float
    research_only: bool
    query_category: str
    factors: list[str]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _ensure_tables() -> None:
    with connect() as conn:
        conn.execute(
            """CREATE TABLE IF NOT EXISTS event_opportunity_candidates (
                event_id TEXT PRIMARY KEY,
                category TEXT NOT NULL,
                title TEXT NOT NULL,
                entity_name TEXT,
                primary_symbol TEXT,
                symbol_candidates JSONB NOT NULL DEFAULT '[]'::jsonb,
                source TEXT,
                url TEXT,
                published_at TEXT,
                detected_at TEXT NOT NULL,
                score DOUBLE PRECISION NOT NULL,
                source_quality DOUBLE PRECISION NOT NULL DEFAULT 0,
                research_only BOOLEAN NOT NULL DEFAULT TRUE,
                query_category TEXT,
                payload JSONB NOT NULL DEFAULT '{}'::jsonb
            )"""
        )
        conn.execute(
            """CREATE INDEX IF NOT EXISTS idx_event_opportunity_score
               ON event_opportunity_candidates(score DESC, detected_at DESC)"""
        )
        conn.execute(
            """CREATE INDEX IF NOT EXISTS idx_event_opportunity_symbol
               ON event_opportunity_candidates(primary_symbol, score DESC, detected_at DESC)"""
        )
        conn.execute(
            """CREATE TABLE IF NOT EXISTS event_opportunity_scanner_status (
                id INTEGER PRIMARY KEY DEFAULT 1,
                cursor INTEGER NOT NULL DEFAULT 0,
                status TEXT NOT NULL DEFAULT 'waiting',
                message TEXT,
                last_scan_at TEXT,
                updated_at TEXT NOT NULL
            )"""
        )


def _parse_dt(value: Any) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        try:
            parsed = parsedate_to_datetime(text)
        except Exception:
            return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _clean_title_and_source(title: str) -> tuple[str, str]:
    clean = " ".join(str(title or "").split()).strip()
    if " - " not in clean:
        return clean, ""
    body, source = clean.rsplit(" - ", 1)
    if 1 <= len(source) <= 80:
        return body.strip(), source.strip()
    return clean, ""


def _event_id(title: str, url: str = "") -> str:
    normalized = re.sub(r"\W+", " ", str(title or "").lower()).strip()
    stable = normalized or str(url or "").strip().lower()
    return hashlib.sha256(stable.encode("utf-8", errors="ignore")).hexdigest()[:32]


def _source_quality(source: str) -> float:
    lowered = str(source or "").lower()
    if any(name in lowered for name in _HIGH_AUTHORITY_SOURCES):
        return 1.0
    return 0.72 if lowered else 0.60


def _category_for_title(title: str, fallback: str = "EVENT") -> tuple[str, list[str]]:
    lowered = str(title or "").lower()
    for category, patterns in _CATEGORY_PATTERNS:
        matched = [pattern for pattern in patterns if re.search(pattern, lowered, flags=re.IGNORECASE)]
        if matched:
            return category, matched
    return str(fallback or "EVENT").upper(), []


def _extract_symbols(title: str) -> list[str]:
    symbols: list[str] = []
    for pattern in _EXPLICIT_TICKER_PATTERNS:
        for match in pattern.findall(str(title or "")):
            symbol = str(match or "").upper().strip(".- ")
            if symbol and symbol not in symbols:
                symbols.append(symbol)
    return symbols[:4]


def _extract_entity(title: str) -> str:
    body, _ = _clean_title_and_source(title)
    body = re.sub(r"^[\"'‘’“”]+|[\"'‘’“”]+$", "", body).strip()
    split = re.split(
        r"\s+(?:is|are|was|were|will|to|set to|plans? to|files? for|announces?|wins?|gets?|receives?|raises?|cuts?|seeks?|launches?|opens?)\b",
        body,
        maxsplit=1,
        flags=re.IGNORECASE,
    )[0]
    words = split.split()
    candidate = " ".join(words[:8]).strip(" :-—")
    return candidate[:120]


def score_event(
    title: str,
    *,
    published_at: Any = None,
    source: str = "",
    query_category: str = "EVENT",
    now: datetime | None = None,
) -> tuple[float, str, list[str]]:
    """Return a bounded catalyst score plus category and transparent factors."""
    clean, inferred_source = _clean_title_and_source(title)
    source = source or inferred_source
    lowered = clean.lower()
    category, matched_patterns = _category_for_title(clean, query_category)
    base_by_category = {
        "IPO_LISTING": 54.0,
        "M&A": 50.0,
        "REGULATORY": 46.0,
        "CONTRACT_CAPEX": 43.0,
        "SUPPLY_DISRUPTION": 45.0,
        "CRYPTO_MARKET_STRUCTURE": 45.0,
        "CAPITAL_RAISE": 39.0,
        "EARNINGS_GUIDANCE": 36.0,
    }
    score = base_by_category.get(category, 20.0 if matched_patterns else 0.0)
    factors: list[str] = [f"category:{category}"] if score else []

    if any(term in lowered for term in _MARKET_TERMS):
        score += 7.0
        factors.append("market_relevance")
    if any(term in lowered for term in _SCALE_TERMS):
        score += 10.0
        factors.append("large_scale")
    if re.search(r"(?:\$|£|€|₦)\s*\d+(?:\.\d+)?\s*(?:billion|bn|b)\b", lowered, flags=re.IGNORECASE):
        score += 10.0
        factors.append("billion_scale")
    elif re.search(r"(?:\$|£|€|₦)\s*\d+(?:\.\d+)?\s*(?:million|mn|m)\b", lowered, flags=re.IGNORECASE):
        score += 5.0
        factors.append("million_scale")
    if any(term in lowered for term in _DEFINITIVE_TERMS):
        score += 9.0
        factors.append("definitive_event")
    if any(term in lowered for term in _RUMOR_TERMS):
        score -= 14.0
        factors.append("rumor_penalty")

    quality = _source_quality(source)
    score += 8.0 * quality
    factors.append("high_authority_source" if quality >= 0.95 else "standard_source")

    current = now or datetime.now(timezone.utc)
    published = _parse_dt(published_at)
    if published is not None:
        age = max(0.0, (current.astimezone(timezone.utc) - published).total_seconds())
        if age <= 6 * 3600:
            score += 15.0
            factors.append("fresh_6h")
        elif age <= 24 * 3600:
            score += 12.0
            factors.append("fresh_24h")
        elif age <= 72 * 3600:
            score += 8.0
            factors.append("fresh_72h")
        elif age <= 7 * 86400:
            score += 3.0
            factors.append("fresh_7d")
        else:
            score -= 12.0
            factors.append("stale_penalty")

    if not matched_patterns and category == str(query_category or "EVENT").upper():
        # Query membership alone is not enough to create a high-confidence event.
        score = min(score, 44.0)
        factors.append("query_only_cap")

    return round(max(0.0, min(100.0, score)), 2), category, factors


def classify_headline(
    title: str,
    *,
    url: str = "",
    published_at: Any = None,
    query_category: str = "EVENT",
    detected_at: str | None = None,
    now: datetime | None = None,
) -> EventOpportunity | None:
    clean_title, source = _clean_title_and_source(title)
    score, category, factors = score_event(
        clean_title,
        published_at=published_at,
        source=source,
        query_category=query_category,
        now=now,
    )
    if score < 45.0:
        return None
    symbols = _extract_symbols(clean_title)
    primary_symbol = symbols[0] if symbols else ""
    published = _parse_dt(published_at)
    published_iso = published.isoformat() if published is not None else ""
    detected = detected_at or utc_now()
    return EventOpportunity(
        event_id=_event_id(clean_title, url),
        category=category,
        title=clean_title,
        entity_name=_extract_entity(clean_title),
        primary_symbol=primary_symbol,
        symbol_candidates=symbols,
        source=source,
        url=str(url or "").strip(),
        published_at=published_iso,
        detected_at=detected,
        score=score,
        source_quality=_source_quality(source),
        research_only=not bool(primary_symbol),
        query_category=str(query_category or "EVENT").upper(),
        factors=factors,
    )


def _fetch_google_news(query: str) -> list[dict[str, str]]:
    encoded = requests.utils.quote(query)
    url = f"https://news.google.com/rss/search?q={encoded}&hl=en-US&gl=US&ceid=US:en"
    response = requests.get(
        url,
        timeout=EVENT_OPPORTUNITY_TIMEOUT_SECONDS,
        headers={"User-Agent": "GARIBALDI-MARKET-ORACLE/EVENT-RADAR"},
    )
    response.raise_for_status()
    root = ET.fromstring(response.content)
    rows: list[dict[str, str]] = []
    for item in root.findall("./channel/item")[:EVENT_OPPORTUNITY_MAX_PER_QUERY]:
        title = " ".join(str(item.findtext("title") or "").split()).strip()
        link = str(item.findtext("link") or "").strip()
        published = str(item.findtext("pubDate") or "").strip()
        if title:
            rows.append({"title": title, "url": link, "published_at": published})
    return rows


def _persist_event(event: EventOpportunity) -> None:
    payload = event.to_dict()
    with connect() as conn:
        conn.execute(
            """INSERT INTO event_opportunity_candidates
               (event_id,category,title,entity_name,primary_symbol,symbol_candidates,source,url,
                published_at,detected_at,score,source_quality,research_only,query_category,payload)
               VALUES (%s,%s,%s,%s,%s,%s::jsonb,%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb)
               ON CONFLICT(event_id) DO UPDATE SET
                   category=EXCLUDED.category,
                   entity_name=EXCLUDED.entity_name,
                   primary_symbol=CASE WHEN EXCLUDED.primary_symbol <> '' THEN EXCLUDED.primary_symbol ELSE event_opportunity_candidates.primary_symbol END,
                   symbol_candidates=CASE WHEN EXCLUDED.symbol_candidates <> '[]'::jsonb THEN EXCLUDED.symbol_candidates ELSE event_opportunity_candidates.symbol_candidates END,
                   source=CASE WHEN EXCLUDED.source <> '' THEN EXCLUDED.source ELSE event_opportunity_candidates.source END,
                   url=CASE WHEN EXCLUDED.url <> '' THEN EXCLUDED.url ELSE event_opportunity_candidates.url END,
                   published_at=CASE WHEN EXCLUDED.published_at <> '' THEN EXCLUDED.published_at ELSE event_opportunity_candidates.published_at END,
                   detected_at=EXCLUDED.detected_at,
                   score=GREATEST(event_opportunity_candidates.score, EXCLUDED.score),
                   source_quality=GREATEST(event_opportunity_candidates.source_quality, EXCLUDED.source_quality),
                   research_only=event_opportunity_candidates.research_only AND EXCLUDED.research_only,
                   query_category=EXCLUDED.query_category,
                   payload=EXCLUDED.payload""",
            (
                event.event_id,
                event.category,
                event.title,
                event.entity_name,
                event.primary_symbol,
                json.dumps(event.symbol_candidates),
                event.source,
                event.url,
                event.published_at,
                event.detected_at,
                event.score,
                event.source_quality,
                event.research_only,
                event.query_category,
                json.dumps(payload),
            ),
        )


def _status() -> dict[str, Any]:
    with connect() as conn:
        row = conn.execute("SELECT * FROM event_opportunity_scanner_status WHERE id=1").fetchone()
    return dict(row) if row else {}


def _write_status(cursor: int, status: str, message: str, *, scanned: bool = True) -> None:
    now = utc_now()
    with connect() as conn:
        conn.execute(
            """INSERT INTO event_opportunity_scanner_status(id,cursor,status,message,last_scan_at,updated_at)
               VALUES (1,%s,%s,%s,%s,%s)
               ON CONFLICT(id) DO UPDATE SET
                   cursor=EXCLUDED.cursor,
                   status=EXCLUDED.status,
                   message=EXCLUDED.message,
                   last_scan_at=CASE WHEN %s THEN EXCLUDED.last_scan_at ELSE event_opportunity_scanner_status.last_scan_at END,
                   updated_at=EXCLUDED.updated_at""",
            (cursor, status, str(message)[:500], now, now, scanned),
        )


def recent_event_opportunities(limit: int = 40) -> list[dict[str, Any]]:
    _ensure_tables()
    cutoff = (datetime.now(timezone.utc) - timedelta(days=EVENT_OPPORTUNITY_RETENTION_DAYS)).isoformat()
    with connect() as conn:
        rows = conn.execute(
            """SELECT event_id,category,title,entity_name,primary_symbol,symbol_candidates,source,url,
                      published_at,detected_at,score,source_quality,research_only,query_category,payload
               FROM event_opportunity_candidates
               WHERE detected_at >= %s
               ORDER BY score DESC, detected_at DESC
               LIMIT %s""",
            (cutoff, max(1, int(limit))),
        ).fetchall()
    return [dict(row) for row in rows]


def scan_event_opportunities(*, force: bool = False) -> list[dict[str, Any]]:
    _ensure_tables()
    if not EVENT_OPPORTUNITY_SCANNER_ENABLED:
        return recent_event_opportunities()

    status = _status()
    last_scan = _parse_dt(status.get("last_scan_at"))
    now = datetime.now(timezone.utc)
    if not force and last_scan is not None and (now - last_scan).total_seconds() < EVENT_OPPORTUNITY_SCAN_SECONDS:
        return recent_event_opportunities()

    cursor = int(status.get("cursor") or 0) % len(_QUERY_GROUPS)
    selected = [
        _QUERY_GROUPS[(cursor + offset) % len(_QUERY_GROUPS)]
        for offset in range(EVENT_OPPORTUNITY_QUERIES_PER_CYCLE)
    ]
    next_cursor = (cursor + len(selected)) % len(_QUERY_GROUPS)
    saved = 0
    errors: list[str] = []
    for query_category, query in selected:
        try:
            headlines = _fetch_google_news(query)
        except Exception as exc:
            errors.append(f"{query_category}:{type(exc).__name__}")
            log.info("Event discovery query unavailable | category=%s | error=%s", query_category, exc)
            continue
        for article in headlines:
            event = classify_headline(
                article.get("title", ""),
                url=article.get("url", ""),
                published_at=article.get("published_at", ""),
                query_category=query_category,
            )
            if event is None:
                continue
            try:
                _persist_event(event)
                saved += 1
            except Exception as exc:
                errors.append(f"persist:{type(exc).__name__}")
                log.debug("Could not persist event opportunity: %s", exc)

    cutoff = (now - timedelta(days=EVENT_OPPORTUNITY_RETENTION_DAYS)).isoformat()
    try:
        with connect() as conn:
            conn.execute("DELETE FROM event_opportunity_candidates WHERE detected_at < %s", (cutoff,))
    except Exception as exc:
        errors.append(f"cleanup:{type(exc).__name__}")

    status_name = "partial" if errors else "healthy"
    _write_status(next_cursor, status_name, f"saved={saved}; queries={len(selected)}; errors={','.join(errors[:6]) or 'none'}")
    opportunities = recent_event_opportunities()
    hot = [row for row in opportunities if float(row.get("score") or 0.0) >= EVENT_OPPORTUNITY_PROMOTION_SCORE]
    if hot:
        log.info(
            "EVENT OPPORTUNITY RADAR | hot=%s | top=%s | score=%.1f | mode=research-first",
            len(hot),
            str(hot[0].get("title") or "")[:180],
            float(hot[0].get("score") or 0.0),
        )
    return opportunities


def _verified_symbol(symbol: str) -> bool:
    normalized = str(symbol or "").upper().strip()
    if not normalized:
        return False
    try:
        from market_data import get_live_snapshot, snapshot_is_verified

        snapshot = get_live_snapshot(normalized)
        if snapshot is None or not snapshot_is_verified(snapshot, normalized):
            return False
        payload = dict(snapshot.to_quote_payload())
    except Exception:
        return False
    return bool(
        payload.get("quote_verified") is True
        and payload.get("stale") is not True
        and str(payload.get("requested_symbol") or "").upper().strip() == normalized
        and str(payload.get("provider_symbol") or "").upper().strip() == normalized
    )


def active_event_watchlist() -> dict[str, str]:
    """Return only high-score event symbols that are actually quote-verifiable now."""
    opportunities = scan_event_opportunities()
    watchlist: dict[str, str] = {}
    checked: set[str] = set()
    for row in opportunities:
        if float(row.get("score") or 0.0) < EVENT_OPPORTUNITY_PROMOTION_SCORE:
            continue
        symbol = str(row.get("primary_symbol") or "").upper().strip()
        if not symbol or symbol in checked:
            continue
        checked.add(symbol)
        if not _verified_symbol(symbol):
            continue
        watchlist[symbol] = str(row.get("entity_name") or symbol)
        if len(watchlist) >= 12:
            break
    return watchlist


def event_context_for_symbol(symbol: str, *, limit: int = 6) -> dict[str, Any]:
    """Return fresh event evidence for a known symbol without creating a trade signal."""
    normalized = str(symbol or "").upper().strip()
    if not normalized:
        return {"score": 0.0, "headlines": [], "events": []}
    _ensure_tables()
    cutoff = (datetime.now(timezone.utc) - timedelta(days=EVENT_OPPORTUNITY_RETENTION_DAYS)).isoformat()
    with connect() as conn:
        rows = conn.execute(
            """SELECT event_id,category,title,entity_name,primary_symbol,source,url,published_at,
                      detected_at,score,source_quality,research_only,query_category,payload
               FROM event_opportunity_candidates
               WHERE primary_symbol=%s AND detected_at >= %s AND score >= %s
               ORDER BY score DESC, detected_at DESC
               LIMIT %s""",
            (normalized, cutoff, EVENT_OPPORTUNITY_CONTEXT_SCORE, max(1, int(limit))),
        ).fetchall()
    events = [dict(row) for row in rows]
    if not events:
        return {"score": 0.0, "headlines": [], "events": []}
    score = max(float(row.get("score") or 0.0) for row in events)
    return {
        "score": round(score, 2),
        "headlines": [str(row.get("title") or "") for row in events if row.get("title")],
        "events": events,
    }
