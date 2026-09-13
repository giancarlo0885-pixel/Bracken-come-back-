from __future__ import annotations

"""Runtime wiring for event-first opportunity discovery.

This patch enriches stock research only. It does not bypass quote verification,
execution policy, portfolio allocation, broker submission, or live-trading
arming. Event-only opportunities remain research records until a symbol can be
verified by the normal market-data path.
"""

from typing import Any

from event_opportunity_scanner import active_event_watchlist, event_context_for_symbol


_INSTALLED = False


def _symbol_from_news_query(query: str) -> str:
    tokens = [token.strip("()[]{}.,:;\"'").upper() for token in str(query or "").split()]
    for token in reversed(tokens):
        if token and 1 <= len(token) <= 10 and token.replace("-", "").replace(".", "").isalnum():
            return token
    return ""


def _merge_unique(values: list[str], extras: list[str], *, limit: int = 12) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for value in [*extras, *values]:
        text = " ".join(str(value or "").split()).strip()
        key = text.lower()
        if not text or key in seen:
            continue
        seen.add(key)
        out.append(text)
        if len(out) >= limit:
            break
    return out


def install_event_opportunity_runtime(market_worker_module: Any) -> None:
    global _INSTALLED
    if _INSTALLED:
        return

    original_scan_market = getattr(market_worker_module, "scan_market", None)
    original_analyze_market = getattr(market_worker_module, "analyze_market", None)
    original_get_news_sentiment = getattr(market_worker_module, "get_news_sentiment", None)
    if not callable(original_scan_market) or not callable(original_analyze_market) or not callable(original_get_news_sentiment):
        return

    def scan_market_with_event_discovery(market: str):
        if str(market or "").lower() == "cash":
            try:
                promoted = active_event_watchlist()
                if promoted:
                    watchlists = getattr(market_worker_module, "WATCHLISTS", None)
                    if isinstance(watchlists, dict) and isinstance(watchlists.get("cash"), dict):
                        watchlists["cash"].update(promoted)
                    logger = getattr(market_worker_module, "log", None)
                    if logger is not None:
                        logger.info("Event radar promoted %s verified symbols into cash discovery.", len(promoted))
            except Exception as exc:
                logger = getattr(market_worker_module, "log", None)
                if logger is not None:
                    logger.info("Event radar discovery unavailable; normal stock scan continues (%s)", exc)
        return original_scan_market(market)

    def analyze_market_with_event_context(symbol: str, history: Any, news_sentiment: float):
        signal = original_analyze_market(symbol, history, news_sentiment)
        if signal is None:
            return None
        try:
            context = event_context_for_symbol(symbol)
        except Exception:
            context = {"score": 0.0, "events": [], "headlines": []}
        score = float(context.get("score") or 0.0)
        if score > 0:
            setattr(signal, "external_catalyst_score", score)
            setattr(signal, "event_catalyst_score", score)
            setattr(signal, "event_opportunities", list(context.get("events") or []))
            setattr(signal, "event_headlines", list(context.get("headlines") or []))
            reason = str(getattr(signal, "reason", "") or "").strip()
            suffix = f"Event radar catalyst {score:.0f}/100 from independently discovered market news."
            setattr(signal, "reason", f"{reason} {suffix}".strip())
        return signal

    def get_news_sentiment_with_event_context(query: str, *, priority: bool = True):
        result = original_get_news_sentiment(query, priority=priority)
        symbol = _symbol_from_news_query(query)
        if not symbol:
            return result
        try:
            context = event_context_for_symbol(symbol)
        except Exception:
            return result
        event_headlines = list(context.get("headlines") or [])
        events = list(context.get("events") or [])
        if not event_headlines:
            return result
        headlines = _merge_unique(list(getattr(result, "headlines", []) or []), event_headlines, limit=12)
        citations = _merge_unique(
            list(getattr(result, "citations", []) or []),
            [str(item.get("url") or "") for item in events if isinstance(item, dict) and item.get("url")],
            limit=12,
        )
        source = str(getattr(result, "source", "") or "Unavailable")
        message = str(getattr(result, "message", "") or "").strip()
        result_type = type(result)
        try:
            return result_type(
                sentiment=float(getattr(result, "sentiment", 0.0) or 0.0),
                headlines=headlines,
                source=f"{source} + Event Radar",
                message=(message + f" Event radar added {len(event_headlines)} independently discovered catalyst headline(s).").strip(),
                citations=citations,
            )
        except Exception:
            # Fail open for research enrichment only; the original provider result
            # remains valid and no execution permission changes.
            return result

    market_worker_module.scan_market = scan_market_with_event_discovery
    market_worker_module.analyze_market = analyze_market_with_event_context
    market_worker_module.get_news_sentiment = get_news_sentiment_with_event_context
    market_worker_module.EVENT_OPPORTUNITY_RUNTIME_ACTIVE = True
    _INSTALLED = True
