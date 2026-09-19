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



_GRAPH_PREFERRED_FEATURES = (
    "rsi", "rsi_14", "momentum", "momentum_pct", "volume_ratio",
    "volatility", "volatility_20d", "spread_pct", "distance_from_vwap_pct",
    "breakout_score", "regime", "market_regime", "confidence",
    "expected_edge_pct", "expected_move_pct", "data_quality_score",
    "buying_power", "cash", "gross_exposure", "margin_utilization_pct",
)
_GRAPH_SKIP_FEATURES = {
    "id", "signal_id", "forecast_id", "decision_id", "quote_id",
    "correlation_id", "symbol", "market", "created_at", "timestamp",
    "quote_timestamp", "decision_timestamp", "provider_symbol",
}


def _graph_scalar(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        number = _number(value)
        if number is None:
            return None
        if abs(number) >= 1000:
            return f"{number:,.0f}"
        if abs(number) >= 10:
            return f"{number:.2f}"
        return f"{number:.4f}".rstrip("0").rstrip(".")
    if isinstance(value, str):
        text = value.strip()
        return text[:48] if text else None
    return None


def _flatten_graph_features(value: Any, prefix: str = "") -> dict[str, str]:
    source = _payload(value) if not isinstance(value, dict) else value
    result: dict[str, str] = {}
    for raw_key, raw_value in source.items():
        key = str(raw_key or "").strip()
        if not key:
            continue
        path = f"{prefix}.{key}" if prefix else key
        scalar = _graph_scalar(raw_value)
        if scalar is not None:
            result[path] = scalar
            continue
        if isinstance(raw_value, dict) and path.count(".") < 2:
            result.update(_flatten_graph_features(raw_value, path))
    return result


def _decision_feature_items(record: dict[str, Any], limit: int = 6) -> list[tuple[str, str]]:
    flattened = _flatten_graph_features(record.get("features"))
    portfolio = _flatten_graph_features(record.get("portfolio_context"), "portfolio")
    flattened.update(portfolio)

    usable = {
        key: value
        for key, value in flattened.items()
        if key.split(".")[-1].lower() not in _GRAPH_SKIP_FEATURES
    }
    ordered: list[tuple[str, str]] = []
    seen: set[str] = set()

    for preferred in _GRAPH_PREFERRED_FEATURES:
        for key, value in usable.items():
            if key in seen:
                continue
            if key.split(".")[-1].lower() == preferred:
                ordered.append((key, value))
                seen.add(key)
                break
        if len(ordered) >= limit:
            return ordered

    for key in sorted(usable):
        if key in seen:
            continue
        ordered.append((key, usable[key]))
        if len(ordered) >= limit:
            break
    return ordered


def _brain_node(
    node_id: str,
    title: str,
    kind: str,
    state: str,
    metric: str,
    detail: str,
    x: float,
    y: float,
    z: float,
    *,
    size: float = 0.28,
    label: bool = False,
) -> dict[str, Any]:
    return {
        "id": node_id,
        "title": title,
        "kind": kind,
        "state": state if state in {"online", "waiting", "offline"} else "waiting",
        "metric": metric,
        "detail": detail,
        "x": round(x, 3),
        "y": round(y, 3),
        "z": round(z, 3),
        "size": size,
        "label": label,
    }


def _build_decision_graph(
    decisions: list[dict[str, Any]],
    events: list[dict[str, Any]],
    ledger: list[dict[str, Any]],
    trades: list[dict[str, Any]],
) -> dict[str, Any]:
    recent = decisions[:18]
    event_map: dict[str, list[dict[str, Any]]] = {}
    for event in events:
        decision_id = str(event.get("decision_id") or "").strip()
        if decision_id:
            event_map.setdefault(decision_id, []).append(event)

    ledger_map: dict[str, dict[str, Any]] = {}
    for item in ledger:
        for raw_key in (item.get("entry_decision_id"), item.get("decision_id")):
            key = str(raw_key or "").strip()
            if key and key not in ledger_map:
                ledger_map[key] = item

    trade_by_id = {
        str(item.get("id")): item
        for item in trades
        if item.get("id") is not None
    }

    nodes: list[dict[str, Any]] = []
    edges: list[dict[str, Any]] = []
    downstream_block_ids: set[str] = set()
    linked_outcomes = 0

    for row_index, decision in enumerate(recent):
        decision_id = str(decision.get("decision_id") or "").strip()
        if not decision_id:
            continue
        symbol = str(decision.get("symbol") or "").upper() or "UNKNOWN"
        market = str(decision.get("market") or "").lower() or "unknown"
        action = str(decision.get("decision") or "OBSERVED").upper()
        row_z = (row_index - (len(recent) - 1) / 2.0) * 1.65
        decision_state = "online" if action in {
            "BUY", "STRONG_BUY", "STRONG BUY", "SELL", "REDUCE", "HOLD"
        } else "waiting"
        decision_node_id = f"decision:{decision_id}"
        nodes.append(_brain_node(
            decision_node_id,
            f"{symbol} · {action}",
            "decision",
            decision_state,
            market.upper(),
            f"Decision {decision_id} · {decision.get('created_at') or 'time unavailable'}",
            -1.8, 2.0, row_z, size=0.42, label=True,
        ))

        feature_items = _decision_feature_items(decision)
        for feature_index, (name, value) in enumerate(feature_items):
            short_name = name.replace("portfolio.", "portfolio · ").replace("_", " ")
            feature_node_id = f"feature:{decision_id}:{feature_index}"
            nodes.append(_brain_node(
                feature_node_id,
                short_name.title(),
                "feature",
                "online",
                value,
                f"Persisted entry-time feature for {symbol}.",
                -7.4 - (feature_index % 2) * 0.75,
                0.65 + (feature_index % 3) * 0.72,
                row_z + (feature_index - 2.5) * 0.12,
                size=0.18,
                label=False,
            ))
            edges.append({
                "source": feature_node_id,
                "target": decision_node_id,
                "kind": "evidence",
                "weight": 1.0,
            })

        decision_events = sorted(
            event_map.get(decision_id, []),
            key=lambda item: _parse_time(item.get("created_at"))
            or datetime.min.replace(tzinfo=timezone.utc),
        )
        seen_stages: set[str] = set()
        previous_node = decision_node_id
        stage_index = 0
        for event in decision_events:
            stage = str(event.get("stage") or "stage").strip().lower()
            rejection = str(event.get("rejection_reason") or "").strip()
            stage_key = f"{stage}:{rejection}" if rejection else stage
            if stage_key in seen_stages:
                continue
            seen_stages.add(stage_key)
            if rejection:
                downstream_block_ids.add(decision_id)
            stage_state = "offline" if rejection else "online"
            stage_node_id = f"stage:{decision_id}:{stage_index}"
            nodes.append(_brain_node(
                stage_node_id,
                stage.replace("_", " ").title(),
                "gate",
                stage_state,
                "BLOCKED" if rejection else "PASSED / OBSERVED",
                rejection or f"Persisted decision event for {symbol}.",
                1.25 + min(stage_index, 3) * 1.45,
                2.0,
                row_z,
                size=0.26,
                label=stage_index < 2,
            ))
            edges.append({
                "source": previous_node,
                "target": stage_node_id,
                "kind": "blocked" if rejection else "gate",
                "weight": 1.2 if rejection else 1.0,
            })
            previous_node = stage_node_id
            stage_index += 1
            if stage_index >= 4:
                break

        outcome = ledger_map.get(decision_id)
        exact_provenance = outcome is not None
        if outcome is None and decision.get("trade_id") is not None:
            outcome = trade_by_id.get(str(decision.get("trade_id")))

        if outcome is not None:
            linked_outcomes += 1
            status = str(outcome.get("status") or "recorded").upper()
            net_pnl = _number(outcome.get("net_pnl"))
            if net_pnl is None:
                net_pnl = _number(outcome.get("realized_pnl"))
            side = str(outcome.get("side") or "TRADE").upper()
            outcome_state = "waiting"
            if status in {"CLOSED", "FILLED", "COMPLETE", "COMPLETED", "RECORDED"}:
                if net_pnl is not None and net_pnl < 0:
                    outcome_state = "offline"
                else:
                    outcome_state = "online"
            metric = status
            if net_pnl is not None:
                metric += " · P/L $" + f"{net_pnl:+,.2f}"
            outcome_id = f"outcome:{decision_id}"
            nodes.append(_brain_node(
                outcome_id,
                f"{side} {symbol}",
                "outcome",
                outcome_state,
                metric,
                (
                    "Exact immutable decision provenance link."
                    if exact_provenance
                    else "Linked through global decision trade reference."
                ),
                8.4, 2.0, row_z, size=0.38, label=True,
            ))
            edges.append({
                "source": previous_node,
                "target": outcome_id,
                "kind": "execution",
                "weight": 1.35,
            })

    recent_closed = [
        item for item in ledger[:80]
        if str(item.get("status") or "").upper() in {"CLOSED", "COMPLETE", "COMPLETED"}
    ]
    provenance_gaps = sum(
        1 for item in recent_closed
        if not str(item.get("entry_decision_id") or item.get("decision_id") or "").strip()
    )

    return {
        "nodes": nodes,
        "edges": edges,
        "summary": {
            "traced_decisions": len({
                str(item.get("decision_id") or "").strip()
                for item in recent
                if str(item.get("decision_id") or "").strip()
            }),
            "linked_outcomes": linked_outcomes,
            "downstream_blocks": len(downstream_block_ids),
            "recent_closed_provenance_gaps": provenance_gaps,
        },
        "read_only": True,
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
    decision_ledger = _safe_select(
        fetch_rows, "SELECT * FROM global_decision_ledger ORDER BY created_at DESC LIMIT 40", (),
        warnings, "decision ledger unavailable",
    )
    decision_events = _safe_select(
        fetch_rows, "SELECT * FROM global_decision_events ORDER BY id DESC LIMIT 180", (),
        warnings, "decision event trace unavailable",
    )
    canonical_trade_ledger = _safe_select(
        fetch_rows, "SELECT * FROM trade_ledger ORDER BY id DESC LIMIT 120", (),
        warnings, "trade provenance unavailable",
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

    decision_graph = _build_decision_graph(
        decision_ledger,
        decision_events,
        canonical_trade_ledger,
        trades,
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
        "decision_graph": decision_graph,
    }


__all__ = ["build_oracle_city_snapshot", "_read_only"]
