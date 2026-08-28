from __future__ import annotations

from datetime import datetime, timezone
import math
from typing import Any, Callable

from config import (
    CRYPTO_CORE_TARGET_PCT,
    CRYPTO_CORE_WEIGHTS,
    CRYPTO_MAX_ACTIVE_SCAN_SYMBOLS,
    CRYPTO_MAX_SPREAD_PCT,
    CRYPTO_MIN_24H_DOLLAR_VOLUME,
    CRYPTO_MIN_CASH_RESERVE_PCT,
    CRYPTO_REGIMES,
    CRYPTO_ROTATION_MIN_SCORE_IMPROVEMENT,
    CRYPTO_TACTICAL_MAX_PCT,
    CRYPTO_TIER_SIZE_MULTIPLIERS,
    CRYPTO_WATCHLIST,
    MAX_SINGLE_CRYPTO_TACTICAL_POSITION_PCT,
)
from capital_allocator import adaptive_capital_allocation
from dashboard_helpers import compact_money_text, format_quantity, live_data_status, money_text, signed_money_text
from provider_router import normalize_symbol


CRYPTO_SIGNAL_NAMES = (
    "trend_momentum",
    "volume_liquidity",
    "catalyst",
    "market_regime",
    "risk_reward",
)


def _finite(value: Any, default: float = 0.0) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return number if math.isfinite(number) else default


def _crypto_symbol(value: Any) -> str:
    symbol = normalize_symbol(value)
    if symbol and not symbol.endswith("-USD") and "-" not in symbol:
        symbol = f"{symbol}-USD"
    return symbol


def _is_crypto_record(record: dict[str, Any]) -> bool:
    symbol = str(record.get("symbol") or record.get("asset") or record.get("ticker") or "").upper().strip()
    market = str(record.get("market") or "").lower().strip()
    asset_class = str(record.get("asset_class") or "").lower().strip()
    return market == "crypto" or asset_class == "crypto" or symbol.endswith("-USD")


def static_crypto_universe() -> list[str]:
    return list(CRYPTO_WATCHLIST.keys())


def dynamic_crypto_universe(
    provider_assets: list[dict[str, Any]],
    provider_supports_symbol: Callable[[str, str], bool] | None = None,
    *,
    limit: int | None = None,
) -> dict[str, Any]:
    provider_supports_symbol = provider_supports_symbol or (lambda symbol, capability: True)
    selected: list[str] = []
    blocked: list[str] = []
    liquidity_rejected: list[str] = []
    for asset in provider_assets:
        symbol = _crypto_symbol(asset.get("symbol") or asset.get("asset") or asset.get("ticker"))
        if not symbol:
            continue
        if not provider_supports_symbol(symbol, "crypto_quote"):
            blocked.append(symbol)
            continue
        dollar_volume = _finite(asset.get("dollar_volume_24h") or asset.get("volume_24h_usd") or asset.get("liquidity"))
        spread = _finite(asset.get("spread_pct"))
        if dollar_volume < CRYPTO_MIN_24H_DOLLAR_VOLUME or spread > CRYPTO_MAX_SPREAD_PCT:
            liquidity_rejected.append(symbol)
            continue
        selected.append(symbol)
    unique = []
    for symbol in sorted(set(selected), key=lambda s: selected.index(s)):
        if symbol not in unique:
            unique.append(symbol)
    max_symbols = min(CRYPTO_MAX_ACTIVE_SCAN_SYMBOLS, limit or CRYPTO_MAX_ACTIVE_SCAN_SYMBOLS)
    merged = []
    for symbol in static_crypto_universe() + unique:
        if symbol not in merged:
            merged.append(symbol)
        if len(merged) >= max_symbols:
            break
    return {
        "symbols": merged,
        "static_count": len(static_crypto_universe()),
        "dynamic_eligible": len(unique),
        "actively_scanned": len(merged),
        "provider_blocked": sorted(set(blocked)),
        "liquidity_rejected": sorted(set(liquidity_rejected)),
    }


