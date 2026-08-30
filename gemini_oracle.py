from __future__ import annotations

import json
import logging
import os
from functools import lru_cache
from typing import Any

from google import genai

from security import safe_exception

log = logging.getLogger("garibaldi-gemini-oracle")

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "").strip()
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-3.5-flash").strip() or "gemini-3.5-flash"
ENABLE_GEMINI = os.getenv("ENABLE_GEMINI", "false").strip().lower() in {
    "1",
    "true",
    "yes",
    "on",
}
GEMINI_MAX_INPUT_CHARS = max(1000, int(os.getenv("GEMINI_MAX_INPUT_CHARS", "30000")))


def gemini_available() -> bool:
    return ENABLE_GEMINI and bool(GEMINI_API_KEY)


def _gemini_safe_error(exc: Exception) -> str:
    text = safe_exception(exc)
    lowered = text.lower()
    status = getattr(exc, "status_code", None)
    if status == 400 or "400" in lowered:
        return "Gemini rejected the request or configured model."
    if status == 401 or "401" in lowered or "api key not valid" in lowered:
        return "Gemini API key is invalid or revoked."
    if status == 403 or "403" in lowered or "permission" in lowered:
        return "Gemini project or model access was denied."
    if status == 404 or "404" in lowered:
        return "The configured Gemini model is unavailable or incorrect."
    if status == 429 or "429" in lowered or "resource exhausted" in lowered:
        return "Gemini API quota or rate limit was reached."
    if "timeout" in lowered or "timed out" in lowered:
        return "Gemini request timed out."
    if "connection" in lowered or "connect" in lowered:
        return "Could not connect to Gemini."
    return text


@lru_cache(maxsize=1)
def get_client():
    if not gemini_available():
        raise RuntimeError(
            "Gemini is not configured. Add GEMINI_API_KEY and set ENABLE_GEMINI=true."
        )
    return genai.Client(api_key=GEMINI_API_KEY)


def _json_text(data: Any, max_chars: int = GEMINI_MAX_INPUT_CHARS) -> str:
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


def call_gemini(
    *,
    instructions: str,
    data: Any,
    request: str,
    max_output_tokens: int = 700,
) -> str:
    if not gemini_available():
        return "Gemini analysis is unavailable because GEMINI_API_KEY is not configured."

    prompt = (
        f"SYSTEM INSTRUCTIONS:\n{instructions.strip()}\n\n"
        "GARIBALDI MARKET ORACLE APPLICATION DATA:\n"
        f"{_json_text(data)}\n\n"
        "REQUEST:\n"
        f"{request.strip()}"
    )
    try:
        interaction = get_client().interactions.create(
            model=GEMINI_MODEL,
            input=prompt,
            generation_config={
                "max_output_tokens": max(20, int(max_output_tokens)),
                "thinking_level": "low",
            },
        )
        text = str(getattr(interaction, "output_text", "") or "").strip()
        return text or "Gemini returned no readable explanation."
    except Exception as exc:
        message = _gemini_safe_error(exc)
        log.warning("Gemini request failed: %s", message)
        return f"Gemini analysis temporarily unavailable: {message}"


def test_gemini_connection() -> dict[str, Any]:
    key_configured = bool(GEMINI_API_KEY)
    if not gemini_available():
        return {
            "available": False,
            "provider": "Google Gemini",
            "model": GEMINI_MODEL,
            "api_key_configured": key_configured,
            "status": "disabled",
            "message": "GEMINI_API_KEY is missing or ENABLE_GEMINI is false.",
        }
    try:
        interaction = get_client().interactions.create(
            model=GEMINI_MODEL,
            input="Reply exactly: GARIBALDI GEMINI ONLINE",
            generation_config={"max_output_tokens": 20, "thinking_level": "minimal"},
        )
        text = str(getattr(interaction, "output_text", "") or "").strip()
        return {
            "available": True,
            "provider": "Google Gemini",
            "model": GEMINI_MODEL,
            "api_key_configured": key_configured,
            "status": "online",
            "message": text or "Connected",
        }
    except Exception as exc:
        message = _gemini_safe_error(exc)
        log.warning("Gemini connection test failed: %s", message)
        return {
            "available": False,
            "provider": "Google Gemini",
            "model": GEMINI_MODEL,
            "api_key_configured": key_configured,
            "status": "error",
            "message": message,
        }


def answer_market_question(question: str, application_context: dict[str, Any]) -> str:
    question = (question or "").strip()
    if not question:
        return "Enter a market question first."
    return call_gemini(
        instructions="""
You are a read-only intelligence specialist for GARIBALDI MARKET ORACLE.
Use only the supplied application data. Never invent or independently replace
market prices, portfolio balances, trades, indicators, or provider timestamps.
Treat verified market-data providers as authoritative for numeric market data.
Clearly identify unavailable information. Explain technical language simply.
Do not execute orders and never guarantee profit.
""".strip(),
        data=application_context,
        request=question,
        max_output_tokens=850,
    )
