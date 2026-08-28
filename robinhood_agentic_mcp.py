from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable


REQUIRED_CRYPTO_TOOLS = {
    "get_accounts",
    "get_portfolio",
    "get_currency_pairs",
    "get_crypto_quotes",
    "get_crypto_positions",
    "get_crypto_orders",
    "preview_crypto_order",
    "place_crypto_order",
    "cancel_crypto_order",
}


@dataclass
class AgenticStatus:
    status: str
    connected: bool
    tools: list[str]
    missing_tools: list[str]


def discover_tools(client: Any | None) -> AgenticStatus:
    if client is None:
        return AgenticStatus("ROBINHOOD_AGENTIC_NOT_CONNECTED", False, [], sorted(REQUIRED_CRYPTO_TOOLS))
    try:
        raw_tools = client.list_tools()
    except Exception:
        return AgenticStatus("ROBINHOOD_AGENTIC_NOT_CONNECTED", False, [], sorted(REQUIRED_CRYPTO_TOOLS))
    tools = sorted(str(tool.get("name") if isinstance(tool, dict) else tool) for tool in (raw_tools or []) if tool)
    missing = sorted(REQUIRED_CRYPTO_TOOLS - set(tools))
    return AgenticStatus("CONNECTED" if not missing else "LIMITED", True, tools, missing)


def _call(client: Any, tool: str, payload: dict[str, Any] | None = None) -> Any:
    if hasattr(client, "call_tool"):
        return client.call_tool(tool, payload or {})
    raise RuntimeError("Robinhood Agentic MCP client does not expose call_tool")


def preview_crypto_order(client: Any | None, order: dict[str, Any]) -> dict[str, Any]:
    status = discover_tools(client)
    if not status.connected:
        return {"ok": False, "status": status.status, "reason": "Robinhood Agentic MCP is not connected"}
    if "preview_crypto_order" in status.missing_tools:
        return {"ok": False, "status": "PREVIEW_UNAVAILABLE", "reason": "preview_crypto_order tool is not exposed"}
    try:
        preview = _call(client, "preview_crypto_order", order)
    except Exception as exc:
        return {"ok": False, "status": "PREVIEW_FAILED", "reason": str(exc)}
    warnings = []
    if isinstance(preview, dict):
        warnings = list(preview.get("warnings") or preview.get("warning_messages") or [])
    return {"ok": not warnings, "status": "PREVIEWED", "preview": preview, "warnings": warnings}


def agentic_preflight(client: Any | None) -> dict[str, Any]:
    status = discover_tools(client)
    result = {
        "ROBINHOOD CONNECTION": status.status,
        "ROBINHOOD AUTH": "UNKNOWN" if not status.connected else "AUTHORIZED_CONNECTION_PRESENT",
        "ACCOUNT STATUS": "UNKNOWN",
        "CRYPTO STATUS": "UNKNOWN",
        "TRADABLE PAIR COUNT": 0,
        "QUOTE CHECK": "NOT_RUN",
        "BUYING POWER CHECK": "NOT_RUN",
        "ORDER PREVIEW CAPABILITY": "PASS" if "preview_crypto_order" not in status.missing_tools else "MISSING",
        "LIVE TRADING ARMED/DISARMED": "DISARMED",
        "missing_tools": status.missing_tools,
    }
    if not status.connected:
        return result
    try:
        accounts = _call(client, "get_accounts", {})
        pairs = _call(client, "get_currency_pairs", {})
        result["ACCOUNT STATUS"] = "PASS" if accounts else "UNKNOWN"
        result["CRYPTO STATUS"] = "PASS" if pairs else "UNKNOWN"
        result["TRADABLE PAIR COUNT"] = len(pairs or []) if isinstance(pairs, list) else 0
    except Exception:
        result["ROBINHOOD AUTH"] = "ERROR"
    return result