def supporting_signal_count(candidate: dict[str, Any]) -> int:
    if isinstance(candidate.get("signals"), dict):
        return sum(1 for name in CRYPTO_SIGNAL_NAMES if bool(candidate["signals"].get(name)))
    count = int(_finite(candidate.get("signals_supporting") or candidate.get("signals_agree")))
    if count > 0:
        return count
    return sum(
        [
            _finite(candidate.get("momentum_score") or candidate.get("trend_score")) >= 60,
            _finite(candidate.get("relative_volume"), 1.0) >= 1.5 or _finite(candidate.get("dollar_volume_24h")) >= CRYPTO_MIN_24H_DOLLAR_VOLUME,
            bool(candidate.get("catalyst") or candidate.get("catalyst_summary")),
            str(candidate.get("crypto_regime") or candidate.get("market_regime") or "neutral").lower() in {"risk_on", "neutral"},
            _finite(candidate.get("reward_risk_ratio")) >= 1.25,
        ]
    )


def crypto_tier(candidate: dict[str, Any]) -> str | None:
    confidence = _finite(candidate.get("confidence"))
    if confidence <= 1:
        confidence *= 100
    rr = _finite(candidate.get("reward_risk_ratio"))
    if confidence >= 80 and rr >= 1.80:
        return "A"
    if confidence >= 70 and rr >= 1.50:
        return "B"
    if confidence >= 62 and rr >= 1.25:
        return "C"
    return None


def crypto_candidate_eligible(candidate: dict[str, Any]) -> tuple[bool, str]:
    symbol = _crypto_symbol(candidate.get("symbol"))
    if not symbol.endswith("-USD"):
        return False, "not a crypto USD symbol"
    if normalize_symbol(candidate.get("requested_symbol") or symbol) != symbol:
        return False, "requested symbol identity mismatch"
    if normalize_symbol(candidate.get("provider_symbol") or symbol) != symbol:
        return False, "provider symbol identity mismatch"
    data = live_data_status({**candidate, "market": "crypto"})
    if data["blocks_execution"]:
        return False, "unverified or stale quote"
    price = _finite(candidate.get("price"))
    if price <= 0:
        return False, "invalid price"
    if _finite(candidate.get("dollar_volume_24h") or candidate.get("volume_24h_usd") or candidate.get("liquidity")) < CRYPTO_MIN_24H_DOLLAR_VOLUME:
        return False, "24h dollar volume below minimum"
    if _finite(candidate.get("spread_pct")) > CRYPTO_MAX_SPREAD_PCT:
        return False, "spread too wide"
    if supporting_signal_count(candidate) < 3:
        return False, "fewer than 3 supporting crypto signals"
    if crypto_tier(candidate) is None:
        return False, "confidence or risk/reward below C tier"
    if candidate.get("provider_capability_supported") is False:
        return False, "provider capability unsupported"
    return True, "eligible"


def crypto_opportunity_score(candidate: dict[str, Any]) -> float:
    eligible, _ = crypto_candidate_eligible(candidate)
    if not eligible:
        return 0.0
    momentum_5m = _finite(candidate.get("change_5m_pct"))
    momentum_15m = _finite(candidate.get("change_15m_pct"))
    momentum_1h = _finite(candidate.get("change_1h_pct"))
    trend_4h = _finite(candidate.get("change_4h_pct"))
    trend_24h = _finite(candidate.get("change_24h_pct") or candidate.get("change_pct"))
    relative_volume = min(_finite(candidate.get("relative_volume"), 1.0) / 3.0, 1.0) * 100
    liquidity = min(_finite(candidate.get("dollar_volume_24h") or candidate.get("liquidity")) / 250_000_000, 1.0) * 100
    breakout = _finite(candidate.get("breakout_quality") or candidate.get("momentum_score") or candidate.get("score"))
    regime = str(candidate.get("crypto_regime") or candidate.get("market_regime") or "neutral").lower()
    regime_score = {"risk_on": 90, "neutral": 70, "risk_off": 45, "high_volatility": 50}.get(regime, 50)
    reward_risk = min(_finite(candidate.get("reward_risk_ratio")) / 3.0, 1.0) * 100
    confidence = _finite(candidate.get("confidence"))
    confidence = confidence * 100 if confidence <= 1 else confidence
    raw_momentum = max(0.0, min(100.0, 50 + (momentum_5m * 2 + momentum_15m * 1.5 + momentum_1h + trend_4h * 0.7 + trend_24h * 0.35)))
    return round(
        raw_momentum * 0.18
        + relative_volume * 0.14
        + liquidity * 0.14
        + breakout * 0.14
        + regime_score * 0.10
        + reward_risk * 0.15
        + confidence * 0.15,
        2,
    )


