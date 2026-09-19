from __future__ import annotations

import json
import math
from datetime import datetime, timezone
from typing import Any, Callable

from config import EXECUTION_MODE, LIVE_STATUS_STALE_SECONDS
from database import rows

FetchRows = Callable[[str, tuple[Any, ...]], list[dict[str, Any]]]


def _number(value: Any) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def _payload(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if isinstance(value, str) and value.strip():
        try:
            parsed = json.loads(value)
        except (TypeError, ValueError, json.JSONDecodeError):
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return {}


def _parse_time(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        parsed = value
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


def _read_only(
    fetch_rows: FetchRows,
    query: str,
    params: tuple[Any, ...] = (),
) -> list[dict[str, Any]]:
    if not query.lstrip().upper().startswith("SELECT"):
        raise ValueError("Oracle City is read-only")
    return fetch_rows(query, params)


def _safe_select(
    fetch_rows: FetchRows,
    query: str,
    params: tuple[Any, ...],
    warnings: list[str],
    label: str,
) -> list[dict[str, Any]]:
    try:
        return _read_only(fetch_rows, query, params)
    except Exception as exc:
        warnings.append(f"{label}: {exc.__class__.__name__}")
        return []


def _worker_state(record: dict[str, Any], now: datetime) -> tuple[str, float | None]:
    status = str(record.get("status") or "").strip().lower()
    heartbeat = record.get("heartbeat") or record.get("last_pulse") or record.get("last_run")
    parsed = _parse_time(heartbeat)
    age = None if parsed is None else max(0.0, (now - parsed).total_seconds())
    if status in {"error", "failed", "offline", "halted", "stopped"}:
        return "offline", age
    if age is None:
        return "waiting", None
    if age > LIVE_STATUS_STALE_SECONDS:
        return "offline", age
    if status in {"running", "online", "ok", "healthy", "live", "scanning"}:
        return "online", age
    return "waiting", age


def _position_value(record: dict[str, Any]) -> float:
    direct = _number(record.get("market_value"))
    if direct is not None:
        return abs(direct)
    quantity = abs(_number(record.get("quantity")) or 0.0)
    price = (
        _number(record.get("current_price"))
        or _number(record.get("last_price"))
        or _number(record.get("price"))
        or _number(record.get("avg_price"))
        or _number(record.get("average_price"))
        or 0.0
    )
    return quantity * price


def _node(
    node_id: str,
    title: str,
    state: str,
    metric: str,
    detail: str,
    x: float,
    z: float,
    *,
    height: float = 4.0,
    scale: float = 1.0,
) -> dict[str, Any]:
    return {
        "id": node_id,
        "title": title,
        "state": state if state in {"online", "waiting", "offline"} else "waiting",
        "metric": metric,
        "detail": detail,
        "x": x,
        "z": z,
        "height": height,
        "scale": scale,
    }


def build_oracle_city_snapshot(
    fetch_rows: FetchRows = rows,
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    warnings: list[str] = []

    workers = _safe_select(
        fetch_rows, "SELECT * FROM market_worker_status ORDER BY market", (),
        warnings, "worker status unavailable",
    )
    portfolios = _safe_select(
        fetch_rows, "SELECT * FROM portfolios ORDER BY market", (),
        warnings, "portfolio summary unavailable",
    )
    positions = _safe_select(
        fetch_rows, "SELECT * FROM positions ORDER BY market,symbol", (),
        warnings, "positions unavailable",
    )
    opportunities = _safe_select(
        fetch_rows,
        """SELECT DISTINCT ON (market,symbol)
                  market,symbol,rank,opportunity_score,payload,created_at
           FROM opportunity_rankings
           ORDER BY market,symbol,created_at DESC""",
        (), warnings, "opportunity rankings unavailable",
    )
    trades = _safe_select(
        fetch_rows, "SELECT * FROM trades ORDER BY id DESC LIMIT 80", (),
        warnings, "trade history unavailable",
    )
    decisions = _safe_select(
        fetch_rows, "SELECT * FROM oracle_decision_audit ORDER BY id DESC LIMIT 80", (),
        warnings, "decision audit unavailable",
    )
    events = _safe_select(
        fetch_rows, "SELECT * FROM intelligence_events ORDER BY id DESC LIMIT 60", (),
        warnings, "intelligence unavailable",
    )

    worker_map = {str(item.get("market") or "").lower(): item for item in workers}
    worker_views: list[dict[str, Any]] = []
    for market in ("cash", "crypto"):
        record = worker_map.get(market, {})
        state, age = _worker_state(record, now)
        worker_views.append({
            "market": market,
            "state": state,
            "status": str(record.get("status") or "unknown"),
            "message": str(record.get("message") or ""),
            "age_seconds": None if age is None else round(age, 1),
            "actions_last_cycle": int(record.get("actions_last_cycle") or 0),
            "cycle_errors": int(record.get("cycle_errors") or 0),
        })
    worker_by_market = {item["market"]: item for item in worker_views}

    opportunity_views: list[dict[str, Any]] = []
    for item in opportunities:
        detail = _payload(item.get("payload"))
        confidence = _number(detail.get("confidence"))
        if confidence is not None and confidence <= 1:
            confidence *= 100
        opportunity_views.append({
            "market": str(item.get("market") or ""),
            "symbol": str(item.get("symbol") or "").upper(),
            "score": round(_number(item.get("opportunity_score")) or 0.0, 2),
            "action": str(detail.get("action") or detail.get("decision") or "WATCH").upper(),
            "confidence": None if confidence is None else round(confidence, 1),
            "strategy": str(detail.get("strategy") or detail.get("setup") or "unattributed"),
            "regime": str(detail.get("regime") or "unknown"),
            "created_at": str(item.get("created_at") or ""),
        })
    opportunity_views.sort(key=lambda item: item["score"], reverse=True)

    position_views: list[dict[str, Any]] = []
    for item in positions:
        quantity = _number(item.get("quantity")) or 0.0
        if abs(quantity) <= 0:
            continue
        position_views.append({
            "market": str(item.get("market") or ""),
            "symbol": str(item.get("symbol") or "").upper(),
            "quantity": quantity,
            "value": round(_position_value(item), 2),
        })

    max_position_value = max((item["value"] for item in position_views), default=0.0)
    portfolio_towers: list[dict[str, Any]] = []
    for index, item in enumerate(
        sorted(position_views, key=lambda row: row["value"], reverse=True)[:18]
    ):
        ring = 2.1 + (index // 8) * 1.4
        slot = index % 8
        angle = (slot / 8.0) * math.tau
        normalized = 0.0 if max_position_value <= 0 else item["value"] / max_position_value
        portfolio_towers.append({
            **item,
            "x": 12.0 + math.cos(angle) * ring,
            "z": math.sin(angle) * ring,
            "height": round(0.9 + normalized * 5.4, 2),
        })

    top = opportunity_views[0] if opportunity_views else None
    stock = worker_by_market["cash"]
    crypto = worker_by_market["crypto"]
    exposure = sum(item["value"] for item in position_views)

    nodes = [
        _node("data", "Data Center", "online" if not warnings else "waiting",
              "PostgreSQL linked" if not warnings else f"{len(warnings)} partial feeds",
              "Canonical read-only state source for Oracle City.", -10, 0, height=4.2),
        _node("intel", "Intelligence Tower", "online" if events else "waiting",
              f"{len(events)} events", "Macro, news, policy and external context.", -6, -5, height=5.1),
        _node("patterns", "Pattern Lab", "online" if opportunities else "waiting",
              f"{len({item['strategy'] for item in opportunity_views})} setups",
              "Strategy, regime and pattern evidence.", -2, -5, height=4.7),
        _node("council", "Council HQ", "online" if opportunity_views else "waiting",
              f"{len(opportunity_views)} ranked",
              (f"Top: {top['symbol']} {top['action']} · score {top['score']:.1f}"
               if top else "Waiting for ranked evidence."),
              0, 0, height=6.0, scale=1.18),
        _node("risk", "Risk Center", "online", str(EXECUTION_MODE).upper(),
              "Risk, sizing and eligibility gates remain authoritative.",
              4, 0, height=5.0),
        _node("execution", "Execution Center", "online" if trades else "waiting",
              f"{len(trades)} recent records", "Persisted paper execution history.",
              8, 0, height=5.4),
        _node("portfolio", "Portfolio Vault", "online" if portfolios else "waiting",
              f"{len(position_views)} open positions",
              "Known marked exposure $" + f"{exposure:,.2f}.",
              12, 0, height=5.8, scale=1.12),
        _node("stock", "Stock Exchange", stock["state"], stock["status"].upper(),
              ("Heartbeat age unknown." if stock["age_seconds"] is None
               else f"Heartbeat {stock['age_seconds']:.0f}s old. {stock['message']}"),
              4, 5, height=4.8),
        _node("crypto", "Crypto Exchange", crypto["state"], crypto["status"].upper(),
              ("Heartbeat age unknown." if crypto["age_seconds"] is None
               else f"Heartbeat {crypto['age_seconds']:.0f}s old. {crypto['message']}"),
              4, -5, height=4.8),
    ]

    flows = [
        {"source": source, "target": target, "label": label}
        for source, target, label in [
            ("data", "intel", "context"),
            ("data", "patterns", "market state"),
            ("intel", "council", "intelligence"),
            ("patterns", "council", "pattern evidence"),
            ("stock", "council", "stock signals"),
            ("crypto", "council", "crypto signals"),
            ("council", "risk", "decision"),
            ("risk", "execution", "approval"),
            ("execution", "portfolio", "fills/outcomes"),
            ("portfolio", "patterns", "learning feedback"),
        ]
    ]

    strategy_agents: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in opportunity_views:
        strategy = item["strategy"] or "unattributed"
        if strategy in seen:
            continue
        seen.add(strategy)
        index = len(strategy_agents)
        angle = (index / 8.0) * math.tau
        strategy_agents.append({
            "id": f"agent-{index}",
            "title": strategy.replace("_", " ").title(),
            "strategy": strategy,
            "symbol": item["symbol"],
            "action": item["action"],
            "score": item["score"],
            "x": -2.0 + math.cos(angle) * 2.2,
            "z": -5.0 + math.sin(angle) * 2.2,
        })
        if len(strategy_agents) >= 8:
            break

    replay: list[dict[str, Any]] = []
    for item in decisions[:30]:
        detail = _payload(item.get("payload"))
        symbol = str(item.get("symbol") or detail.get("symbol") or "").upper()
        action = str(
            item.get("action") or detail.get("action") or detail.get("decision") or "OBSERVED"
        ).upper()
        replay.append({
            "kind": "decision",
            "time": str(item.get("created_at") or item.get("decision_time") or ""),
            "title": f"{action} {symbol or 'candidate'}",
            "detail": str(
                item.get("reason") or detail.get("reason") or detail.get("explanation") or ""
            )[:260],
            "path": ["patterns", "council", "risk"],
        })

    for item in trades[:30]:
        symbol = str(item.get("symbol") or "").upper()
        side = str(item.get("side") or "").upper()
        notional = _number(item.get("value"))
        if notional is None:
            notional = _number(item.get("notional"))
        notional_text = "" if notional is None else " · $" + f"{notional:,.2f}"
        replay.append({
            "kind": "trade",
            "time": str(item.get("created_at") or ""),
            "title": f"{side} {symbol}",
            "detail": (str(item.get("reason") or "Paper trade") + notional_text)[:260],
            "path": ["council", "risk", "execution", "portfolio"],
        })

    for item in events[:20]:
        title = str(
            item.get("title") or item.get("event_type") or item.get("category") or "Intelligence"
        )
        replay.append({
            "kind": "intel",
            "time": str(item.get("event_time") or item.get("created_at") or ""),
            "title": title,
            "detail": str(item.get("details") or item.get("summary") or "")[:260],
            "path": ["data", "intel", "council"],
        })

    replay.sort(
        key=lambda item: _parse_time(item["time"])
        or datetime.min.replace(tzinfo=timezone.utc)
    )

    return {
        "generated_at": now.isoformat(),
        "read_only": True,
        "execution_mode": EXECUTION_MODE,
        "warnings": warnings,
        "summary": {
            "workers_online": sum(1 for item in worker_views if item["state"] == "online"),
            "workers_total": len(worker_views),
            "open_positions": len(position_views),
            "recent_trades": len(trades),
            "ranked_opportunities": len(opportunity_views),
            "known_exposure": round(exposure, 2),
        },
        "nodes": nodes,
        "flows": flows,
        "portfolio_towers": portfolio_towers,
        "strategy_agents": strategy_agents,
        "opportunities": opportunity_views[:24],
        "replay": replay[-80:],
    }


__all__ = ["build_oracle_city_snapshot", "_read_only"]
