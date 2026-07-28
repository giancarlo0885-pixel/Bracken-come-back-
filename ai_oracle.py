from __future__ import annotations

import json
import logging
import os
from functools import lru_cache
from typing import Any

from openai import OpenAI

log = logging.getLogger("garibaldi-ai-oracle")

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "").strip()
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-5-mini").strip()
ENABLE_OPENAI = os.getenv("ENABLE_OPENAI", "true").strip().lower() in {
    "1", "true", "yes", "on"
}
OPENAI_TIMEOUT_SECONDS = float(os.getenv("OPENAI_TIMEOUT_SECONDS", "30"))
OPENAI_MAX_INPUT_CHARS = int(os.getenv("OPENAI_MAX_INPUT_CHARS", "30000"))


def openai_available() -> bool:
    return ENABLE_OPENAI and bool(OPENAI_API_KEY)


@lru_cache(maxsize=1)
def get_client() -> OpenAI:
    if not openai_available():
        raise RuntimeError(
            "OpenAI is not configured. Add OPENAI_API_KEY and set ENABLE_OPENAI=true."
        )
    return OpenAI(
        api_key=OPENAI_API_KEY,
        timeout=OPENAI_TIMEOUT_SECONDS,
        max_retries=2,
    )


def _json_text(data: Any, max_chars: int = OPENAI_MAX_INPUT_CHARS) -> str:
    try:
        text = json.dumps(
            data,
            default=str,
            ensure_ascii=False,
            separators=(",", ":"),
        )
    except Exception:
        text = json.dumps({"data": str(data)}, ensure_ascii=False)

    if len(text) <= max_chars:
        return text

    return text[:max_chars] + ',"_truncated":true}'


def _call_openai(
    *,
    instructions: str,
    data: Any,
    request: str,
    max_output_tokens: int = 700,
) -> str:
    if not openai_available():
        return "OpenAI analysis is unavailable because OPENAI_API_KEY is not configured."

    prompt = (
        "GARIBALDI MARKET ORACLE APPLICATION DATA:\n"
        f"{_json_text(data)}\n\n"
        "REQUEST:\n"
        f"{request}"
    )

    try:
        response = get_client().responses.create(
            model=OPENAI_MODEL,
            instructions=instructions,
            input=prompt,
            max_output_tokens=max_output_tokens,
        )
        text = (response.output_text or "").strip()
        return text or "OpenAI returned no readable explanation."
    except Exception as exc:
        log.exception("OpenAI request failed")
        return f"OpenAI analysis temporarily unavailable: {exc}"


def test_openai_connection() -> dict[str, Any]:
    if not openai_available():
        return {
            "available": False,
            "provider": "OpenAI",
            "model": OPENAI_MODEL,
            "message": "OPENAI_API_KEY is missing or ENABLE_OPENAI is false.",
        }

    try:
        response = get_client().responses.create(
            model=OPENAI_MODEL,
            instructions="Return only the requested status text.",
            input="Reply exactly: GARIBALDI AI ONLINE",
            max_output_tokens=20,
        )
        return {
            "available": True,
            "provider": "OpenAI",
            "model": OPENAI_MODEL,
            "message": (response.output_text or "Connected").strip(),
        }
    except Exception as exc:
        log.exception("OpenAI connection test failed")
        return {
            "available": False,
            "provider": "OpenAI",
            "model": OPENAI_MODEL,
            "message": str(exc),
        }


def explain_trade(signal: dict[str, Any]) -> str:
    return _call_openai(
        instructions="""
You are the plain-English explanation engine for GARIBALDI MARKET ORACLE.
Use only the supplied application data.
Do not invent prices, news, indicators, forecasts, or transactions.
Explain the decision, strongest supporting evidence, biggest risk, and what
would invalidate the setup. Never guarantee profit. Keep it under 150 words.
""".strip(),
        data=signal,
        request="Explain this market decision for a nonprofessional trader.",
        max_output_tokens=300,
    )


def explain_risk(trade: dict[str, Any]) -> str:
    return _call_openai(
        instructions="""
You are the risk specialist for GARIBALDI MARKET ORACLE.
Use only supplied data. Explain position size, cash exposure, stop loss,
take profit, volatility, concentration, downside risk, and missing data.
Never promise success. Keep the answer under 180 words.
""".strip(),
        data=trade,
        request="Explain the risk of this proposed trade and what must be monitored.",
        max_output_tokens=350,
    )