def tactical_position_size(candidate: dict[str, Any], portfolio: dict[str, Any], existing_value: float = 0.0) -> dict[str, Any]:
    tier = crypto_tier(candidate)
    if tier is None:
        return {"allowed": False, "amount": 0.0, "reason": "candidate is below C tier"}
    equity = max(0.0, _finite(portfolio.get("equity")))
    cash = max(0.0, _finite(portfolio.get("cash")))
    reserve = equity * CRYPTO_MIN_CASH_RESERVE_PCT
    if equity <= 0 or cash <= 0:
        return {"allowed": False, "amount": 0.0, "reason": "broker capacity invalid"}
    if cash <= reserve:
        return {
            "allowed": False,
            "amount": 0.0,
            "tier": tier,
            "cash_after": cash,
            "reserve_required": round(reserve, 2),
            "max_single_position_value": round(equity * MAX_SINGLE_CRYPTO_TACTICAL_POSITION_PCT, 2),
            "reason": "crypto cash reserve protected",
        }
    price = _finite(candidate.get("price"))
    stop = _finite(candidate.get("stop") or candidate.get("stop_loss") or price * 0.90)
    allocation = adaptive_capital_allocation(
        symbol=_crypto_symbol(candidate.get("symbol")),
        market="crypto",
        equity=portfolio.get("equity"),
        cash=portfolio.get("cash"),
        current_exposure=portfolio.get("invested") or portfolio.get("gross_exposure") or 0.0,
        price=price,
        stop_price=stop,
        tier=tier,
        confidence=candidate.get("confidence"),
        reward_risk=candidate.get("reward_risk_ratio"),
        market_regime=candidate.get("crypto_regime") or candidate.get("market_regime") or "neutral",
        dollar_volume=candidate.get("dollar_volume_24h") or candidate.get("liquidity"),
        spread_pct=candidate.get("spread_pct"),
        drawdown_pct=portfolio.get("drawdown_pct") or portfolio.get("drawdown"),
        existing_position_value=existing_value,
        buying_power=portfolio.get("buying_power"),
        buying_power_validated=portfolio.get("buying_power_validated") is True,
        fractional_crypto=True,
    )
    max_single = equity * MAX_SINGLE_CRYPTO_TACTICAL_POSITION_PCT
    amount = allocation.calculated_notional
    return {
        "allowed": allocation.approved,
        "amount": round(amount, 2),
        "tier": tier,
        "cash_after": allocation.cash_after_trade,
        "reserve_required": round(reserve, 2),
        "max_single_position_value": round(max_single, 2),
        "reason": allocation.reason if allocation.approved else allocation.reason.lower().replace("_", " "),
    }


