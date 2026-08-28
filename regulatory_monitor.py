from __future__ import annotations

from dataclasses import dataclass, asdict
from datetime import datetime, timedelta, timezone
import xml.etree.ElementTree as ET
from typing import Any

import requests

from cache import cached_call
from config import MARKET_NEWS_CACHE_TTL_SECONDS
from security import redact_url


SEC_CRYPTO_FEED_URL = "https://www.sec.gov/news/pressreleases.rss"
CFTC_FEED_URL = "https://www.cftc.gov/RSS/PressReleases.xml"
REGULATORY_EVENT_TTL_DAYS = 14
CRYPTO_REGULATORY_TERMS = (
    "crypto",
    "digital asset",
    "bitcoin",
    "ether",
    "stablecoin",
    "token",
    "staking",
    "etf",
    "exchange-traded",
)


@dataclass
class RegulatoryEvent:
    source: str
    title: str
    url: str
    published_at: str
    expires_at: str
    category: str = "crypto_regulatory"
    verified: bool = True

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ProviderResult:
    available: bool
    provider: str
    records: list[dict[str, Any]]
    message: str


def _parse_datetime(value: Any) -> datetime:
    text = str(value or "").strip()
    for fmt in ("%a, %d %b %Y %H:%M:%S %z", "%a, %d %b %Y %H:%M:%S %Z"):
        try:
            parsed = datetime.strptime(text, fmt)
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            return parsed.astimezone(timezone.utc)
        except ValueError:
            continue
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)
    except ValueError:
        return datetime.now(timezone.utc)


def _feed_items(url: str) -> list[dict[str, str]]:
    headers = {"User-Agent": "GaribaldiMarketOracle/1.0 contact=dashboard"}
    response = requests.get(url, headers=headers, timeout=12)
    response.raise_for_status()
    root = ET.fromstring(response.text)
    items = []
    for item in root.findall(".//item"):
        title = (item.findtext("title") or "").strip()
        link = (item.findtext("link") or "").strip()
        published = (item.findtext("pubDate") or item.findtext("published") or "").strip()
        if title and link:
            items.append({"title": title, "url": link, "published_at": published})
    return items


def _crypto_regulatory_events() -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for source, url in (("SEC", SEC_CRYPTO_FEED_URL), ("CFTC", CFTC_FEED_URL)):
        for item in _feed_items(url):
            haystack = f"{item.get('title', '')} {item.get('url', '')}".lower()
            if not any(term in haystack for term in CRYPTO_REGULATORY_TERMS):
                continue
            published = _parse_datetime(item.get("published_at"))
            expires = published + timedelta(days=REGULATORY_EVENT_TTL_DAYS)
            if expires < datetime.now(timezone.utc):
                continue
            records.append(
                RegulatoryEvent(
                    source=source,
                    title=item["title"],
                    url=item["url"],
                    published_at=published.isoformat(),
                    expires_at=expires.isoformat(),
                ).to_dict()
            )
    return records


def fetch() -> ProviderResult:
    try:
        records = cached_call(
            "official_crypto_regulatory_events",
            MARKET_NEWS_CACHE_TTL_SECONDS,
            _crypto_regulatory_events,
        )
    except Exception as exc:
        return ProviderResult(False, "SEC/CFTC", [], f"Official regulatory feeds unavailable: {redact_url(str(exc))[:180]}")
    return ProviderResult(
        bool(records),
        "SEC/CFTC",
        records,
        "Official SEC/CFTC crypto regulatory events cached with expiry." if records else "No current official crypto regulatory events found.",
    )
