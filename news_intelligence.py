from __future__ import annotations

import logging
import os
import re
import threading
import time
from dataclasses import dataclass, field

import requests
import xml.etree.ElementTree as ET

from api_manager import get_api_settings
from cache import get as cache_get, make_key, set_value
from config import (
    ENABLE_NEWS,
    NEWS_CACHE_TTL_SECONDS,
    NEWS_NEGATIVE_CACHE_TTL_SECONDS,
    NEWSAPI_MAX_REQUESTS_PER_12H,
    NEWSAPI_RATE_LIMIT_COOLDOWN_SECONDS,
)

log = logging.getLogger("news-intelligence")
NEWSAPI_URL = "https://newsapi.org/v2/everything"
GEMINI_API_BASE = "https://generativelanguage.googleapis.com/v1beta/models"
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-3.7-flash").strip() or "gemini-3.7-flash"
GOOGLE_GROUNDED_INTELLIGENCE_ENABLED = os.getenv("GOOGLE_GROUNDED_INTELLIGENCE_ENABLED", "true").lower() == "true"
GEMINI_MAX_REQUESTS_PER_12H = max(1, int(os.getenv("GEMINI_MAX_REQUESTS_PER_12H", "8")))
GEMINI_RATE_LIMIT_COOLDOWN_SECONDS = max(60, int(os.getenv("GEMINI_RATE_LIMIT_COOLDOWN_SECONDS", "900")))
GEMINI_MIN_REQUEST_INTERVAL_SECONDS = max(0, int(os.getenv("GEMINI_MIN_REQUEST_INTERVAL_SECONDS", "120")))
GEMINI_TIMEOUT_SECONDS = max(5, min(60, int(os.getenv("GEMINI_TIMEOUT_SECONDS", "20"))))
GEMINI_MAX_GROUNDED_SOURCES = max(3, min(12, int(os.getenv("GEMINI_MAX_GROUNDED_SOURCES", "8"))))

POSITIVE = {"beat","growth","surge","rally","gain","record","approval","partnership","profit","upgrade","bullish","strong","breakthrough","outperform","expansion","rebound","soar"}
NEGATIVE = {"miss","loss","fall","drop","lawsuit","probe","fraud","downgrade","bearish","weak","risk","ban","hack","recession","warning","decline","slump","cut","investigation"}


@dataclass
class NewsResult:
    sentiment: float
    headlines: list[str]
    source: str
    message: str = ""
    citations: list[str] = field(default_factory=list)


_lock = threading.RLock()
_window_started = time.time()
_window_requests = 0
_cooldown_until = 0.0
_last_cooldown_log = 0.0
_gemini_window_started = time.time()
_gemini_window_requests = 0
_gemini_cooldown_until = 0.0
_gemini_last_cooldown_log = 0.0
_gemini_last_request_at = 0.0


def _score(text: str) -> float:
    words = set(re.findall(r"[a-zA-Z]+", str(text).lower()))
    pos = len(words & POSITIVE)
    neg = len(words & NEGATIVE)
    total = pos + neg
    return 0.0 if not total else (pos - neg) / total


def _average_sentiment(headlines: list[str]) -> float:
    return 0.0 if not headlines else sum(_score(x) for x in headlines) / len(headlines)


def _get_newsapi_key() -> str:
    for name in ("NEWSAPI_API_KEY", "NEWS_API_KEY", "NEWSAPI_KEY"):
        value = os.getenv(name, "").strip()
        if value:
            return value
    try:
        settings = get_api_settings()
        value = str(settings.get("NEWS_API_KEY", "") or "").strip()
        if value:
            return value
    except Exception as exc:
        log.debug("Could not read NewsAPI key from api_manager: %s", exc)
    return ""


def _get_gemini_key() -> str:
    for name in ("GEMINI_API_KEY", "GOOGLE_API_KEY"):
        value = os.getenv(name, "").strip()
        if value:
            return value
    try:
        settings = get_api_settings()
        value = str(settings.get("GEMINI_API_KEY", "") or "").strip()
        if value:
            return value
    except Exception as exc:
        log.debug("Could not read Gemini key from api_manager: %s", exc)
    return ""


