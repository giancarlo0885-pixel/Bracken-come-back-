from __future__ import annotations

import logging
import os
import re
import threading
import time
from dataclasses import dataclass

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
POSITIVE = {"beat","growth","surge","rally","gain","record","approval","partnership","profit","upgrade","bullish","strong","breakthrough","outperform","expansion","rebound","soar"}
NEGATIVE = {"miss","loss","fall","drop","lawsuit","probe","fraud","downgrade","bearish","weak","risk","ban","hack","recession","warning","decline","slump","cut","investigation"}

@dataclass
class NewsResult:
    sentiment: float
    headlines: list[str]
    source: str
    message: str = ""

_lock = threading.RLock()
_window_started = time.time()
_window_requests = 0
_cooldown_until = 0.0
_last_cooldown_log = 0.0


def _score(text: str) -> float:
    words=set(re.findall(r"[a-zA-Z]+", str(text).lower()))
    pos=len(words & POSITIVE); neg=len(words & NEGATIVE); total=pos+neg
    return 0.0 if not total else (pos-neg)/total


def _average_sentiment(headlines: list[str]) -> float:
    return 0.0 if not headlines else sum(_score(x) for x in headlines)/len(headlines)


def _get_newsapi_key() -> str:
    for name in ("NEWSAPI_API_KEY","NEWS_API_KEY","NEWSAPI_KEY"):
        value=os.getenv(name,"").strip()
        if value: return value
    try:
        settings=get_api_settings()
        for name in ("NEWSAPI_API_KEY","NEWS_API_KEY","NEWSAPI_KEY"):
            value=str(settings.values.get(name,"")).strip()
            if value: return value
    except Exception as exc:
        log.debug("Could not read NewsAPI key from api_manager: %s", exc)
    return ""


def _normalize_query(query: str) -> str:
    return " ".join(str(query).strip().lower().split())


def _budget_allows_request() -> bool:
    global _window_started, _window_requests
    now=time.time()
    with _lock:
        if now < _cooldown_until:
            return False
        if now-_window_started >= 12*3600:
            _window_started=now; _window_requests=0
        if _window_requests >= NEWSAPI_MAX_REQUESTS_PER_12H:
            return False
        _window_requests += 1
        return True


def _activate_cooldown(reason: str) -> None:
    global _cooldown_until, _last_cooldown_log
    now=time.time()
    with _lock:
        _cooldown_until=max(_cooldown_until, now+NEWSAPI_RATE_LIMIT_COOLDOWN_SECONDS)
        if now-_last_cooldown_log > 300:
            log.warning("NewsAPI paused for %s seconds after rate limit: %s", NEWSAPI_RATE_LIMIT_COOLDOWN_SECONDS, reason)
            _last_cooldown_log=now


def provider_state() -> dict[str, float | int | bool]:
    now=time.time()
    with _lock:
        return {
            "newsapi_cooldown_active": now < _cooldown_until,
            "newsapi_cooldown_remaining_seconds": max(0, int(_cooldown_until-now)),
            "newsapi_window_requests": _window_requests,
            "newsapi_window_limit": NEWSAPI_MAX_REQUESTS_PER_12H,
        }


def _fetch_newsapi(query: str, api_key: str) -> NewsResult:
    response=requests.get(NEWSAPI_URL, params={"q":query,"language":"en","sortBy":"publishedAt","pageSize":8}, headers={"X-Api-Key":api_key,"Accept":"application/json"}, timeout=15)
    try: payload=response.json()
    except ValueError: payload={}
    if not response.ok:
        code=str(payload.get("code", f"http_{response.status_code}"))
        message=str(payload.get("message", response.text[:200]))
        if response.status_code==429 or code.lower() in {"ratelimited","maximumresultsreached"}:
            _activate_cooldown(f"{code}: {message}")
        raise RuntimeError(f"{code}: {message}")
    articles=payload.get("articles",[])
    if not isinstance(articles,list): articles=[]
    headlines=[]
    for article in articles:
        if isinstance(article,dict):
            title=str(article.get("title") or "").strip()
            if title and title!="[Removed]": headlines.append(title)
    return NewsResult(_average_sentiment(headlines), headlines, "NewsAPI", f"NewsAPI returned {len(headlines)} headlines for {query}.")


def _fetch_google_news(query: str) -> NewsResult:
    encoded=requests.utils.quote(query)
    url=f"https://news.google.com/rss/search?q={encoded}&hl=en-US&gl=US&ceid=US:en"
    response=requests.get(url, timeout=15, headers={"User-Agent":"GARIBALDI-MARKET-ORACLE/22"})
    response.raise_for_status()
    root=ET.fromstring(response.content)
    headlines=[]
    for item in root.findall("./channel/item")[:8]:
        title=item.findtext("title")
        if title and title.strip(): headlines.append(title.strip())
    return NewsResult(_average_sentiment(headlines), headlines, "Google News RSS", f"Google News returned {len(headlines)} headlines for {query}.")


def get_news_sentiment(query: str, *, priority: bool = True) -> NewsResult:
    clean=" ".join(str(query).strip().split())
    if not ENABLE_NEWS:
        return NewsResult(0.0,[],"Disabled","News collection is disabled.")
    if not clean:
        return NewsResult(0.0,[],"Unavailable","No news query was provided.")
    if not priority:
        return NewsResult(0.0,[],"Deferred","News deferred until the symbol reaches the promoted candidate group.")

    key=make_key("symbol_news", _normalize_query(clean))
    cached=cache_get(key)
    if cached is not None:
        return cached

    api_key=_get_newsapi_key()
    if api_key and _budget_allows_request():
        try:
            result=_fetch_newsapi(clean, api_key)
            set_value(key,result,NEWS_CACHE_TTL_SECONDS if result.headlines else NEWS_NEGATIVE_CACHE_TTL_SECONDS)
            log.info("News ready | provider=NewsAPI | query=%s | headlines=%d", clean, len(result.headlines))
            return result
        except Exception as exc:
            # Rate-limit details are logged once by _activate_cooldown; ordinary failures stay concise.
            log.info("NewsAPI unavailable for %s; using RSS fallback (%s)", clean, exc)

    try:
        result=_fetch_google_news(clean)
        set_value(key,result,NEWS_CACHE_TTL_SECONDS if result.headlines else NEWS_NEGATIVE_CACHE_TTL_SECONDS)
        log.info("News ready | provider=Google RSS | query=%s | headlines=%d", clean, len(result.headlines))
        return result
    except Exception as exc:
        result=NewsResult(0.0,[],"Unavailable",str(exc))
        set_value(key,result,NEWS_NEGATIVE_CACHE_TTL_SECONDS)
        log.warning("All news providers failed for %s: %s", clean, exc)
        return result
