from __future__ import annotations

from dataclasses import dataclass, asdict
from datetime import datetime, timezone
import json
import math
from typing import Any, Iterable

from config import (
    DECISION_CRYPTO_MAX_AGE_MINUTES,
    DECISION_STOCK_MAX_AGE_MINUTES,
    ENABLE_BROKER_SUBMISSION,
    GLOBAL_PIT_CRITICAL_ATTENTION_SCORE,
    GLOBAL_PIT_DEEP_RESEARCH_SECONDS,
    GLOBAL_PIT_FAST_LOOP_SECONDS,
    GLOBAL_PIT_HOT_ATTENTION_SCORE,
    GLOBAL_PIT_MARKET_LOOP_SECONDS,
    GLOBAL_PIT_MAX_PARALLEL_LANES,
    GLOBAL_PIT_MAX_POSITION_PCT,
    GLOBAL_PIT_PREFERRED_POSITION_PCT,
    GLOBAL_PIT_RESERVE_PCT,
    GLOBAL_PIT_ROTATION_MIN_ADVANTAGE_PCT,
    GLOBAL_PIT_TARGET_INVESTED_PCT,
)
from market_sessions import market_session_state, quote_freshness_seconds, quote_is_fresh

SUPPORTED_EXECUTION_ASSETS = {"stock", "equity", "etf", "crypto"}
INTELLIGENCE_ONLY_ASSETS = {
    "forex",
    "commodity",
    "commodities",
    "fixed_income",
    "rates",
    "index",
    "future",
    "futures",
    "option",
    "options",
    "mutual_fund",
}
LANES = {
    "us_equities": {"stock", "equity", "etf"},
    "global_equities": {"stock", "equity", "etf", "adr"},
    "crypto": {"crypto"},
    "macro_rates": {"fixed_income", "rates", "index"},
    "commodities": {"commodity", "commodities", "future", "futures"},
    "forex": {"forex"},
    "news_events": {"news", "event"},
    "options_flow": {"option", "options"},
    "portfolio_risk": {"portfolio"},
}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _upper(value: Any) -> str:
    return str(value or "").strip().upper()


def _asset_class(value: Any) -> str:
    text = str(value or "stock").strip().lower().replace(" ", "_")
    if text in {"cash", "us_stock", "international_stock"}:
        return "stock"
    if text in {"fund", "etfs"}:
        return "etf"
    if text in {"fx", "currency"}:
        return "forex"
    return text or "stock"


def _finite(value: Any, default: float = 0.0) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    if not math.isfinite(number):
        return default
    return number


def _clamp(value: float, low: float = 0.0, high: float = 100.0) -> float:
    return max(low, min(high, value))

def _percent_score(value: Any, default: float = 0.0) -> float:
    number = _finite(value, default)
    if 0.0 <= number <= 1.0:
        number *= 100.0
    return _clamp(number)


def _quote_identity_matches(asset: dict[str, Any]) -> bool:
    symbol = _upper(asset.get("symbol"))
    requested = _upper(asset.get("requested_symbol"))
    provider = _upper(asset.get("provider_symbol"))
    return bool(symbol and requested and provider and symbol == requested == provider)


def _execution_quote_eligible(asset: dict[str, Any], now: datetime | None = None) -> bool:
    if asset.get("quote_verified") is not True or not _quote_identity_matches(asset):
        return False
    symbol = _upper(asset.get("symbol"))
    interval = str(asset.get("source_interval") or asset.get("interval") or "").strip()
    if not interval or not asset.get("quote_timestamp"):
        return False
    cls = _asset_class(asset.get("asset_class"))
    max_age_seconds = (
        DECISION_CRYPTO_MAX_AGE_MINUTES if cls == "crypto" else DECISION_STOCK_MAX_AGE_MINUTES
    ) * 60
    return quote_is_fresh(
        asset.get("quote_timestamp"),
        interval,
        now=now,
        max_intraday_age_seconds=max_age_seconds,
        exchange=asset.get("exchange") or "",
        region=asset.get("region") or asset.get("country") or "",
        symbol=symbol,
    )



