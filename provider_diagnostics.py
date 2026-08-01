from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import threading
import time
from typing import Any

import requests

from api_manager import KEY_NAMES, resolve_api_key
from provider_capabilities import diagnostics as capability_diagnostics


@dataclass
class ProviderDiagnostic:
    provider: str
    configured: bool
    status: str
    latency_ms: float | None
    capability: str
    message: str
    checked_at: str


_CACHE_LOCK = threading.Lock()
_CACHE: dict[str, tuple[float, ProviderDiagnostic]] = {}
DEFAULT_DIAGNOSTIC_TTL_SECONDS = 1800
DEFAULT_TIMEOUT_SECONDS = 5


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _result(name: str, configured: bool, status: str, message: str, *, latency: float | None = None, capability: str = "unknown") -> ProviderDiagnostic:
    return ProviderDiagnostic(
        provider=name,
        configured=configured,
        status=status,
        latency_ms=round(latency, 1) if latency is not None else None,
        capability=capability,
        message=message[:260],
        checked_at=_now(),
    )


def _classify_http(name: str, response: requests.Response, elapsed_ms: float) -> ProviderDiagnostic:
    text = (response.text or "")[:1000].lower()
    code = response.status_code
    if 200 <= code < 300:
        # Several market-data APIs return HTTP 200 while embedding quota,
        # authentication, or plan errors inside JSON/text. Classify those
        # before reporting a provider as healthy.
        if any(term in text for term in (
            "rate limit", "ratelimit", "too many requests", "call frequency",
            "api call frequency", "quota exceeded", "limit reached",
        )):
            return _result(name, True, "rate_limited", "Credential detected, but the provider request allowance is temporarily exhausted.", latency=elapsed_ms, capability="limited")
        if any(term in text for term in (
            "premium", "upgrade your plan", "subscription required",
            "not available under your current plan", "entitlement",
        )):
            return _result(name, True, "plan_limited", "Credential works, but this response indicates the requested capability is not included in the current API plan.", latency=elapsed_ms, capability="limited")
        if any(term in text for term in (
            "invalid api key", "invalid apikey", "invalid token",
            "api key is invalid", "authentication failed",
        )):
            return _result(name, True, "invalid_key", "Provider rejected the credential. Check the Railway variable value.", latency=elapsed_ms, capability="offline")
        return _result(name, True, "healthy", "Credential accepted and provider responded.", latency=elapsed_ms, capability="live")
    if code == 429 or "rate limit" in text or "ratelimit" in text or "too many requests" in text:
        return _result(name, True, "rate_limited", "Credential detected, but the provider request allowance is temporarily exhausted.", latency=elapsed_ms, capability="limited")
    if code == 402:
        return _result(name, True, "plan_limited", "Credential was recognized, but this endpoint requires a paid plan or additional entitlement.", latency=elapsed_ms, capability="limited")
    if code in {401, 403}:
        if any(term in text for term in ("premium", "upgrade", "subscription", "not available", "permission", "plan")):
            return _result(name, True, "plan_limited", "Credential works, but this endpoint is not included in the current API plan.", latency=elapsed_ms, capability="limited")
        return _result(name, True, "invalid_key", "Provider rejected the credential. Check the Railway variable value.", latency=elapsed_ms, capability="offline")
    if code == 404:
        return _result(name, True, "configured", "Credential detected; this provider does not expose a safe universal health endpoint.", latency=elapsed_ms, capability="configured")
    return _result(name, True, "degraded", f"Provider returned HTTP {code}.", latency=elapsed_ms, capability="degraded")


def _request(name: str, url: str, *, params: dict[str, Any] | None = None, headers: dict[str, str] | None = None) -> ProviderDiagnostic:
    started = time.perf_counter()
    try:
        response = requests.get(url, params=params, headers=headers, timeout=DEFAULT_TIMEOUT_SECONDS)
        return _classify_http(name, response, (time.perf_counter() - started) * 1000)
    except requests.Timeout:
        return _result(name, True, "degraded", "Provider probe timed out; workers may still use cached or fallback data.", latency=(time.perf_counter() - started) * 1000, capability="degraded")
    except requests.RequestException as exc:
        return _result(name, True, "degraded", f"Provider network probe failed: {exc}", latency=(time.perf_counter() - started) * 1000, capability="degraded")