def oracle_council(symbol: str, market_data: dict[str, Any]) -> str:
    return _call_openai(
        instructions="""
You are the GARIBALDI ORACLE COUNCIL.
Review the opportunity through these specialists:
1. Technical Strategist
2. Fundamental and Earnings Analyst
3. Macro Economist
4. News and Sentiment Analyst
5. Whale and Institutional Activity Analyst
6. Risk Manager

Use only supplied data. Say "insufficient data" whenever evidence is missing.
Separate bullish evidence, bearish evidence, uncertainty, and risk.
End with one conclusion: WATCH, AVOID, CAUTIOUS BUY, BUY, HOLD, REDUCE, or SELL.
Include a confidence score from 0 to 100. Never guarantee returns.
""".strip(),
        data={"symbol": symbol, "market_data": market_data},
        request=f"Hold a concise Oracle Council review for {symbol}.",
        max_output_tokens=1100,
    )


def market_briefing(
    market_snapshot: dict[str, Any],
    briefing_type: str = "market",
) -> str:
    return _call_openai(
        instructions="""
Write an executive market briefing for GARIBALDI MARKET ORACLE.
Use only supplied data. Cover market direction, strongest assets or sectors,
weakest assets or sectors, unusual volume or volatility, major news signals,
highest-ranked opportunities, portfolio risks, and what to watch next.
Do not fabricate missing information or guarantee returns.
Use short headings and readable language.
""".strip(),
        data=market_snapshot,
        request=f"Create the {briefing_type} briefing.",
        max_output_tokens=1100,
    )


def summarize_watchlist(watchlist_results: list[dict[str, Any]]) -> str:
    return _call_openai(
        instructions="""
Summarize a GARIBALDI MARKET ORACLE watchlist scan.
Use only supplied data. Group symbols into:
- Best opportunities
- Improving
- Waiting or neutral
- Weak or high-risk
Explain why the leading symbols matter. Do not invent market events.
""".strip(),
        data={"watchlist": watchlist_results},
        request="Create an easy-to-read watchlist summary.",
        max_output_tokens=850,
    )


def portfolio_coach(
    portfolio: dict[str, Any],
    positions: list[dict[str, Any]],
    analytics: dict[str, Any] | None = None,
) -> str:
    return _call_openai(
        instructions="""
You are the portfolio coach for GARIBALDI MARKET ORACLE.
Use only supplied data. Review cash level, concentration, gains and losses,
sector exposure, volatility, drawdown, diversification, and positions needing
attention. Do not place trades or guarantee returns. Mention missing data.
Prioritize the three most important observations.
""".strip(),
        data={
            "portfolio": portfolio,
            "positions": positions,
            "analytics": analytics or {},
        },
        request="Explain portfolio health and the three most important concerns.",
        max_output_tokens=850,
    )


def answer_market_question(
    question: str,
    application_context: dict[str, Any],
) -> str:
    question = (question or "").strip()
    if not question:
        return "Enter a market question first."

    return _call_openai(
        instructions="""
You are the in-app assistant for GARIBALDI MARKET ORACLE.
Answer using only supplied application data. Never claim live information that
was not provided. Clearly identify unavailable information. Do not invent
quotes, balances, trades, news, or indicators. Explain technical language
simply. Do not execute orders or guarantee profit.
""".strip(),
        data=application_context,
        request=question,
        max_output_tokens=850,
    )


def opportunity_comparison(
    opportunities: list[dict[str, Any]],
    limit: int = 5,
) -> str:
    selected = opportunities[: max(1, min(limit, 10))]
    return _call_openai(
        instructions="""
Compare ranked market opportunities for GARIBALDI MARKET ORACLE.
Use only supplied data. Explain why the top candidate ranks above the others,
identify the best risk-adjusted setup, identify the most speculative setup,
and state any missing evidence. Do not guarantee returns.
""".strip(),
        data={"opportunities": selected},
        request="Compare these opportunities in plain English.",
        max_output_tokens=800,
    )