def crypto_core_rebalance_plan(
    quotes: dict[str, dict[str, Any]],
    portfolio: dict[str, Any],
    positions: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    equity = max(0.0, _finite(portfolio.get("equity")))
    cash = max(0.0, _finite(portfolio.get("cash")))
    reserve = equity * CRYPTO_MIN_CASH_RESERVE_PCT
    spendable = max(0.0, cash - reserve)
    if equity <= 0 or spendable <= 0:
        return []

    current_core_values: dict[str, float] = {symbol: 0.0 for symbol in CRYPTO_CORE_WEIGHTS}
    for position in positions:
        symbol = _crypto_symbol(position.get("symbol"))
        if symbol not in current_core_values:
            continue
        bucket = str(position.get("bucket") or position.get("strategy") or "").lower()
        if "core" not in bucket:
            continue
        current_core_values[symbol] += max(0.0, _finite(position.get("market_value"), _finite(position.get("quantity")) * _finite(position.get("current_price"))))

    rows: list[dict[str, Any]] = []
    remaining = spendable
    total_core_target = equity * CRYPTO_CORE_TARGET_PCT
    for symbol, weight in sorted(CRYPTO_CORE_WEIGHTS.items(), key=lambda item: current_core_values.get(item[0], 0.0) - total_core_target * item[1]):
        quote = dict(quotes.get(symbol) or {})
        if not quote:
            continue
        candidate = {
            **quote,
            "symbol": symbol,
            "market": "crypto",
            "asset_class": "crypto",
            "dollar_volume_24h": quote.get("dollar_volume_24h") or quote.get("liquidity") or CRYPTO_MIN_24H_DOLLAR_VOLUME,
            "spread_pct": quote.get("spread_pct", 0.0),
            "signals_supporting": quote.get("signals_supporting", 3),
            "confidence": quote.get("confidence", 70),
            "reward_risk_ratio": quote.get("reward_risk_ratio", 1.5),
        }
        data = live_data_status(candidate)
        if data["blocks_execution"]:
            continue
        price = _finite(quote.get("price"))
        if price <= 0:
            continue
        target_value = total_core_target * weight
        deficit = max(0.0, target_value - current_core_values.get(symbol, 0.0))
        if deficit <= 0 or remaining <= 0:
            continue
        amount = min(deficit, remaining)
        if amount <= 0:
            continue
        rows.append(
            {
                "Asset": symbol,
                "Bucket": "Core",
                "Target Weight": f"{weight:.0%}",
                "Current Core Value": round(current_core_values.get(symbol, 0.0), 2),
                "Amount": round(amount, 2),
                "Quantity": round(amount / price, 10),
                "Reason": "Underweight verified crypto core holding above protected reserve.",
                "Data Status": data["label"],
            }
        )
        remaining -= amount
    return rows


def crypto_core_tactical_quantities(symbol: str, positions: list[dict[str, Any]]) -> dict[str, float]:
    symbol = _crypto_symbol(symbol)
    core = 0.0
    tactical = 0.0
    for position in positions:
        if _crypto_symbol(position.get("symbol")) != symbol:
            continue
        bucket = str(position.get("bucket") or position.get("strategy") or "").lower()
        quantity = _finite(position.get("quantity"))
        if "core" in bucket:
            core += quantity
        else:
            tactical += quantity
    return {"core_quantity": core, "tactical_quantity": tactical}


def protected_crypto_sell_quantity(symbol: str, requested_quantity: float, positions: list[dict[str, Any]]) -> dict[str, Any]:
    quantities = crypto_core_tactical_quantities(symbol, positions)
    sellable = min(_finite(requested_quantity), quantities["tactical_quantity"])
    return {
        **quantities,
        "sellable_quantity": sellable,
        "blocked_quantity": max(0.0, _finite(requested_quantity) - sellable),
        "allowed": sellable > 0 and sellable == _finite(requested_quantity),
    }


def crypto_rotation_candidate(incoming: dict[str, Any], positions: list[dict[str, Any]]) -> dict[str, Any] | None:
    incoming_score = crypto_opportunity_score(incoming)
    if incoming_score <= 0:
        return None
    tactical_positions = [p for p in positions if _crypto_symbol(p.get("symbol")).endswith("-USD") and "core" not in str(p.get("bucket") or p.get("strategy") or "").lower()]
    if not tactical_positions:
        return None
    weakest = min(tactical_positions, key=lambda row: _finite(row.get("holding_score") or row.get("score") or 50))
    held_score = _finite(weakest.get("holding_score") or weakest.get("score") or 50)
    improvement = incoming_score - held_score
    if improvement < CRYPTO_ROTATION_MIN_SCORE_IMPROVEMENT:
        return None
    return {
        "current_holding": _crypto_symbol(weakest.get("symbol")),
        "holding_score": round(held_score, 2),
        "incoming_candidate": _crypto_symbol(incoming.get("symbol")),
        "candidate_score": incoming_score,
        "score_improvement": round(improvement, 2),
        "recommended_action": f"ROTATE INTO {_crypto_symbol(incoming.get('symbol'))}",
    }


def crypto_page_sections(
    candidates: list[dict[str, Any]],
    positions: list[dict[str, Any]],
    ledger_rows: list[dict[str, Any]] | None,
    portfolio: dict[str, Any],
    provider_assets: list[dict[str, Any]] | None = None,
    provider_supports_symbol: Callable[[str, str], bool] | None = None,
) -> dict[str, Any]:
    universe = dynamic_crypto_universe(provider_assets or [], provider_supports_symbol)
    quote_map = {_crypto_symbol(item.get("symbol")): dict(item) for item in candidates if _is_crypto_record(item)}
    eligible_rows = []
    rejected = []
    for candidate in candidates:
        if not _is_crypto_record(candidate):
            continue
        ok, reason = crypto_candidate_eligible(candidate)
        score = crypto_opportunity_score(candidate)
        row = {
            "Rank": 0,
            "Asset": _crypto_symbol(candidate.get("symbol")),
            "Price": money_text(candidate.get("price")),
            "5m %": f"{_finite(candidate.get('change_5m_pct')):+.1f}%",
            "15m %": f"{_finite(candidate.get('change_15m_pct')):+.1f}%",
            "1h %": f"{_finite(candidate.get('change_1h_pct')):+.1f}%",
            "4h %": f"{_finite(candidate.get('change_4h_pct')):+.1f}%",
            "24h %": f"{_finite(candidate.get('change_24h_pct') or candidate.get('change_pct')):+.1f}%",
            "Relative Volume": f"{_finite(candidate.get('relative_volume'), 1.0):.2f}x",
            "Liquidity": compact_money_text(candidate.get("dollar_volume_24h") or candidate.get("liquidity")),
            "Opportunity Score": score,
            "Signals Supporting": supporting_signal_count(candidate),
            "Confidence": f"{(_finite(candidate.get('confidence')) * 100 if _finite(candidate.get('confidence')) <= 1 else _finite(candidate.get('confidence'))):.0f}%",
            "R/R": f"{_finite(candidate.get('reward_risk_ratio')):.2f}",
            "Tier": crypto_tier(candidate) or "",
            "Entry": money_text(candidate.get("entry") or candidate.get("price")),
            "Stop": money_text(candidate.get("stop") or candidate.get("stop_loss")),
            "Target": money_text(candidate.get("target") or candidate.get("target_price")),
            "Action": "BUY" if ok else "WATCH",
            "Data Status": live_data_status({**candidate, "market": "crypto"})["label"],
            "_score": score,
        }
        if ok:
            eligible_rows.append(row)
        else:
            rejected.append({"Asset": row["Asset"], "Reason": reason, "Data Status": row["Data Status"]})
    eligible_rows = sorted(eligible_rows, key=lambda row: _finite(row["_score"]), reverse=True)
    for index, row in enumerate(eligible_rows, start=1):
        row["Rank"] = index
        row.pop("_score", None)
    owned = []
    equity = max(0.0, _finite(portfolio.get("equity")))
    for position in positions:
        if not _is_crypto_record(position):
            continue
        symbol = _crypto_symbol(position.get("symbol"))
        if not symbol.endswith("-USD"):
            continue
        qty = _finite(position.get("quantity"))
        avg = _finite(position.get("average_price") or position.get("entry_price"))
        current = _finite(position.get("current_price") or position.get("price"))
        value = qty * current
        pnl = (current - avg) * qty if avg > 0 and current > 0 else 0.0
        owned.append(
            {
                "Asset": symbol,
                "Bucket": position.get("bucket") or ("Core" if symbol in CRYPTO_CORE_WEIGHTS else "Tactical"),
                "Quantity": format_quantity(qty),
                "Average Cost": money_text(avg),
                "Current Verified Price": money_text(current),
                "Market Value": money_text(value),
                "Unrealized P/L $": signed_money_text(pnl),
                "Unrealized P/L %": f"{(((current / avg) - 1) * 100 if avg > 0 and current > 0 else 0):+.1f}%",
                "Portfolio Weight": f"{(value / equity * 100 if equity else 0):.1f}%",
                "Strategy": position.get("strategy") or "",
                "Tier": position.get("tier") or "",
                "Opened": position.get("opened_at") or "",
                "Provider": position.get("provider") or "",
                "Data Status": position.get("data_status") or "Position mark",
            }
        )
    profit_sources = []
    for row in ledger_rows or []:
        if not _is_crypto_record(row):
            continue
        symbol = _crypto_symbol(row.get("symbol"))
        if not symbol.endswith("-USD"):
            continue
        profit_sources.append(
            {
                "Asset": symbol,
                "Strategy": row.get("strategy") or "",
                "Bucket": row.get("bucket") or "",
                "Entry": money_text(row.get("entry_price")),
                "Exit / Current": money_text(row.get("exit_price") or row.get("current_price")),
                "Quantity": format_quantity(row.get("quantity")),
                "Gross P/L": signed_money_text(row.get("gross_pnl")),
                "Fees": money_text(row.get("fees")),
                "Net P/L": signed_money_text(row.get("net_pnl")),
                "Return %": f"{_finite(row.get('return_pct')):+.1f}%",
                "Tier": row.get("tier") or "",
                "Held For": row.get("held_for") or "",
                "Status": row.get("status") or "",
            }
        )
    rotations = [candidate for candidate in (crypto_rotation_candidate(item, positions) for item in candidates) if candidate]
    return {
        "summary": {
            "Configured Core Assets": len(CRYPTO_CORE_WEIGHTS),
            "Static Tactical Symbols": len(CRYPTO_WATCHLIST),
            "Dynamic Eligible Symbols": universe["dynamic_eligible"],
            "Actively Scanned": universe["actively_scanned"],
            "Provider Blocked": len(universe["provider_blocked"]),
            "Liquidity Rejected": len(universe["liquidity_rejected"]),
            "Unverified": len(rejected),
            "Core Target": f"{CRYPTO_CORE_TARGET_PCT:.0%}",
            "Tactical Max": f"{CRYPTO_TACTICAL_MAX_PCT:.0%}",
            "Cash Reserve": f"{CRYPTO_MIN_CASH_RESERVE_PCT:.0%}",
        },
        "owned": owned,
        "best_trades": eligible_rows,
        "movers": eligible_rows[:15],
        "rotations": rotations,
        "profit_sources": profit_sources,
        "core_allocation": [{"Asset": symbol, "Target Weight": f"{weight:.0%}"} for symbol, weight in CRYPTO_CORE_WEIGHTS.items()],
        "core_deployment": crypto_core_rebalance_plan(quote_map, portfolio, positions),
        "waiting": rejected[:20],
        "universe": universe,
    }


def worker_provider_failure_result(symbols: list[str], failed_provider: str, error: Exception) -> dict[str, Any]:
    return {
        "continue_scanning": True,
        "failed_provider": failed_provider,
        "error": str(error),
        "remaining_symbols": list(symbols),
    }