def _probe(name: str, key: str) -> ProviderDiagnostic:
    # Probes use small metadata or quote requests. They never expose credentials
    # in logs or returned messages.
    if name == "POLYGON_API_KEY":
        return _request(name, "https://api.polygon.io/v2/aggs/ticker/SPY/prev", params={"adjusted": "true", "apiKey": key})
    if name == "FINNHUB_API_KEY":
        return _request(name, "https://finnhub.io/api/v1/quote", params={"symbol": "AAPL", "token": key})
    if name == "EODHD_API_KEY":
        return _request(name, "https://eodhd.com/api/real-time/AAPL.US", params={"api_token": key, "fmt": "json"})
    if name == "ALPHA_VANTAGE_API_KEY":
        return _request(name, "https://www.alphavantage.co/query", params={"function": "GLOBAL_QUOTE", "symbol": "IBM", "apikey": key})
    if name == "FRED_API_KEY":
        return _request(name, "https://api.stlouisfed.org/fred/series", params={"series_id": "GDP", "api_key": key, "file_type": "json"})
    if name == "NASDAQ_DATA_LINK_API_KEY":
        return _request(name, "https://data.nasdaq.com/api/v3/datasets/FRED/GDP.json", params={"api_key": key, "rows": 1})
    if name == "OPENAI_API_KEY":
        return _request(name, "https://api.openai.com/v1/models", headers={"Authorization": f"Bearer {key}"})
    if name == "NEWS_API_KEY":
        # NewsAPI has a tight free quota. Detect the key but avoid consuming a
        # news request during dashboard reruns; the worker reports live state.
        return _result(name, True, "configured", "Credential detected. Live health is reported by the news worker to protect request quota.", capability="configured")
    if name == "SEC_API_KEY":
        return _result(name, True, "configured", "Credential detected. SEC access is identified automatically; endpoint support depends on the selected SEC data service.", capability="configured")
    if name in {"QUIVER_API_KEY", "UNUSUAL_WHALES_API_KEY", "COINGLASS_API_KEY", "WHALE_ALERT_API_KEY"}:
        return _result(name, True, "configured", "Credential detected. The worker will activate supported adapters and record endpoint-level limitations automatically.", capability="configured")
    return _result(name, True, "configured", "Credential detected.", capability="configured")


def diagnose_provider(name: str, *, force: bool = False, ttl_seconds: int = DEFAULT_DIAGNOSTIC_TTL_SECONDS) -> ProviderDiagnostic:
    key = resolve_api_key(name)
    if not key:
        return _result(name, False, "not_configured", "No recognized Railway variable was found.", capability="offline")

    now = time.monotonic()
    with _CACHE_LOCK:
        cached = _CACHE.get(name)
        if cached and not force and now - cached[0] < ttl_seconds:
            return cached[1]

    diagnostic = _probe(name, key)
    with _CACHE_LOCK:
        _CACHE[name] = (now, diagnostic)
    return diagnostic


def _provider_names() -> list[str]:
    return list(KEY_NAMES)


def provider_diagnostics(*, force: bool = False) -> list[dict[str, Any]]:
    records = [asdict(diagnose_provider(name, force=force)) for name in _provider_names()]
    records.insert(0, asdict(_result(
        "YAHOO_FINANCE", True, "available",
        "Public market-data fallback is available without a key.", capability="fallback"
    )))
    for item in capability_diagnostics():
        if not item.get("available") and item.get("limitation"):
            records.append(
                {
                    "provider": item["provider"],
                    "configured": True,
                    "status": "capability_cooldown",
                    "latency_ms": None,
                    "capability": item["capability"],
                    "message": item["limitation"],
                    "checked_at": _now(),
                    "cooldown_remaining_seconds": item["cooldown_remaining_seconds"],
                }
            )
    return records


def detection_summary(records: list[dict[str, Any]]) -> dict[str, int]:
    useful = {"healthy", "available", "configured", "plan_limited", "rate_limited", "degraded"}
    return {
        "detected": sum(bool(x.get("configured")) for x in records),
        "operational": sum(str(x.get("status")) in useful and bool(x.get("configured")) for x in records),
        "healthy": sum(str(x.get("status")) in {"healthy", "available"} for x in records),
        "limited": sum(str(x.get("status")) in {"plan_limited", "rate_limited", "degraded"} for x in records),
        "invalid": sum(str(x.get("status")) == "invalid_key" for x in records),
    }


if __name__ == "__main__":
    import json
    print(json.dumps(provider_diagnostics(force=True), indent=2))