def _normalize_query(query: str) -> str:
    return " ".join(str(query).strip().lower().split())


def _budget_allows_request() -> bool:
    global _window_started, _window_requests
    now = time.time()
    with _lock:
        if now < _cooldown_until:
            return False
        if now - _window_started >= 12 * 3600:
            _window_started = now
            _window_requests = 0
        if _window_requests >= NEWSAPI_MAX_REQUESTS_PER_12H:
            return False
        _window_requests += 1
        return True


def _gemini_budget_allows_request() -> bool:
    global _gemini_window_started, _gemini_window_requests, _gemini_last_request_at
    now = time.time()
    with _lock:
        if now < _gemini_cooldown_until:
            return False
        if now - _gemini_window_started >= 12 * 3600:
            _gemini_window_started = now
            _gemini_window_requests = 0
        if _gemini_window_requests >= GEMINI_MAX_REQUESTS_PER_12H:
            return False
        if now - _gemini_last_request_at < GEMINI_MIN_REQUEST_INTERVAL_SECONDS:
            return False
        _gemini_window_requests += 1
        _gemini_last_request_at = now
        return True


def _record_gemini_health(status: str, message: str) -> None:
    """Share worker-observed Gemini health with the web dashboard."""
    try:
        from database import connect, utc_now

        with connect() as conn:
            conn.execute(
                """INSERT INTO provider_health(provider,configured,status,message,checked_at)
                   VALUES ('GEMINI_API_KEY',TRUE,%s,%s,%s)
                   ON CONFLICT(provider) DO UPDATE SET
                       configured=EXCLUDED.configured,
                       status=EXCLUDED.status,
                       message=EXCLUDED.message,
                       checked_at=EXCLUDED.checked_at""",
                (status, str(message)[:260], utc_now()),
            )
    except Exception as exc:
        log.debug("Could not persist Gemini health: %s", exc)


def _activate_cooldown(reason: str) -> None:
    global _cooldown_until, _last_cooldown_log
    now = time.time()
    with _lock:
        _cooldown_until = max(_cooldown_until, now + NEWSAPI_RATE_LIMIT_COOLDOWN_SECONDS)
        if now - _last_cooldown_log > 300:
            log.warning("NewsAPI paused for %s seconds after rate limit: %s", NEWSAPI_RATE_LIMIT_COOLDOWN_SECONDS, reason)
            _last_cooldown_log = now


def _activate_gemini_cooldown(reason: str) -> None:
    global _gemini_cooldown_until, _gemini_last_cooldown_log
    now = time.time()
    with _lock:
        _gemini_cooldown_until = max(_gemini_cooldown_until, now + GEMINI_RATE_LIMIT_COOLDOWN_SECONDS)
        if now - _gemini_last_cooldown_log > 300:
            log.warning("Gemini grounded intelligence paused for %s seconds: %s", GEMINI_RATE_LIMIT_COOLDOWN_SECONDS, reason)
            _gemini_last_cooldown_log = now
    _record_gemini_health("rate_limited", "Gemini quota is temporarily exhausted; news fallback remains active.")


def provider_state() -> dict[str, float | int | bool | str]:
    now = time.time()
    with _lock:
        return {
            "newsapi_cooldown_active": now < _cooldown_until,
            "newsapi_cooldown_remaining_seconds": max(0, int(_cooldown_until - now)),
            "newsapi_window_requests": _window_requests,
            "newsapi_window_limit": NEWSAPI_MAX_REQUESTS_PER_12H,
            "google_grounded_enabled": GOOGLE_GROUNDED_INTELLIGENCE_ENABLED,
            "gemini_model": GEMINI_MODEL,
            "gemini_cooldown_active": now < _gemini_cooldown_until,
            "gemini_cooldown_remaining_seconds": max(0, int(_gemini_cooldown_until - now)),
            "gemini_window_requests": _gemini_window_requests,
            "gemini_window_limit": GEMINI_MAX_REQUESTS_PER_12H,
        }


