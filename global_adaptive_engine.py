from __future__ import annotations

from dataclasses import dataclass, asdict
from datetime import date, datetime, timedelta, timezone
import hashlib
import os
import json
import math
from typing import Any

from config import (
    ENABLE_BROKER_SUBMISSION,
    GLOBAL_PIT_MAX_POSITION_PCT,
    GLOBAL_PIT_PREFERRED_POSITION_PCT,
    GLOBAL_PIT_RESERVE_PCT,
    GLOBAL_PIT_TARGET_INVESTED_PCT,
    MAX_SECTOR_EXPOSURE_PCT,
    PAPER_MAX_MARKET_PARTICIPATION_PCT,
)
from global_pit_engine import _execution_quote_eligible, _finite, _asset_class, _upper, hard_risk_gate

EXECUTABLE_ASSET_CLASSES = {"stock", "equity", "etf", "crypto"}
INTELLIGENCE_ONLY_ASSET_CLASSES = {
    "forex",
    "commodity",
    "commodities",
    "rates",
    "fixed_income",
    "index",
    "future",
    "futures",
    "option",
    "options",
    "macro",
    "economic_release",
    "news",
}
PROMOTION_MIN_SAMPLE = 30
PROMOTION_MIN_ACCURACY = 0.55
PROMOTION_MAX_MAPE = 15.0
FUNNEL_STAGES = [
    "surveillance",
    "active_hot",
    "deep_research",
    "buy_signal",
    "verified_quote",
    "forecast_approved",
    "portfolio_approved",
    "execution_approved",
    "paper_trade_executed",
]
V39_OUTCOME_HORIZONS = {"5m": 5, "15m": 15, "1h": 60, "4h": 240, "1d": 1440, "1w": 10080}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, default=str)


def _parse_aware_datetime(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, date):
        parsed = datetime(value.year, value.month, value.day)
    else:
        text = str(value or "").strip()
        if not text:
            return None
        try:
            parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError:
            return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _decision_id(market: str, symbol: str, signal_id: Any = None, created_at: Any = None) -> str:
    raw = f"{market}|{symbol}|{signal_id or ''}|{created_at or utc_now()}"
    return "decision:" + hashlib.sha1(raw.encode("utf-8")).hexdigest()[:18]


@dataclass(frozen=True)
class GlobalAssetIdentity:
    asset_class: str
    exchange: str
    native_symbol: str
    currency: str

    @property
    def canonical_id(self) -> str:
        raw = "|".join(
            [
                _asset_class(self.asset_class),
                str(self.exchange or "UNKNOWN").strip().upper(),
                str(self.native_symbol or "").strip().upper(),
                str(self.currency or "UNKNOWN").strip().upper(),
            ]
        )
        digest = hashlib.sha1(raw.encode("utf-8")).hexdigest()[:12]
        return f"gaid:{raw}:{digest}"


def canonical_identity(asset: dict[str, Any]) -> dict[str, Any]:
    native = asset.get("native_symbol") or asset.get("symbol")
    identity = GlobalAssetIdentity(
        asset_class=_asset_class(asset.get("asset_class")),
        exchange=str(asset.get("exchange") or "UNKNOWN").strip().upper(),
        native_symbol=str(native or "").strip().upper(),
        currency=str(asset.get("currency") or "UNKNOWN").strip().upper(),
    )
    aliases = asset.get("provider_aliases") or {}
    if isinstance(aliases, str):
        try:
            aliases = json.loads(aliases)
        except Exception:
            aliases = {"raw": aliases}
    provider = asset.get("provider")
    provider_symbol = asset.get("provider_symbol")
    if provider and provider_symbol:
        aliases = {**(aliases or {}), str(provider): str(provider_symbol)}
    return {**asdict(identity), "canonical_id": identity.canonical_id, "provider_aliases": aliases or {}}


