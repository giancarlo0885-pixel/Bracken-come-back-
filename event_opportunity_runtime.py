from __future__ import annotations

"""Runtime wiring for event-first opportunity discovery.

This patch enriches stock research only. It does not bypass quote verification,
execution policy, portfolio allocation, broker submission, or live-trading
arming. Event-only opportunities remain research records until a symbol can be
verified by the normal market-data path.
"""

from typing import Any

from event_opportunity_scanner import active_event_watchlist, event_context_for_symbol
from market_intelligence_bridge import brain_context_for_signal
from oracle_brain_feedback import outcome_memory_for_signal


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
            event_context = event_context_for_symbol(symbol)
        except Exception:
            event_context = {"score": 0.0, "events": [], "headlines": []}
        market = "crypto" if str(symbol or "").upper().endswith("-USD") else "cash"
        sector = str(getattr(signal, "sector", "") or "")
        try:
            brain_context = brain_context_for_signal(symbol, market=market, sector=sector)
        except Exception:
            brain_context = {
                "catalyst_score": 0.0,
                "sources": [],
                "headlines": [],
                "citations": [],
                "execution_impact": "NONE",
            }
        try:
            brain_outcome_context = outcome_memory_for_signal(
                signal,
                market=market,
                symbol=symbol,
            )
        except Exception:
            brain_outcome_context = {
                "ranking_adjustment": 0.0,
                "execution_impact": "NONE",
                "status": "unavailable",
            }
        brain_outcome_adjustment = float(brain_outcome_context.get("ranking_adjustment") or 0.0)
        setattr(signal, "brain_outcome_adjustment", brain_outcome_adjustment)
        setattr(signal, "brain_outcome_context", brain_outcome_context)

        event_score = float(event_context.get("score") or 0.0)
        brain_score = float(brain_context.get("catalyst_score") or 0.0)
        score = max(event_score, brain_score)
        if score > 0:
            setattr(signal, "external_catalyst_score", score)
            setattr(signal, "event_catalyst_score", event_score)
            setattr(signal, "brain_intelligence_score", brain_score)
            setattr(signal, "event_opportunities", list(event_context.get("events") or []))
            setattr(signal, "event_headlines", list(event_context.get("headlines") or []))
            setattr(signal, "brain_intelligence_context", brain_context)
            reason = str(getattr(signal, "reason", "") or "").strip()
            parts: list[str] = []
            if event_score > 0:
                parts.append(f"event radar {event_score:.0f}/100")
            if brain_score > 0:
                parts.append(f"attributed Brain context {brain_score:.0f}/100")
            suffix = (
                f"Bounded external catalyst context: {', '.join(parts)}; "
                "price/volume confirmation and all Council/risk vetoes still apply."
            )
            setattr(signal, "reason", f"{reason} {suffix}".strip())
        if brain_outcome_adjustment:
            reason = str(getattr(signal, "reason", "") or "").strip()
            direction = "support" if brain_outcome_adjustment > 0 else "penalty"
            suffix = (
                f"Mature exact-provenance Brain outcomes add a bounded ranking {direction} "
                f"of {brain_outcome_adjustment:+.2f} points; Council, quote, risk, capacity, "
                "and execution gates remain authoritative."
            )
            setattr(signal, "reason", f"{reason} {suffix}".strip())
        return signal

    def get_news_sentiment_with_event_context(query: str, *, priority: bool = True):
        result = original_get_news_sentiment(query, priority=priority)
        symbol = _symbol_from_news_query(query)
        if not symbol:
            return result
        try:
            event_context = event_context_for_symbol(symbol)
        except Exception:
            event_context = {"events": [], "headlines": []}
        market = "crypto" if symbol.endswith("-USD") else "cash"
        try:
            brain_context = brain_context_for_signal(symbol, market=market)
        except Exception:
            brain_context = {"headlines": [], "citations": []}
        event_headlines = list(event_context.get("headlines") or [])
        brain_headlines = list(brain_context.get("headlines") or [])
        events = list(event_context.get("events") or [])
        if not event_headlines and not brain_headlines:
            return result
        added_headlines = _merge_unique(brain_headlines, event_headlines, limit=12)
        headlines = _merge_unique(list(getattr(result, "headlines", []) or []), added_headlines, limit=12)
        citations = _merge_unique(
            list(getattr(result, "citations", []) or []),
            [
                *[str(item.get("url") or "") for item in events if isinstance(item, dict) and item.get("url")],
                *[str(item) for item in brain_context.get("citations", []) if item],
            ],
            limit=12,
        )
        source = str(getattr(result, "source", "") or "Unavailable")
        message = str(getattr(result, "message", "") or "").strip()
        context_sources = []
        if event_headlines:
            context_sources.append("Event Radar")
        if brain_headlines:
            context_sources.append("Oracle Brain")
        result_type = type(result)
        try:
            return result_type(
                sentiment=float(getattr(result, "sentiment", 0.0) or 0.0),
                headlines=headlines,
                source=f"{source} + {' + '.join(context_sources)}",
                message=(
                    message
                    + f" Research memory added {len(added_headlines)} attributed catalyst headline(s); "
                    "execution authority remains NONE."
                ).strip(),
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