def _fetch_newsapi(query: str, api_key: str) -> NewsResult:
    response = requests.get(
        NEWSAPI_URL,
        params={"q": query, "language": "en", "sortBy": "publishedAt", "pageSize": 8},
        headers={"X-Api-Key": api_key, "Accept": "application/json"},
        timeout=15,
    )
    try:
        payload = response.json()
    except ValueError:
        payload = {}
    if not response.ok:
        code = str(payload.get("code", f"http_{response.status_code}"))
        message = str(payload.get("message", response.text[:200]))
        if response.status_code == 429 or code.lower() in {"ratelimited", "maximumresultsreached"}:
            _activate_cooldown(f"{code}: {message}")
        raise RuntimeError(f"{code}: {message}")
    articles = payload.get("articles", [])
    if not isinstance(articles, list):
        articles = []
    headlines: list[str] = []
    citations: list[str] = []
    for article in articles:
        if not isinstance(article, dict):
            continue
        title = str(article.get("title") or "").strip()
        url = str(article.get("url") or "").strip()
        if title and title != "[Removed]":
            headlines.append(title)
            if url:
                citations.append(url)
    return NewsResult(
        _average_sentiment(headlines),
        headlines,
        "NewsAPI",
        f"NewsAPI returned {len(headlines)} headlines for {query}.",
        citations,
    )


def _grounded_web_sources(payload: dict) -> tuple[list[str], list[str]]:
    candidates = payload.get("candidates") or []
    if not isinstance(candidates, list) or not candidates:
        return [], []
    candidate = candidates[0] if isinstance(candidates[0], dict) else {}
    metadata = candidate.get("groundingMetadata") or candidate.get("grounding_metadata") or {}
    chunks = metadata.get("groundingChunks") or metadata.get("grounding_chunks") or []
    if not isinstance(chunks, list):
        return [], []
    headlines: list[str] = []
    citations: list[str] = []
    seen_titles: set[str] = set()
    seen_urls: set[str] = set()
    for chunk in chunks:
        if not isinstance(chunk, dict):
            continue
        web = chunk.get("web") or {}
        if not isinstance(web, dict):
            continue
        title = " ".join(str(web.get("title") or "").split()).strip()
        uri = str(web.get("uri") or web.get("url") or "").strip()
        if title and title.lower() not in seen_titles:
            seen_titles.add(title.lower())
            headlines.append(title)
        if uri and uri not in seen_urls:
            seen_urls.add(uri)
            citations.append(uri)
        if len(headlines) >= GEMINI_MAX_GROUNDED_SOURCES and len(citations) >= GEMINI_MAX_GROUNDED_SOURCES:
            break
    return headlines[:GEMINI_MAX_GROUNDED_SOURCES], citations[:GEMINI_MAX_GROUNDED_SOURCES]


def _fetch_gemini_grounded(query: str, api_key: str) -> NewsResult:
    url = f"{GEMINI_API_BASE}/{GEMINI_MODEL}:generateContent"
    prompt = (
        "Find the most recent market-moving news and official developments relevant to "
        f"{query}. Prioritize primary sources and reputable financial reporting. Focus on "
        "earnings, filings, regulation, material business events, security incidents, monetary "
        "policy, and macro catalysts. Use Google Search when it improves freshness. Do not invent "
        "facts and do not provide trading instructions."
    )
    response = requests.post(
        url,
        headers={"x-goog-api-key": api_key, "Content-Type": "application/json"},
        json={
            "contents": [{"role": "user", "parts": [{"text": prompt}]}],
            "tools": [{"google_search": {}}],
            "generationConfig": {"temperature": 0.1, "maxOutputTokens": 600},
        },
        timeout=GEMINI_TIMEOUT_SECONDS,
    )
    try:
        payload = response.json()
    except ValueError:
        payload = {}
    if not response.ok:
        error = payload.get("error") if isinstance(payload, dict) else None
        message = str((error or {}).get("message") or response.text[:200]) if isinstance(error, dict) else response.text[:200]
        if response.status_code in {429, 500, 502, 503, 504}:
            _activate_gemini_cooldown(f"http_{response.status_code}: {message}")
        raise RuntimeError(f"http_{response.status_code}: {message}")
    headlines, citations = _grounded_web_sources(payload if isinstance(payload, dict) else {})
    if not headlines or not citations:
        raise RuntimeError("Google Search grounding returned no attributable web sources")
    return NewsResult(
        _average_sentiment(headlines),
        headlines,
        "Google Gemini Grounded Search",
        f"Google grounding returned {len(headlines)} attributable sources for {query}.",
        citations,
    )