def merge_global_asset_records(records: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    merged: dict[str, dict[str, Any]] = {}
    for record in records:
        identity = canonical_identity(record)
        key = identity["canonical_id"]
        current = merged.setdefault(key, {**record, **identity})
        current.update({k: v for k, v in record.items() if v not in (None, "", [], {})})
        aliases = dict(current.get("provider_aliases") or {})
        aliases.update(identity.get("provider_aliases") or {})
        provider = record.get("provider")
        provider_symbol = record.get("provider_symbol") or record.get("symbol")
        if provider and provider_symbol:
            aliases[str(provider)] = str(provider_symbol)
        current["provider_aliases"] = aliases
    return merged


def classify_capital_engine(asset_or_market: Any) -> str:
    cls = _asset_class(asset_or_market.get("asset_class") if isinstance(asset_or_market, dict) else asset_or_market)
    if cls == "crypto":
        return "crypto"
    if cls in {"stock", "equity", "etf"}:
        return "stock"
    return "intelligence_only"


def capital_engine_state(portfolio: dict[str, Any], positions: list[dict[str, Any]], opportunities: list[dict[str, Any]], engine: str) -> dict[str, Any]:
    equity = max(0.0, _finite(portfolio.get("equity") or portfolio.get("total_equity") or portfolio.get("value")))
    cash = max(0.0, _finite(portfolio.get("cash")))
    invested = max(0.0, equity - cash)
    reserve = equity * GLOBAL_PIT_RESERVE_PCT
    gap = max(0.0, equity * GLOBAL_PIT_TARGET_INVESTED_PCT - invested)
    qualified = [item for item in opportunities if classify_capital_engine(item) == engine and item.get("qualified_for_capital") is True]
    exposure = sum(_finite(p.get("market_value") or _finite(p.get("quantity")) * _finite(p.get("current_price"))) for p in positions)
    return {
        "engine": engine,
        "equity": equity,
        "cash": cash,
        "invested_pct": invested / equity if equity else 0.0,
        "reserve_pct": GLOBAL_PIT_RESERVE_PCT,
        "reserve_cash_required": reserve,
        "deployment_gap": gap,
        "buying_power": _finite(portfolio.get("buying_power"), cash),
        "gross_exposure": exposure,
        "margin_utilization": _finite(portfolio.get("margin_utilization")),
        "qualified_opportunities": len(qualified),
    }


def split_capital_engines(stock_portfolio: dict[str, Any], crypto_portfolio: dict[str, Any], stock_positions: list[dict[str, Any]], crypto_positions: list[dict[str, Any]], opportunities: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {
        "stock": capital_engine_state(stock_portfolio, stock_positions, opportunities, "stock"),
        "crypto": capital_engine_state(crypto_portfolio, crypto_positions, opportunities, "crypto"),
    }


def liquidity_capacity(asset: dict[str, Any], proposed_order_value: float) -> dict[str, Any]:
    adv = _finite(asset.get("avg_dollar_volume") or asset.get("liquidity"))
    spread_pct = max(0.0, _finite(asset.get("spread_pct")))
    proposed = max(0.0, _finite(proposed_order_value))
    verified = adv > 0
    max_order_value = adv * PAPER_MAX_MARKET_PARTICIPATION_PCT if verified else 0.0
    executable_value = min(proposed, max_order_value) if verified else 0.0
    participation = executable_value / adv if adv > 0 and executable_value > 0 else (math.inf if proposed > 0 else 0.0)
    desired_participation = proposed / adv if adv > 0 else math.inf
    estimated_slippage_pct = spread_pct / 2.0 + max(0.0, participation - PAPER_MAX_MARKET_PARTICIPATION_PCT) * 100.0
    return {
        "verified": verified,
        "average_dollar_volume": adv,
        "proposed_order_value": proposed,
        "executable_order_value": executable_value,
        "market_participation_pct": participation,
        "desired_market_participation_pct": desired_participation,
        "max_market_participation_pct": PAPER_MAX_MARKET_PARTICIPATION_PCT,
        "estimated_slippage_pct": estimated_slippage_pct,
        "partial_sizing": bool(verified and 0 < executable_value < proposed),
        "allowed": bool(verified and executable_value > 0 and participation <= PAPER_MAX_MARKET_PARTICIPATION_PCT),
    }


def apply_cross_market_influence(executable: list[dict[str, Any]], intelligence: list[dict[str, Any]]) -> list[dict[str, Any]]:
    influences: dict[str, float] = {}
    for item in intelligence:
        cls = _asset_class(item.get("asset_class"))
        strength = _finite(item.get("strength_score") or item.get("opportunity_score") or item.get("change_1d_pct"))
        confidence = max(0.0, min(1.0, _finite(item.get("confidence"), 1.0)))
        signed = strength * confidence
        label = str(item.get("symbol") or item.get("name") or "").lower()
        if cls in {"commodity", "commodities", "future", "futures"} and "oil" in label:
            influences["Energy"] = influences.get("Energy", 0.0) + max(-8.0, min(8.0, signed * 0.08))
        if cls in {"commodity", "commodities", "future", "futures"} and "copper" in label:
            influences["Industrials"] = influences.get("Industrials", 0.0) + max(-6.0, min(6.0, signed * 0.06))
        if cls in {"rates", "fixed_income"}:
            influences["Financials"] = influences.get("Financials", 0.0) + max(-5.0, min(5.0, signed * 0.05))
            influences["Technology"] = influences.get("Technology", 0.0) - max(-5.0, min(5.0, signed * 0.04))
        if cls == "forex" and "USD" in str(item.get("symbol") or "").upper():
            influences["International"] = influences.get("International", 0.0) - max(-4.0, min(4.0, signed * 0.04))
            influences["Commodities"] = influences.get("Commodities", 0.0) - max(-4.0, min(4.0, signed * 0.03))
        if cls == "crypto" and str(item.get("symbol") or "").upper().startswith("BTC"):
            influences["Crypto"] = influences.get("Crypto", 0.0) + max(-8.0, min(8.0, signed * 0.08))
    adjusted = []
    for asset in executable:
        sector = str(asset.get("sector") or "")
        cls = _asset_class(asset.get("asset_class"))
        delta = influences.get(sector, 0.0)
        if cls == "crypto":
            delta += influences.get("Crypto", 0.0)
        if asset.get("country") and str(asset.get("country")).lower() != "united states":
            delta += influences.get("International", 0.0)
        adjusted.append({**asset, "macro_influence_score": round(delta, 2), "soft_score": round(_finite(asset.get("opportunity_score")) + delta, 2)})
    return adjusted


def evaluate_forecast_outcome(decision: dict[str, Any], observed: dict[str, Any]) -> dict[str, Any]:
    generated = _parse_aware_datetime(decision.get("generated_at") or decision.get("created_at"))
    observed_at = _parse_aware_datetime(observed.get("observed_at") or observed.get("timestamp"))
    if generated is None or observed_at is None:
        return {"evaluated": False, "reason": "timestamp missing or invalid"}
    if observed_at <= generated:
        return {"evaluated": False, "reason": "future leakage guard: observation is not after decision"}
    entry = _finite(decision.get("price") or decision.get("source_price"))
    exit_price = _finite(observed.get("price"))
    predicted = _finite(decision.get("expected_move_pct"))
    realized = ((exit_price / entry) - 1.0) * 100.0 if entry > 0 and exit_price > 0 else 0.0
    direction_correct = (predicted >= 0 and realized >= 0) or (predicted < 0 and realized < 0)
    return {
        "evaluated": True,
        "realized_return_pct": realized,
        "absolute_forecast_error": abs(predicted - realized),
        "direction_correct": direction_correct,
        "mfe_pct": _finite(observed.get("mfe_pct")),
        "mae_pct": _finite(observed.get("mae_pct")),
        "drawdown_pct": _finite(observed.get("drawdown_pct")),
        "duration_seconds": _finite(observed.get("duration_seconds")),
        "risk_adjusted_result": realized / max(1.0, abs(_finite(observed.get("mae_pct"), 1.0))),
        "execution_cost_pct": _finite(observed.get("execution_cost_pct")),
        "slippage_pct": _finite(observed.get("slippage_pct")),
    }


def learning_weights_by_context(observations: list[dict[str, Any]], min_samples: int = 10) -> dict[str, float]:
    grouped: dict[tuple[str, str, str], list[float]] = {}
    for obs in observations:
        key = (str(obs.get("model") or "unknown"), str(obs.get("asset_class") or "unknown"), str(obs.get("market_regime") or "unknown"))
        grouped.setdefault(key, []).append(_finite(obs.get("realized_edge_pct") or obs.get("realized_return_pct")))
    weights: dict[str, float] = {}
    for key, values in grouped.items():
        if len(values) < min_samples:
            continue
        avg = sum(values) / len(values)
        weights["|".join(key)] = max(0.75, min(1.25, 1.0 + avg / 100.0))
    return weights


def champion_challenger_decision(model: dict[str, Any], champion: dict[str, Any] | None = None) -> dict[str, Any]:
    status = str(model.get("status") or "SHADOW").upper()
    samples = int(_finite(model.get("sample_count")))
    accuracy = _finite(model.get("directional_accuracy"))
    mape = _finite(model.get("mape"), 100.0)
    if status in {"RETIRED", "CHAMPION"}:
        return {"promote": False, "status": status, "reason": "no automatic promotion for current status"}
    if samples < PROMOTION_MIN_SAMPLE:
        return {"promote": False, "status": status, "reason": "insufficient out-of-sample evidence"}
    if accuracy < PROMOTION_MIN_ACCURACY or mape > PROMOTION_MAX_MAPE:
        return {"promote": False, "status": status, "reason": "promotion metrics below threshold"}
    if champion and accuracy <= _finite(champion.get("directional_accuracy")) and mape >= _finite(champion.get("mape"), 100.0):
        return {"promote": False, "status": status, "reason": "challenger does not beat champion"}
    return {"promote": False, "status": "ELIGIBLE_FOR_PROMOTION", "reason": "eligible for explicit governance promotion"}


def adaptive_portfolio_optimizer(opportunities: list[dict[str, Any]], portfolio: dict[str, Any], positions: list[dict[str, Any]], *, engine: str) -> dict[str, Any]:
    state = capital_engine_state(portfolio, positions, opportunities, engine)
    cash = state["cash"]
    equity = state["equity"]
    reserve = state["reserve_cash_required"]
    exposure_by_symbol = {_upper(p.get("symbol")): _finite(p.get("market_value") or _finite(p.get("quantity")) * _finite(p.get("current_price"))) for p in positions}
    sector_exposure: dict[str, float] = {}
    unknown_position_sector = False
    for p in positions:
        sector = str(p.get("sector") or "").strip()
        if engine == "stock" and not sector:
            unknown_position_sector = True
            sector = "__UNKNOWN_SECTOR__"
        sector = sector or "Unknown"
        sector_exposure[sector] = sector_exposure.get(sector, 0.0) + _finite(p.get("market_value") or _finite(p.get("quantity")) * _finite(p.get("current_price")))
    allocations = []
    recalc_count = 0
    rejections: list[dict[str, Any]] = []
    for item in sorted(opportunities, key=lambda row: _finite(row.get("soft_score") or row.get("opportunity_score")), reverse=True):
        if classify_capital_engine(item) != engine or item.get("qualified_for_capital") is not True:
            continue
        symbol = _upper(item.get("symbol"))
        if engine == "stock" and unknown_position_sector:
            rejections.append({"symbol": symbol, "reason": "unknown existing position sector prevents concentration calculation"})
            continue
        if not hard_risk_gate(item).get("allowed"):
            continue
        current = exposure_by_symbol.get(symbol, 0.0)
        max_position = equity * GLOBAL_PIT_MAX_POSITION_PCT
        if current >= max_position:
            continue
        candidate_amount = min(cash - reserve, max_position - current, equity * GLOBAL_PIT_PREFERRED_POSITION_PCT)
        if candidate_amount <= 0:
            break
        capacity = liquidity_capacity(item, candidate_amount)
        if not capacity["allowed"]:
            continue
        executable_amount = min(candidate_amount, _finite(capacity.get("executable_order_value")))
        if executable_amount <= 0:
            continue
        sector = str(item.get("sector") or "").strip()
        if engine == "stock" and not sector:
            rejections.append({"symbol": symbol, "reason": "unknown candidate sector prevents concentration calculation"})
            continue
        sector = sector or "Unknown"
        if equity and (sector_exposure.get(sector, 0.0) + executable_amount) / equity > MAX_SECTOR_EXPOSURE_PCT:
            rejections.append({"symbol": symbol, "reason": "sector concentration limit"})
            continue
        allocations.append({"symbol": symbol, "amount": round(executable_amount, 2), "sector": sector, "liquidity": capacity})
        cash -= executable_amount
        exposure_by_symbol[symbol] = current + executable_amount
        sector_exposure[sector] = sector_exposure.get(sector, 0.0) + executable_amount
        recalc_count += 1
    return {**state, "allocations": allocations, "recalculations": recalc_count, "cash_after_plan": round(cash, 2), "rejections": rejections}


def decision_funnel(records: list[dict[str, Any]]) -> dict[str, Any]:
    counts = {stage: 0 for stage in FUNNEL_STAGES}
    rejection_reasons: dict[str, int] = {}
    for record in records:
        reached = {str(stage).lower() for stage in (record.get("stages") or [])}
        event_stage = str(record.get("stage") or "").lower()
        if event_stage:
            reached.add(event_stage)
        if record.get("attention_level") in {"HOT", "CRITICAL"}:
            reached.add("active_hot")
        if record.get("qualified_for_capital"):
            reached.add("portfolio_approved")
        if record.get("quote_verified"):
            reached.add("verified_quote")
        reached.add("surveillance")
        for index, stage in enumerate(FUNNEL_STAGES):
            required = set(FUNNEL_STAGES[: index + 1])
            if required.issubset(reached):
                counts[stage] += 1
        for reason in record.get("rejection_reasons") or ([] if not record.get("rejection_reason") else [record.get("rejection_reason")]):
            rejection_reasons[str(reason)] = rejection_reasons.get(str(reason), 0) + 1
    return {"counts": counts, "rejection_reasons": rejection_reasons}


def reserve_provider_budget(ledger: dict[str, Any] | None, provider: str, capability: str, *, database_url_configured: bool = False) -> dict[str, Any]:
    if database_url_configured and ledger is None:
        return {"reserved": False, "reason": "shared provider ledger unavailable; fail closed"}
    ledger = ledger if ledger is not None else {}
    key = f"{provider}:{capability}"
    entry = ledger.setdefault(key, {"remaining": 0, "used": 0, "cooldown": False})
    if entry.get("cooldown"):
        return {"reserved": False, "reason": "provider capability in cooldown", "entry": entry}
    if int(entry.get("remaining") or 0) <= 0:
        return {"reserved": False, "reason": "provider capability budget exhausted", "entry": entry}
    entry["remaining"] = int(entry.get("remaining") or 0) - 1
    entry["used"] = int(entry.get("used") or 0) + 1
    entry["last_reserved_at"] = utc_now()
    return {"reserved": True, "entry": entry}


def reserve_provider_budget_db(conn: Any, provider: str, capability: str, *, daily_budget: int, entitlement: str = "configured", data_mode: str = "unknown", now: datetime | None = None) -> dict[str, Any]:
    now_dt = now or datetime.now(timezone.utc)
    utc_date = now_dt.date().isoformat()
    timestamp = now_dt.isoformat()
    budget = max(0, int(daily_budget))
    existing = conn.execute(
        """SELECT * FROM provider_budget_ledger
           WHERE provider=%s AND capability=%s AND utc_date=%s
           FOR UPDATE""",
        (provider, capability, utc_date),
    ).fetchone()
    if not existing:
        conn.execute(
            """INSERT INTO provider_budget_ledger
               (provider,capability,utc_date,entitlement,daily_budget,requests_used,remaining_budget,data_mode,updated_at)
               VALUES (%s,%s,%s,%s,%s,0,%s,%s,%s)""",
            (provider, capability, utc_date, entitlement, budget, budget, data_mode, timestamp),
        )
        existing = {"requests_used": 0, "remaining_budget": budget, "cooldown_until": None}
    cooldown_until = _parse_aware_datetime(existing.get("cooldown_until"))
    if cooldown_until and cooldown_until > now_dt.astimezone(timezone.utc):
        return {"reserved": False, "reason": "provider capability in cooldown", "remaining": int(existing.get("remaining_budget") or 0)}
    remaining = int(existing.get("remaining_budget") or 0)
    if remaining <= 0:
        return {"reserved": False, "reason": "provider capability budget exhausted", "remaining": 0}
    conn.execute(
        """UPDATE provider_budget_ledger
           SET requests_used=requests_used+1,
               remaining_budget=remaining_budget-1,
               updated_at=%s
           WHERE provider=%s AND capability=%s AND utc_date=%s AND remaining_budget > 0""",
        (timestamp, provider, capability, utc_date),
    )
    return {"reserved": True, "remaining": remaining - 1, "provider": provider, "capability": capability}


PROVIDER_WIDE_CAPABILITY = "__provider__"


def provider_cooldown_active_db(conn: Any, provider: str, capability: str = PROVIDER_WIDE_CAPABILITY, *, now: datetime | None = None) -> dict[str, Any]:
    now_dt = now or datetime.now(timezone.utc)
    row = conn.execute(
        """SELECT cooldown_until, last_failure FROM provider_budget_ledger
           WHERE provider=%s AND capability=%s AND utc_date=%s""",
        (provider, capability, now_dt.date().isoformat()),
    ).fetchone()
    if not row:
        return {"active": False}
    cooldown_until = _parse_aware_datetime(row.get("cooldown_until"))
    if cooldown_until and cooldown_until > now_dt.astimezone(timezone.utc):
        return {
            "active": True,
            "cooldown_until": cooldown_until.isoformat(),
            "reason": row.get("last_failure") or "provider cooldown",
        }
    return {"active": False}


def mark_provider_cooldown_db(
    conn: Any,
    provider: str,
    *,
    seconds: int,
    reason: str = "provider cooldown",
    capability: str = PROVIDER_WIDE_CAPABILITY,
    now: datetime | None = None,
) -> dict[str, Any]:
    now_dt = now or datetime.now(timezone.utc)
    timestamp = now_dt.isoformat()
    utc_date = now_dt.date().isoformat()
    cooldown_until = (now_dt + timedelta(seconds=max(1, int(seconds)))).isoformat()
    conn.execute(
        """INSERT INTO provider_budget_ledger
           (provider,capability,utc_date,entitlement,daily_budget,requests_used,remaining_budget,last_failure,cooldown_until,data_mode,updated_at)
           VALUES (%s,%s,%s,%s,0,0,0,%s,%s,%s,%s)
           ON CONFLICT (provider, capability, utc_date) DO UPDATE SET
               last_failure=EXCLUDED.last_failure,
               cooldown_until=EXCLUDED.cooldown_until,
               updated_at=EXCLUDED.updated_at""",
        (provider, capability, utc_date, "configured", reason[:500], cooldown_until, "shared_cooldown", timestamp),
    )
    return {"active": True, "cooldown_until": cooldown_until, "reason": reason}


def reserve_provider_budget_live(provider: str, capability: str, *, daily_budget: int, entitlement: str = "configured", data_mode: str = "unknown") -> dict[str, Any]:
    if os.getenv("DATABASE_URL", "").strip():
        try:
            from database import connect
            with connect() as conn:
                return reserve_provider_budget_db(conn, provider, capability, daily_budget=daily_budget, entitlement=entitlement, data_mode=data_mode)
        except Exception as exc:
            return {"reserved": False, "reason": f"shared provider ledger unavailable; fail closed: {exc.__class__.__name__}"}
    return {"reserved": True, "reason": "DATABASE_URL not configured; shared provider ledger not required for local mode"}


def provider_cooldown_active_live(provider: str, capability: str = PROVIDER_WIDE_CAPABILITY) -> dict[str, Any]:
    if os.getenv("DATABASE_URL", "").strip():
        try:
            from database import connect
            with connect() as conn:
                return provider_cooldown_active_db(conn, provider, capability)
        except Exception as exc:
            return {"active": True, "reason": f"shared provider cooldown unavailable; fail closed: {exc.__class__.__name__}"}
    return {"active": False}


def mark_provider_cooldown_live(provider: str, *, seconds: int, reason: str = "provider cooldown", capability: str = PROVIDER_WIDE_CAPABILITY) -> dict[str, Any]:
    if os.getenv("DATABASE_URL", "").strip():
        try:
            from database import connect
            with connect() as conn:
                return mark_provider_cooldown_db(conn, provider, seconds=seconds, reason=reason, capability=capability)
        except Exception as exc:
            return {"active": True, "reason": f"shared provider cooldown unavailable; fail closed: {exc.__class__.__name__}"}
    return {"active": True, "reason": reason}


def persist_decision_event(conn: Any, *, market: str, symbol: str, stage: str, decision_id: str | None = None, payload: dict[str, Any] | None = None, rejection_reason: str | None = None) -> str:
    payload = payload or {}
    identity = canonical_identity({**payload, "symbol": symbol, "asset_class": payload.get("asset_class") or ("crypto" if market == "crypto" else "stock")})
    did = decision_id or _decision_id(market, symbol, payload.get("signal_id"), payload.get("created_at"))
    created_at = utc_now()
    reasons = [rejection_reason] if rejection_reason else list(payload.get("rejection_reasons") or [])
    conn.execute(
        """INSERT INTO global_asset_identities
           (canonical_id, asset_class, exchange, native_symbol, currency, provider_aliases, created_at, updated_at)
           VALUES (%s,%s,%s,%s,%s,%s::jsonb,%s,%s)
           ON CONFLICT (canonical_id) DO UPDATE SET provider_aliases=EXCLUDED.provider_aliases, updated_at=EXCLUDED.updated_at""",
        (identity["canonical_id"], identity["asset_class"], identity["exchange"], identity["native_symbol"], identity["currency"], _json(identity.get("provider_aliases") or {}), created_at, created_at),
    )
    conn.execute(
        """INSERT INTO global_decision_ledger
           (decision_id, scan_id, signal_id, forecast_id, execution_claim_id, trade_id, canonical_id, market, symbol, asset_class,
            features, portfolio_context, decision, rejection_reasons, created_at)
           VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s::jsonb,%s,%s::jsonb,%s)
           ON CONFLICT (decision_id) DO UPDATE SET decision=EXCLUDED.decision, rejection_reasons=EXCLUDED.rejection_reasons""",
        (
            did,
            payload.get("scan_id"),
            str(payload.get("signal_id")) if payload.get("signal_id") is not None else None,
            payload.get("forecast_id"),
            payload.get("execution_claim_id"),
            payload.get("trade_id"),
            identity["canonical_id"],
            market,
            symbol,
            identity["asset_class"],
            _json(payload.get("features") or payload),
            _json(payload.get("portfolio_context") or {}),
            stage,
            _json(reasons),
            created_at,
        ),
    )
    conn.execute(
        """INSERT INTO global_decision_events
           (decision_id, market, symbol, stage, rejection_reason, payload, created_at)
           VALUES (%s,%s,%s,%s,%s,%s::jsonb,%s)""",
        (did, market, symbol, stage, rejection_reason, _json(payload), created_at),
    )
    return did


def build_decision_funnel_from_events(events: list[dict[str, Any]]) -> dict[str, Any]:
    by_decision: dict[str, dict[str, Any]] = {}
    for event in events:
        did = str(event.get("decision_id") or f"{event.get('market')}:{event.get('symbol')}")
        record = by_decision.setdefault(did, {"stages": [], "rejection_reasons": []})
        stage = str(event.get("stage") or "").lower()
        if stage:
            record["stages"].append(stage)
        if event.get("rejection_reason"):
            record["rejection_reasons"].append(event.get("rejection_reason"))
    return decision_funnel(list(by_decision.values()))


def record_invalid_symbol_failure(conn: Any, *, symbol: str, provider: str, failure_type: str, retry_after_seconds: int = 3600) -> dict[str, Any]:
    timestamp = utc_now()
    retry_after = (datetime.now(timezone.utc) + timedelta(seconds=max(60, int(retry_after_seconds)))).isoformat()
    conn.execute(
        """INSERT INTO invalid_symbol_quarantine
           (symbol, provider, failure_type, failure_count, last_failure, retry_after)
           VALUES (%s,%s,%s,1,%s,%s)
           ON CONFLICT(symbol, provider, failure_type)
           DO UPDATE SET failure_count=invalid_symbol_quarantine.failure_count+1,
                         last_failure=EXCLUDED.last_failure,
                         retry_after=EXCLUDED.retry_after""",
        (_upper(symbol), provider, failure_type, timestamp, retry_after),
    )
    return {"symbol": _upper(symbol), "provider": provider, "failure_type": failure_type, "retry_after": retry_after}



def v39_dashboard_summary(stock_engine: dict[str, Any], crypto_engine: dict[str, Any], funnel: dict[str, Any], provider_states: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    return {
        "stock_capital_deployment": stock_engine,
        "crypto_capital_deployment": crypto_engine,
        "decision_funnel": funnel,
        "provider_budget_states": provider_states or [],
        "broker_submission_enabled": ENABLE_BROKER_SUBMISSION,
    }


def ensure_v39_tables(conn: Any) -> None:
    statements = [
        """CREATE TABLE IF NOT EXISTS global_asset_identities (
            canonical_id TEXT PRIMARY KEY, asset_class TEXT NOT NULL, exchange TEXT NOT NULL,
            native_symbol TEXT NOT NULL, currency TEXT NOT NULL, provider_aliases JSONB DEFAULT '{}'::jsonb,
            created_at TEXT NOT NULL, updated_at TEXT NOT NULL)""",
        """CREATE TABLE IF NOT EXISTS global_decision_ledger (
            decision_id TEXT PRIMARY KEY, scan_id TEXT, signal_id TEXT, forecast_id TEXT,
            execution_claim_id TEXT, trade_id BIGINT, canonical_id TEXT, market TEXT, symbol TEXT,
            asset_class TEXT, features JSONB, portfolio_context JSONB, decision TEXT,
            rejection_reasons JSONB DEFAULT '[]'::jsonb, created_at TEXT NOT NULL)""",
        """CREATE TABLE IF NOT EXISTS global_forecast_outcomes (
            id BIGSERIAL PRIMARY KEY, decision_id TEXT, forecast_id TEXT, symbol TEXT,
            horizon TEXT, realized_return_pct DOUBLE PRECISION, absolute_forecast_error DOUBLE PRECISION,
            direction_correct BOOLEAN, mfe_pct DOUBLE PRECISION, mae_pct DOUBLE PRECISION,
            drawdown_pct DOUBLE PRECISION, payload JSONB, created_at TEXT NOT NULL)""",
        """CREATE TABLE IF NOT EXISTS global_model_governance (
            model_key TEXT PRIMARY KEY, status TEXT NOT NULL DEFAULT 'SHADOW', champion BOOLEAN DEFAULT FALSE,
            sample_count INTEGER DEFAULT 0, directional_accuracy DOUBLE PRECISION DEFAULT 0,
            mape DOUBLE PRECISION DEFAULT 100, calibration_error DOUBLE PRECISION DEFAULT 100,
            payload JSONB, updated_at TEXT NOT NULL)""",
        """CREATE TABLE IF NOT EXISTS provider_budget_ledger (
            provider TEXT NOT NULL, capability TEXT NOT NULL, utc_date TEXT NOT NULL,
            entitlement TEXT, daily_budget INTEGER DEFAULT 0, requests_used INTEGER DEFAULT 0,
            remaining_budget INTEGER DEFAULT 0, latency_ms DOUBLE PRECISION, last_success TEXT,
            last_failure TEXT, cooldown_until TEXT, data_mode TEXT, updated_at TEXT NOT NULL,
            PRIMARY KEY(provider, capability, utc_date))""",
        """CREATE TABLE IF NOT EXISTS global_decision_events (
            id BIGSERIAL PRIMARY KEY, decision_id TEXT NOT NULL, market TEXT, symbol TEXT,
            stage TEXT NOT NULL, rejection_reason TEXT, payload JSONB, created_at TEXT NOT NULL)""",
        """CREATE TABLE IF NOT EXISTS invalid_symbol_quarantine (
            symbol TEXT NOT NULL, provider TEXT NOT NULL, failure_type TEXT NOT NULL,
            failure_count INTEGER NOT NULL DEFAULT 1, last_failure TEXT NOT NULL, retry_after TEXT NOT NULL,
            PRIMARY KEY(symbol, provider, failure_type))""",
        "CREATE INDEX IF NOT EXISTS idx_global_decision_symbol_created ON global_decision_ledger (symbol, created_at DESC)",
        "CREATE INDEX IF NOT EXISTS idx_global_outcomes_decision ON global_forecast_outcomes (decision_id)",
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_global_outcomes_decision_horizon ON global_forecast_outcomes (decision_id, horizon)",
        "CREATE INDEX IF NOT EXISTS idx_global_outcomes_forecast_horizon ON global_forecast_outcomes (forecast_id, horizon)",
        "CREATE INDEX IF NOT EXISTS idx_provider_budget_capability ON provider_budget_ledger (provider, capability, utc_date)",
        "CREATE INDEX IF NOT EXISTS idx_global_decision_events_created ON global_decision_events (created_at DESC)",
        "CREATE INDEX IF NOT EXISTS idx_global_decision_events_decision ON global_decision_events (decision_id, stage)",
        "CREATE INDEX IF NOT EXISTS idx_global_decision_events_market_symbol_stage ON global_decision_events (market, symbol, stage, created_at DESC)",
        "CREATE INDEX IF NOT EXISTS idx_invalid_symbol_retry ON invalid_symbol_quarantine (retry_after)",
        "CREATE INDEX IF NOT EXISTS idx_invalid_symbol_provider_retry ON invalid_symbol_quarantine (provider, retry_after)",
    ]
    for statement in statements:
        conn.execute(statement)
