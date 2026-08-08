from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import config


ENTRY_INTENTS = {"entry", "buy", "new_entry", "new position"}
EXIT_INTENTS = {"exit", "sell", "automated_exit", "stop_loss", "take_profit", "trailing_stop"}
FORCED_EXIT_INTENTS = {"forced_risk_reduction", "margin_reduction", "risk_reduction"}
ROTATION_INTENTS = {"rotation", "portfolio_rotation"}
BROKER_INTENTS = {"broker", "broker_submission", "submission"}
SUPPORTED_MARKETS = {"cash", "stock", "crypto"}


@dataclass(frozen=True)
class ExecutionPolicyResult:
    allowed: bool
    reason: str
    intent: str
    market: str


def _flag(name: str, overrides: dict[str, Any] | None = None) -> bool:
    value = overrides[name] if overrides and name in overrides else getattr(config, name, False)
    if value is True:
        return True
    if value is False or value is None:
        return False
    if isinstance(value, str):
        return value.strip().lower() == "true"
    return False


def normalize_market(market: str) -> str:
    text = str(market or "").strip().lower()
    if text == "stock":
        return "cash"
    return text


def normalize_intent(intent: str) -> str:
    text = str(intent or "").strip().lower()
    if text in ENTRY_INTENTS:
        return "entry"
    if text in EXIT_INTENTS:
        return "exit"
    if text in FORCED_EXIT_INTENTS:
        return "forced_risk_reduction"
    if text in ROTATION_INTENTS:
        return "rotation"
    if text in BROKER_INTENTS:
        return "broker"
    return text or "unknown"


def execution_policy(
    *,
    market: str = "cash",
    intent: str = "entry",
    overrides: dict[str, Any] | None = None,
) -> ExecutionPolicyResult:
    market_name = normalize_market(market)
    normalized_intent = normalize_intent(intent)
    if market_name not in SUPPORTED_MARKETS:
        return ExecutionPolicyResult(False, f"unsupported execution market {market_name or 'unknown'}", normalized_intent, market_name or "unknown")
    if _flag("GLOBAL_KILL_SWITCH", overrides):
        return ExecutionPolicyResult(False, "GLOBAL_KILL_SWITCH is enabled", normalized_intent, market_name)
    if not _flag("ENABLE_AUTOTRADE", overrides):
        return ExecutionPolicyResult(False, "ENABLE_AUTOTRADE is false", normalized_intent, market_name)
    market_flag = "ENABLE_CRYPTO_AUTOTRADE" if market_name == "crypto" else "ENABLE_STOCK_AUTOTRADE"
    if not _flag(market_flag, overrides):
        return ExecutionPolicyResult(False, f"{market_flag} is false", normalized_intent, market_name)
    intent_flag = {
        "entry": "ENABLE_NEW_ENTRIES",
        "exit": "ENABLE_AUTOMATED_EXITS",
        "forced_risk_reduction": "ENABLE_AUTOMATED_EXITS",
        "rotation": "ENABLE_PORTFOLIO_ROTATION",
        "broker": "ENABLE_BROKER_SUBMISSION",
    }.get(normalized_intent)
    if not intent_flag:
        return ExecutionPolicyResult(False, f"unknown execution intent {normalized_intent}", normalized_intent, market_name)
    if not _flag(intent_flag, overrides):
        return ExecutionPolicyResult(False, f"{intent_flag} is false", normalized_intent, market_name)
    return ExecutionPolicyResult(True, "execution policy approved", normalized_intent, market_name)