def _list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
            if isinstance(parsed, list):
                return [str(x) for x in parsed if x]
        except Exception:
            return [item.strip() for item in value.split(",") if item.strip()]
    if isinstance(value, Iterable) and not isinstance(value, (dict, bytes)):
        return [str(x) for x in value if x]
    return [str(value)]


def quote_freshness_label(record: dict[str, Any], *, live_max_age_seconds: int = 180, delayed_max_age_seconds: int = 900) -> dict[str, Any]:
    """Truthful dashboard freshness label; unknown never becomes live."""
    verified = record.get("quote_verified") is True
    age = record.get("quote_age_seconds")
    if age is None:
        age = quote_freshness_seconds(record.get("quote_timestamp"))
    if age is None:
        return {"label": "FRESHNESS UNKNOWN", "fresh": False, "delayed": False, "age_seconds": None}
    age = _finite(age, 10**9)
    interval = str(record.get("interval") or record.get("source_interval") or "").lower()
    provider_mode = str(record.get("provider_mode") or record.get("mode") or "").lower()
    is_eod = "eod" in provider_mode or interval in {"1d", "daily", "day"}
    if verified and age <= live_max_age_seconds and not is_eod:
        return {"label": "LIVE DATA", "fresh": True, "delayed": False, "age_seconds": age}
    if verified and age <= delayed_max_age_seconds:
        return {"label": "DELAYED DATA", "fresh": False, "delayed": True, "age_seconds": age}
    return {"label": "OLD DATA", "fresh": False, "delayed": False, "age_seconds": age}


@dataclass
class GlobalAsset:
    symbol: str
    name: str = ""
    asset_class: str = "stock"
    exchange: str = ""
    country: str = ""
    currency: str = "USD"
    sector: str = "Unknown"
    industry: str = ""
    market_cap: float = 0.0
    liquidity: float = 0.0
    provider_support: list[str] | None = None
    discovery_sources: list[str] | None = None
    mover_tags: list[str] | None = None
    attention_score: float = 0.0
    opportunity_score: float = 0.0
    data_quality_score: float = 0.0
    last_scanned: str = ""
    quote_timestamp: str = ""
    quote_verified: bool = False
    quote_age_seconds: float | None = None
    market_session: str = "unknown"
    tradeable: bool = False

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["provider_support"] = self.provider_support or []
        payload["discovery_sources"] = self.discovery_sources or []
        payload["mover_tags"] = self.mover_tags or []
        return payload


def build_global_universe(seed_watchlists: dict[str, Any] | None = None, provider_discoveries: list[dict[str, Any]] | None = None) -> list[dict[str, Any]]:
    """Merge static seeds with provider discoveries without depending on either alone."""
    merged: dict[str, dict[str, Any]] = {}
    for symbol, name in (seed_watchlists or {}).items():
        key = _upper(symbol)
        if not key:
            continue
        merged[key] = GlobalAsset(symbol=key, name=str(name or key), discovery_sources=["seed_watchlist"]).to_dict()
    for item in provider_discoveries or []:
        key = _upper(item.get("symbol"))
        if not key:
            continue
        asset_class = _asset_class(item.get("asset_class") or item.get("category") or item.get("type") or "stock")
        current = merged.setdefault(key, GlobalAsset(symbol=key).to_dict())
        current.update({k: v for k, v in item.items() if v not in (None, "", [])})
        current["symbol"] = key
        current["asset_class"] = asset_class
        sources = set(_list(current.get("discovery_sources")))
        sources.add(str(item.get("discovery_source") or "provider_discovery"))
        current["discovery_sources"] = sorted(sources)
        providers = set(_list(current.get("provider_support"))) | set(_list(item.get("provider") or item.get("provider_support")))
        current["provider_support"] = sorted(providers)
    return list(merged.values())


def supported_for_paper_execution(asset: dict[str, Any]) -> bool:
    cls = _asset_class(asset.get("asset_class"))
    return cls in SUPPORTED_EXECUTION_ASSETS and asset.get("tradeable") is True