def _fetch_google_news(query: str) -> NewsResult:
    encoded = requests.utils.quote(query)
    url = f"https://news.google.com/rss/search?q={encoded}&hl=en-US&gl=US&ceid=US:en"
    response = requests.get(url, timeout=15, headers={"User-Agent": "GARIBALDI-MARKET-ORACLE/22"})
    response.raise_for_status()
    root = ET.fromstring(response.content)
    headlines: list[str] = []
    citations: list[str] = []
    for item in root.findall("./channel/item")[:8]:
        title = item.findtext("title")
        link = item.findtext("link")
        if title and title.strip():
            headlines.append(title.strip())
            if link and link.strip():
                citations.append(link.strip())
    return NewsResult(
        _average_sentiment(headlines),
        headlines,
        "Google News RSS",
        f"Google News returned {len(headlines)} headlines for {query}.",
        citations,
    )


def get_news_sentiment(query: str, *, priority: bool = True) -> NewsResult:
    clean = " ".join(str(query).strip().split())
    if not ENABLE_NEWS:
        return NewsResult(0.0, [], "Disabled", "News collection is disabled.")
    if not clean:
        return NewsResult(0.0, [], "Unavailable", "No news query was provided.")
    if not priority:
        return NewsResult(0.0, [], "Deferred", "News deferred until the symbol reaches the promoted candidate group.")

    key = make_key("symbol_news", _normalize_query(clean))
    cached = cache_get(key)
    if cached is not None:
        return cached

    gemini_key = _get_gemini_key()
    if GOOGLE_GROUNDED_INTELLIGENCE_ENABLED and gemini_key and _gemini_budget_allows_request():
        try:
            result = _fetch_gemini_grounded(clean, gemini_key)
            _record_gemini_health("healthy", "Gemini grounded intelligence responded successfully.")
            set_value(key, result, NEWS_CACHE_TTL_SECONDS)
            log.info(
                "News ready | provider=Google Gemini Grounded Search | query=%s | sources=%d",
                clean,
                len(result.citations),
            )
            return result
        except Exception as exc:
            log.info("Google grounded intelligence unavailable for %s; using provider fallback (%s)", clean, exc)

    api_key = _get_newsapi_key()
    if api_key and _budget_allows_request():
        try:
            result = _fetch_newsapi(clean, api_key)
            set_value(key, result, NEWS_CACHE_TTL_SECONDS if result.headlines else NEWS_NEGATIVE_CACHE_TTL_SECONDS)
            log.info("News ready | provider=NewsAPI | query=%s | headlines=%d", clean, len(result.headlines))
            return result
        except Exception as exc:
            log.info("NewsAPI unavailable for %s; using RSS fallback (%s)", clean, exc)

    try:
        result = _fetch_google_news(clean)
        set_value(key, result, NEWS_CACHE_TTL_SECONDS if result.headlines else NEWS_NEGATIVE_CACHE_TTL_SECONDS)
        log.info("News ready | provider=Google RSS | query=%s | headlines=%d", clean, len(result.headlines))
        return result
    except Exception as exc:
        result = NewsResult(0.0, [], "Unavailable", str(exc))
        set_value(key, result, NEWS_NEGATIVE_CACHE_TTL_SECONDS)
        log.warning("All news providers failed for %s: %s", clean, exc)
        return result
