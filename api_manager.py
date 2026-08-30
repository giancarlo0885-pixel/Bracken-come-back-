from __future__ import annotations

import os
from dataclasses import dataclass
from dotenv import load_dotenv

load_dotenv()

# Canonical provider variables understood by the platform. Aliases allow the
# app to recognize common Railway naming variations automatically.
PROVIDER_ALIASES: dict[str, tuple[str, ...]] = {
    "ALPHA_VANTAGE_API_KEY": ("ALPHA_VANTAGE_API_KEY", "ALPHAVANTAGE_API_KEY"),
    "FINNHUB_API_KEY": ("FINNHUB_API_KEY", "FINNHUB_TOKEN"),
    "NEWS_API_KEY": ("NEWS_API_KEY", "NEWSAPI_API_KEY", "NEWSAPI_KEY"),
    "POLYGON_API_KEY": ("POLYGON_API_KEY", "POLYGON_KEY"),
    "EODHD_API_KEY": ("EODHD_API_KEY", "EOD_API_KEY", "EODHD_TOKEN"),
    "OPENAI_API_KEY": ("OPENAI_API_KEY",),
    "GEMINI_API_KEY": ("GEMINI_API_KEY", "GOOGLE_API_KEY"),
    "FRED_API_KEY": ("FRED_API_KEY",),
    "NASDAQ_DATA_LINK_API_KEY": ("NASDAQ_DATA_LINK_API_KEY", "NASDAQ_API_KEY", "QUANDL_API_KEY"),
    "SEC_API_KEY": ("SEC_API_KEY",),
    "QUIVER_API_KEY": ("QUIVER_API_KEY", "QUIVERQUANT_API_KEY"),
    "UNUSUAL_WHALES_API_KEY": ("UNUSUAL_WHALES_API_KEY", "UNUSUAL_WHALES_TOKEN"),
    "COINGLASS_API_KEY": ("COINGLASS_API_KEY",),
    "WHALE_ALERT_API_KEY": ("WHALE_ALERT_API_KEY",),
}

KEY_NAMES = list(PROVIDER_ALIASES)


def resolve_api_key(name: str) -> str | None:
    """Return the first non-empty credential found for a provider alias."""
    for candidate in PROVIDER_ALIASES.get(name, (name,)):
        value = os.getenv(candidate, "").strip()
        if value:
            return value
    return None


@dataclass(frozen=True)
class APISettings:
    values: dict[str, str | None]

    def has(self, name: str) -> bool:
        return bool(self.values.get(name))

    def get(self, name: str, default: str | None = None) -> str | None:
        return self.values.get(name) or default


def get_api_settings() -> APISettings:
    return APISettings({name: resolve_api_key(name) for name in KEY_NAMES})


def api_status() -> dict[str, bool]:
    settings = get_api_settings()
    return {
        name.replace("_API_KEY", "").replace("_", " ").title(): settings.has(name)
        for name in KEY_NAMES
    }