def session_for_asset(asset: dict[str, Any], now: datetime | None = None) -> str:
    cls = _asset_class(asset.get("asset_class"))
    if cls == "crypto":
        return "regular"
    return str(asset.get("market_session") or market_session_state(now, asset.get("exchange"), asset.get("region") or asset.get("country"), asset.get("symbol")))


def attention_for_asset(asset: dict[str, Any], now: datetime | None = None) -> dict[str, Any]:
    session = session_for_asset(asset, now)
    price_move = abs(_finite(asset.get("price_acceleration") or asset.get("change_1d_pct")))
    volume = _finite(asset.get("volume_acceleration") or asset.get("relative_volume"), 1.0)
    volatility = abs(_finite(asset.get("volatility") or asset.get("volatility_pct")))
    news = abs(_finite(asset.get("news_intensity")))
    sentiment = abs(_finite(asset.get("sentiment_shift")))
    options = abs(_finite(asset.get("options_activity")))
    forecast = abs(_finite(asset.get("forecast_change") or asset.get("expected_move_pct")))
    opportunity = _finite(asset.get("opportunity_score")) / 100.0
    score = (
        min(22.0, price_move * 3.0)
        + min(18.0, max(0.0, volume - 1.0) * 12.0)
        + min(12.0, volatility * 1.5)
        + min(12.0, news * 4.0)
        + min(8.0, sentiment * 4.0)
        + min(8.0, options * 2.0)
        + min(10.0, forecast * 1.2)
        + min(10.0, opportunity * 10.0)
    )
    if session in {"regular", "premarket"}:
        score += 8.0
    elif session == "after-hours":
        score *= 0.75
    elif session == "closed":
        score *= 0.55
    cls = _asset_class(asset.get("asset_class"))
    if cls == "crypto":
        score += 10.0
    score = _clamp(score)
    if score >= GLOBAL_PIT_CRITICAL_ATTENTION_SCORE:
        level = "CRITICAL"
    elif score >= GLOBAL_PIT_HOT_ATTENTION_SCORE:
        level = "HOT"
    elif score >= 40:
        level = "ACTIVE"
    else:
        level = "NORMAL"
    quote_interval = GLOBAL_PIT_FAST_LOOP_SECONDS if level in {"HOT", "CRITICAL"} else GLOBAL_PIT_MARKET_LOOP_SECONDS
    if session == "after-hours" and cls != "crypto":
        quote_interval = max(quote_interval * 2, GLOBAL_PIT_MARKET_LOOP_SECONDS * 2)
    if session == "closed" and cls != "crypto":
        quote_interval = max(quote_interval * 4, GLOBAL_PIT_DEEP_RESEARCH_SECONDS // 2)
    return {
        "symbol": _upper(asset.get("symbol")),
        "attention_score": round(score, 2),
        "attention_level": level,
        "market_session": session,
        "quote_poll_seconds": int(quote_interval),
        "research_poll_seconds": int(GLOBAL_PIT_DEEP_RESEARCH_SECONDS if level in {"NORMAL", "ACTIVE"} else GLOBAL_PIT_MARKET_LOOP_SECONDS),
    }


def deduplicate_provider_requests(requests: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[tuple[str, str, str, str, str]] = set()
    out: list[dict[str, Any]] = []
    for request in requests:
        key = (
            str(request.get("provider") or "").lower(),
            _upper(request.get("symbol")),
            str(request.get("data_type") or request.get("capability") or "quote").lower(),
            str(request.get("interval") or "").lower(),
            str(request.get("mode") or "").lower(),
        )
        if key in seen:
            continue
        seen.add(key)
        out.append(request)
    return out


def schedule_scanning_lanes(assets: list[dict[str, Any]], provider_budget: dict[str, int] | None = None, now: datetime | None = None) -> list[dict[str, Any]]:
    remaining = {str(k): int(v) for k, v in (provider_budget or {}).items()}
    lanes: dict[str, list[dict[str, Any]]] = {name: [] for name in LANES}
    for asset in assets:
        attention = attention_for_asset(asset, now)
        enriched = {**asset, **attention}
        cls = _asset_class(asset.get("asset_class"))
        lane = "crypto" if cls == "crypto" else "us_equities" if str(asset.get("country") or asset.get("region") or "").lower() in {"", "united states", "north america"} and cls in {"stock", "equity", "etf"} else None
        if lane is None:
            for name, classes in LANES.items():
                if cls in classes:
                    lane = name
                    break
        lane = lane or "news_events"
        provider = str(asset.get("preferred_provider") or asset.get("provider") or "shared")
        if remaining and remaining.get(provider, 1) <= 0 and attention["attention_level"] not in {"HOT", "CRITICAL"}:
            enriched["deferred_reason"] = "provider budget reserved for higher attention assets"
            enriched["quote_poll_seconds"] = max(int(enriched["quote_poll_seconds"]), GLOBAL_PIT_DEEP_RESEARCH_SECONDS)
        elif provider in remaining:
            remaining[provider] -= 1
        lanes[lane].append(enriched)
    planned = []
    for lane, items in lanes.items():
        if not items:
            continue
        items.sort(key=lambda x: _finite(x.get("attention_score")), reverse=True)
        planned.append({"lane": lane, "max_workers": min(GLOBAL_PIT_MAX_PARALLEL_LANES, max(1, len(items))), "assets": items})
    return planned


def _ranking_score(asset: dict[str, Any], learning_weights: dict[str, float] | None = None) -> float:
    weights = {"edge": 0.22, "probability": 0.14, "confidence": 0.12, "data": 0.12, "liquidity": 0.10, "risk": 0.10, "regime": 0.08, "portfolio_fit": 0.12}
    for key, value in (learning_weights or {}).items():
        if key in weights:
            weights[key] = max(0.0, min(0.40, float(value)))
    total = sum(weights.values()) or 1.0
    weights = {key: value / total for key, value in weights.items()}
    raw_risk = asset.get("risk_score") if asset.get("risk_score") is not None else asset.get("risk_level_score")
    risk_score = _clamp(_finite(raw_risk, 100.0))
    liquidity_value = _finite(asset.get("liquidity") or asset.get("avg_dollar_volume"))
    values = {
        "edge": _finite(asset.get("expected_edge") or asset.get("expected_move_pct") or asset.get("change_1d_pct")) * 10.0,
        "probability": _percent_score(asset.get("probability_up")),
        "confidence": _percent_score(asset.get("confidence")),
        "data": _percent_score(asset.get("data_quality_score")),
        "liquidity": _clamp((liquidity_value / 1_000_000.0) * 100.0),
        "risk": 100.0 - risk_score,
        "regime": _percent_score(asset.get("regime_score")),
        "portfolio_fit": _percent_score(asset.get("portfolio_fit")),
    }
    return _clamp(sum(_clamp(values[key]) * weights[key] for key in weights))


def rank_global_opportunities(assets: list[dict[str, Any]], learning_weights: dict[str, float] | None = None, now: datetime | None = None) -> list[dict[str, Any]]:
    ranked = []
    for asset in assets:
        attention = attention_for_asset(asset, now)
        freshness = quote_freshness_label(asset)
        supported = supported_for_paper_execution(asset)
        data_ok = _execution_quote_eligible(asset, now)
        liquidity = _finite(asset.get("liquidity") or asset.get("avg_dollar_volume"))
        score = _ranking_score(asset, learning_weights) + min(12.0, attention["attention_score"] * 0.12)
        if not data_ok:
            score *= 0.60
        payload = {
            **asset,
            **attention,
            "opportunity_score": round(_clamp(score), 2),
            "data_label": freshness["label"],
            "paper_execution_supported": supported,
            "qualified_for_capital": bool(supported and data_ok and liquidity > 0),
            "execution_mode": "paper_only" if supported else "intelligence_only",
        }
        ranked.append(payload)
    ranked.sort(key=lambda item: (_finite(item.get("opportunity_score")), item.get("attention_level") == "CRITICAL"), reverse=True)
    for index, item in enumerate(ranked, 1):
        item["global_rank"] = index
    return ranked


def capital_deployment_plan(queue: list[dict[str, Any]], equity: float, cash: float, positions: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    equity = max(0.0, _finite(equity))
    cash = max(0.0, _finite(cash))
    positions = positions or []
    invested = max(0.0, equity - cash)
    target_invested = equity * GLOBAL_PIT_TARGET_INVESTED_PCT
    reserve_cash = equity * GLOBAL_PIT_RESERVE_PCT
    deployable = max(0.0, min(cash - reserve_cash, target_invested - invested))
    existing = {_upper(p.get("symbol")): _finite(p.get("market_value") or _finite(p.get("quantity")) * _finite(p.get("current_price"))) for p in positions}
    allocations = []
    for item in queue:
        if deployable <= 0:
            break
        if not item.get("qualified_for_capital"):
            continue
        symbol = _upper(item.get("symbol"))
        current_value = existing.get(symbol, 0.0)
        max_value = equity * GLOBAL_PIT_MAX_POSITION_PCT
        if current_value >= max_value:
            continue
        preferred = equity * GLOBAL_PIT_PREFERRED_POSITION_PCT * (_finite(item.get("opportunity_score")) / 100.0)
        amount = min(deployable, max(0.0, max_value - current_value), max(preferred, equity * 0.01))
        if amount <= 0:
            continue
        allocations.append({"symbol": symbol, "amount": round(amount, 2), "reason": "qualified global queue opportunity", "score": item.get("opportunity_score")})
        deployable -= amount
    return {
        "target_invested_pct": GLOBAL_PIT_TARGET_INVESTED_PCT,
        "reserve_pct": GLOBAL_PIT_RESERVE_PCT,
        "current_invested_pct": (invested / equity) if equity else 0.0,
        "deployment_gap": round(max(0.0, target_invested - invested), 2),
        "reserve_cash_required": round(reserve_cash, 2),
        "allocations": allocations,
        "qualified_assets_used": len(allocations),
    }


def rotation_requires_advantage(current_holding: dict[str, Any], candidate: dict[str, Any], estimated_cost_pct: float = 0.5) -> dict[str, Any]:
    held_score = _finite(current_holding.get("opportunity_score"))
    new_score = _finite(candidate.get("opportunity_score"))
    improvement = new_score - held_score
    required = GLOBAL_PIT_ROTATION_MIN_ADVANTAGE_PCT + max(0.0, _finite(estimated_cost_pct))
    allowed = bool(candidate.get("qualified_for_capital") and improvement >= required)
    return {"rotate": allowed, "improvement_pct": round(improvement, 2), "required_advantage_pct": round(required, 2)}


def learning_weights_from_observations(observations: list[dict[str, Any]]) -> dict[str, float]:
    weights = {"edge": 0.22, "probability": 0.14, "confidence": 0.12, "data": 0.12, "liquidity": 0.10, "risk": 0.10, "regime": 0.08, "portfolio_fit": 0.12}
    for item in observations:
        feature = str(item.get("feature") or "").strip().lower()
        if feature not in weights:
            continue
        samples = _finite(item.get("sample_count"))
        if samples < 10:
            continue
        edge = _finite(item.get("realized_edge_pct"))
        adjustment = max(-0.05, min(0.05, edge / 100.0))
        weights[feature] = max(0.02, min(0.30, weights[feature] + adjustment))
    return weights


def hard_risk_gate(signal: dict[str, Any], now: datetime | None = None) -> dict[str, Any]:
    freshness = quote_freshness_label(signal)
    reasons = []
    if signal.get("quote_verified") is not True:
        reasons.append("verified quote required")
    if not _quote_identity_matches(signal):
        reasons.append("quote symbol identity must match")
    if not _execution_quote_eligible(signal, now):
        reasons.append("execution-fresh quote required")
    if _finite(signal.get("liquidity") or signal.get("avg_dollar_volume")) <= 0:
        reasons.append("verified liquidity required")
    if not supported_for_paper_execution(signal):
        reasons.append("unsupported asset class cannot execute")
    if ENABLE_BROKER_SUBMISSION:
        reasons.append("broker submission must remain disabled for V38 paper mode")
    return {"allowed": not reasons, "reasons": reasons, "data_label": freshness["label"]}


def dashboard_activity_labels(state: dict[str, Any]) -> dict[str, str]:
    execution_enabled = bool(state.get("execution_enabled"))
    labels = {
        "Scanning": "Active" if state.get("scans_completed_today", 0) else "Waiting for scanner activity",
        "Researching": "Active" if state.get("research_events_persisted", 0) else "Waiting for persisted research",
        "Learning": "Active" if state.get("learning_observations_persisted", 0) else "Waiting for evaluated outcomes",
        "Allocating": "Paper planning only" if state.get("qualified_allocations", 0) else "Waiting for verified entry",
        "Rebalancing": "Evaluating rotations" if state.get("rotation_candidates", 0) else "No justified rotation",
        "Trading": "Paper execution disabled" if not execution_enabled else "Paper execution gates enabled",
    }
    return labels


def ensure_global_pit_tables(conn: Any) -> None:
    conn.execute("""CREATE TABLE IF NOT EXISTS global_financial_universe (
        symbol TEXT PRIMARY KEY, asset_class TEXT, exchange TEXT, country TEXT, currency TEXT,
        sector TEXT, industry TEXT, market_cap DOUBLE PRECISION, liquidity DOUBLE PRECISION,
        provider_support JSONB DEFAULT '[]'::jsonb, discovery_sources JSONB DEFAULT '[]'::jsonb,
        last_scanned TEXT, attention_score DOUBLE PRECISION, opportunity_score DOUBLE PRECISION,
        data_quality_score DOUBLE PRECISION, payload JSONB, updated_at TEXT NOT NULL)""")
    conn.execute("""CREATE TABLE IF NOT EXISTS global_opportunity_queue (
        symbol TEXT PRIMARY KEY, asset_class TEXT, market TEXT, sector TEXT, country TEXT,
        attention_level TEXT, attention_score DOUBLE PRECISION, opportunity_score DOUBLE PRECISION,
        data_label TEXT, paper_execution_supported BOOLEAN, qualified_for_capital BOOLEAN,
        payload JSONB, updated_at TEXT NOT NULL)""")
    conn.execute("""CREATE TABLE IF NOT EXISTS global_market_map (
        bucket TEXT PRIMARY KEY, strength_score DOUBLE PRECISION, regime TEXT, payload JSONB, updated_at TEXT NOT NULL)""")
    conn.execute("""CREATE TABLE IF NOT EXISTS global_learning_observations (
        id SERIAL PRIMARY KEY, symbol TEXT, asset_class TEXT, sector TEXT, regime TEXT, feature TEXT,
        predicted_move_pct DOUBLE PRECISION, actual_move_pct DOUBLE PRECISION,
        realized_edge_pct DOUBLE PRECISION, sample_count INTEGER DEFAULT 1, payload JSONB, created_at TEXT NOT NULL)""")
    conn.execute("""CREATE TABLE IF NOT EXISTS global_pit_activity (
        id INTEGER PRIMARY KEY DEFAULT 1, scans_completed_today INTEGER DEFAULT 0,
        research_events_persisted INTEGER DEFAULT 0, learning_observations_persisted INTEGER DEFAULT 0,
        qualified_allocations INTEGER DEFAULT 0, rotation_candidates INTEGER DEFAULT 0,
        last_scan_at TEXT, last_research_at TEXT, last_learning_at TEXT, updated_at TEXT NOT NULL)""")


def persist_global_pit_state(conn: Any, universe: list[dict[str, Any]], queue: list[dict[str, Any]], *, now: str | None = None) -> None:
    timestamp = now or utc_now()
    ensure_global_pit_tables(conn)
    for asset in universe:
        symbol = _upper(asset.get("symbol"))
        if not symbol:
            continue
        conn.execute(
            """INSERT INTO global_financial_universe
            (symbol, asset_class, exchange, country, currency, sector, industry, market_cap, liquidity,
             provider_support, discovery_sources, last_scanned, attention_score, opportunity_score, data_quality_score, payload, updated_at)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s::jsonb,%s,%s,%s,%s,%s::jsonb,%s)
            ON CONFLICT (symbol) DO UPDATE SET asset_class=EXCLUDED.asset_class, exchange=EXCLUDED.exchange,
            country=EXCLUDED.country, currency=EXCLUDED.currency, sector=EXCLUDED.sector, industry=EXCLUDED.industry,
            market_cap=EXCLUDED.market_cap, liquidity=EXCLUDED.liquidity, provider_support=EXCLUDED.provider_support,
            discovery_sources=EXCLUDED.discovery_sources, last_scanned=EXCLUDED.last_scanned,
            attention_score=EXCLUDED.attention_score, opportunity_score=EXCLUDED.opportunity_score,
            data_quality_score=EXCLUDED.data_quality_score, payload=EXCLUDED.payload, updated_at=EXCLUDED.updated_at""",
            (symbol, _asset_class(asset.get("asset_class")), asset.get("exchange"), asset.get("country") or asset.get("region"),
             asset.get("currency"), asset.get("sector"), asset.get("industry"), _finite(asset.get("market_cap")),
             _finite(asset.get("liquidity") or asset.get("avg_dollar_volume")), json.dumps(_list(asset.get("provider_support"))),
             json.dumps(_list(asset.get("discovery_sources"))), asset.get("last_scanned") or timestamp,
             _finite(asset.get("attention_score")), _finite(asset.get("opportunity_score")), _finite(asset.get("data_quality_score")),
             json.dumps(asset), timestamp),
        )
    for item in queue:
        symbol = _upper(item.get("symbol"))
        if not symbol:
            continue
        conn.execute(
            """INSERT INTO global_opportunity_queue
            (symbol, asset_class, market, sector, country, attention_level, attention_score, opportunity_score,
             data_label, paper_execution_supported, qualified_for_capital, payload, updated_at)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s)
            ON CONFLICT (symbol) DO UPDATE SET asset_class=EXCLUDED.asset_class, market=EXCLUDED.market,
            sector=EXCLUDED.sector, country=EXCLUDED.country, attention_level=EXCLUDED.attention_level,
            attention_score=EXCLUDED.attention_score, opportunity_score=EXCLUDED.opportunity_score,
            data_label=EXCLUDED.data_label, paper_execution_supported=EXCLUDED.paper_execution_supported,
            qualified_for_capital=EXCLUDED.qualified_for_capital, payload=EXCLUDED.payload, updated_at=EXCLUDED.updated_at""",
            (symbol, _asset_class(item.get("asset_class")), item.get("market") or item.get("asset_class"), item.get("sector"),
             item.get("country") or item.get("region"), item.get("attention_level"), _finite(item.get("attention_score")),
             _finite(item.get("opportunity_score")), item.get("data_label"), bool(item.get("paper_execution_supported")),
             bool(item.get("qualified_for_capital")), json.dumps(item), timestamp),
        )
    conn.execute(
        """INSERT INTO global_pit_activity
        (id, scans_completed_today, qualified_allocations, last_scan_at, updated_at)
        VALUES (1, %s, %s, %s, %s)
        ON CONFLICT (id) DO UPDATE SET scans_completed_today=global_pit_activity.scans_completed_today + EXCLUDED.scans_completed_today,
        qualified_allocations=EXCLUDED.qualified_allocations, last_scan_at=EXCLUDED.last_scan_at, updated_at=EXCLUDED.updated_at""",
        (1, sum(1 for item in queue if item.get("qualified_for_capital")), timestamp, timestamp),
    )
