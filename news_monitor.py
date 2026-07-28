from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import Any

import requests

from cache import cached_call
from config import MARKET_NEWS_CACHE_TTL_SECONDS


log = logging.getLogger("news-monitor")

NEWSAPI_URL = "https://newsapi.org/v2/everything"

MAX_ARTICLES = int(
    os.getenv("NEWSAPI_MAX_ARTICLES", "20")
)

DEFAULT_QUERY = os.getenv(
    "NEWSAPI_MARKET_QUERY",
    (
        "stock market OR cryptocurrency OR economy OR "
        "Federal Reserve OR earnings"
    ),
).strip()


@dataclass
class ProviderResult:
    available: bool
    provider: str
    records: list[dict[str, Any]]
    message: str


def _get_api_key() -> str:
    for name in (
        "NEWSAPI_API_KEY",
        "NEWS_API_KEY",
        "NEWSAPI_KEY",
    ):
        value = os.getenv(name, "").strip()

        if value:
            return value

    return ""


def _clean_article(
    article: dict[str, Any],
) -> dict[str, Any]:
    source_data = article.get("source")

    if not isinstance(source_data, dict):
        source_data = {}

    return {
        "title": article.get("title"),
        "description": article.get("description"),
        "source": source_data.get("name"),
        "author": article.get("author"),
        "published_at": article.get("publishedAt"),
        "url": article.get("url"),
    }


def _fetch_uncached() -> ProviderResult:
    api_key = _get_api_key()

    if not api_key:
        return ProviderResult(
            available=False,
            provider="Not configured",
            records=[],
            message=(
                "Add NEWSAPI_API_KEY to Railway variables."
            ),
        )

    try:
        response = requests.get(
            NEWSAPI_URL,
            params={
                "q": DEFAULT_QUERY,
                "language": "en",
                "sortBy": "publishedAt",
                "pageSize": MAX_ARTICLES,
            },
            headers={
                "X-Api-Key": api_key,
                "Accept": "application/json",
            },
            timeout=20,
        )

        try:
            payload = response.json()
        except ValueError:
            payload = {}

        if response.status_code == 401:
            return ProviderResult(
                available=False,
                provider="NewsAPI",
                records=[],
                message="NewsAPI rejected the API key.",
            )

        if response.status_code == 429:
            return ProviderResult(
                available=False,
                provider="NewsAPI",
                records=[],
                message="NewsAPI request limit reached.",
            )

        if not response.ok:
            error_message = payload.get(
                "message",
                response.text[:200],
            )

            return ProviderResult(
                available=False,
                provider="NewsAPI",
                records=[],
                message=(
                    f"NewsAPI request failed: "
                    f"{error_message}"
                ),
            )

        articles = payload.get("articles", [])

        if not isinstance(articles, list):
            articles = []

        records = [
            _clean_article(article)
            for article in articles
            if isinstance(article, dict)
            and article.get("title")
            and article.get("title") != "[Removed]"
        ]

        log.info(
            "NewsAPI returned %d market news records",
            len(records),
        )

        if not records:
            return ProviderResult(
                available=True,
                provider="NewsAPI",
                records=[],
                message=(
                    "NewsAPI connected successfully, but no "
                    "market headlines were returned."
                ),
            )

        return ProviderResult(
            available=True,
            provider="NewsAPI",
            records=records,
            message=(
                f"Loaded {len(records)} current market "
                f"headlines."
            ),
        )

    except requests.RequestException as exc:
        log.warning(
            "NewsAPI connection failed: %s",
            exc,
        )

        return ProviderResult(
            available=False,
            provider="NewsAPI",
            records=[],
            message=f"NewsAPI connection failed: {exc}",
        )

def fetch() -> ProviderResult:
    """Return cached broad-market news so intelligence collection cannot burn the API quota."""
    return cached_call("broad_market_news", MARKET_NEWS_CACHE_TTL_SECONDS, _fetch_uncached)
