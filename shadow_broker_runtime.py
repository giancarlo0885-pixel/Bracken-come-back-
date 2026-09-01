from __future__ import annotations

from datetime import datetime, timezone, timedelta
import logging
import os
import threading
import time
from typing import Any

from database import row, rows
from robinhood_crypto_api import RobinhoodCryptoClient, best_bid_ask
from shadow_broker import evaluate_shadow_order, record_shadow_order


log = logging.getLogger("shadow-broker-runtime")
_LOCK = threading.Lock()
_LAST_MAINTENANCE = 0.0


def _enabled() -> bool:
    return (
        os.getenv("EXECUTION_MODE", "paper").strip().lower() == "paper"
        and os.getenv("ROBINHOOD_CRYPTO_ENABLED", "false").strip().lower() == "true"
    )


def _signal_value(signal: Any, *names: str) -> Any:
    for name in names:
        if isinstance(signal, dict) and name in signal:
            return signal.get(name)
        if hasattr(signal, name):
            return getattr(signal, name)
    return None


def _proposal_context(signals: Any) -> dict[str, dict[str, Any]]:
    """Collect only evidence already produced by the Oracle signal pipeline."""
    contexts: dict[str, dict[str, Any]] = {}
    for signal in list(signals or []):
        symbol = str(_signal_value(signal, "symbol") or "").upper().strip()
        if not symbol:
            continue
        values = {
            "strategy": _signal_value(signal, "strategy", "primary_strategy"),
            "reason": _signal_value(signal, "reason", "rationale", "explanation"),
            "score": _signal_value(signal, "score", "opportunity_score"),
            "confidence": _signal_value(signal, "confidence"),
            "risk_reward_ratio": _signal_value(signal, "risk_reward_ratio", "reward_risk_ratio"),
            "target_price": _signal_value(signal, "target_price", "take_profit", "take_profit_price"),
            "stop_loss": _signal_value(signal, "stop_loss", "stop", "stop_loss_price"),
            "mean_reversion_zscore": _signal_value(signal, "mean_reversion_zscore"),
            "short_horizon_return": _signal_value(signal, "short_horizon_return"),
            "regime": _signal_value(signal, "crypto_regime", "regime", "market_regime"),
            "signal_id": _signal_value(signal, "signal_id", "id"),
            "forecast_id": _signal_value(signal, "forecast_id"),
            "decision_id": _signal_value(signal, "decision_id"),
        }
        contexts[symbol] = {key: value for key, value in values.items() if value not in (None, "")}
    return contexts


def _recent_untracked_fills(limit: int = 25) -> list[dict[str, Any]]:
    return rows(
        """
        SELECT f.*, o.reason AS order_reason, o.requested_notional,
               o.liquidity_value, o.participation_rate
        FROM paper_fills f
        JOIN paper_orders o ON o.order_id=f.order_id
        LEFT JOIN shadow_broker_orders s ON s.paper_fill_id=f.fill_id
        WHERE f.market='crypto' AND s.paper_fill_id IS NULL
        ORDER BY f.id DESC
        LIMIT %s
        """,
        (max(1, int(limit)),),
    )


def capture_recent_paper_fills(
    client: RobinhoodCryptoClient | None = None,
    *,
    limit: int = 25,
    proposal_context: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Turn new paper fills into durable, broker-verified trade proposals.

    Robinhood access remains read-only. The resulting record is a proposal for
    review and never calls a broker order-placement endpoint.
    """
    if not _enabled():
        return {"status": "DISABLED", "captured": 0}
    client = client or RobinhoodCryptoClient()
    fills = _recent_untracked_fills(limit=limit)
    if not fills:
        return {"status": "NO_NEW_FILLS", "captured": 0}

    symbols = sorted({str(item.get("symbol") or "").upper().strip() for item in fills if item.get("symbol")})
    quotes = client.best_bid_ask_quotes(*symbols)
    quote_map = {
        str(item.get("symbol") or "").upper().strip(): item
        for item in quotes
        if isinstance(item, dict) and item.get("symbol")
    }
    captured = 0
    skipped = 0
    proposal_context = proposal_context or {}
    for fill in reversed(fills):
        symbol = str(fill.get("symbol") or "").upper().strip()
        quote = quote_map.get(symbol)
        if not quote or best_bid_ask(quote) is None:
            skipped += 1
            continue
        fill_id = str(fill.get("fill_id") or "").strip()
        proposal_id = f"proposal:{fill_id}" if fill_id else None
        context = dict(proposal_context.get(symbol) or {})
        broker_estimate: Any = None
        try:
            estimated = client.estimated_price(
                symbol,
                str(fill.get("side") or "").lower(),
                fill.get("quantity") or 0,
            )
            if estimated:
                broker_estimate = estimated
        except Exception:
            broker_estimate = None
        payload = {
            "proposal_status": "AWAITING_HUMAN_APPROVAL",
            "proposal_source": "paper_execution_shadow",
            "human_approval_required": True,
            "submission_allowed": False,
            "paper_order_id": fill.get("order_id"),
            "paper_order_reason": fill.get("order_reason"),
            "paper_quote_provider": fill.get("quote_provider"),
            "paper_quote_timestamp": fill.get("quote_timestamp"),
            "requested_notional": fill.get("requested_notional"),
            "paper_fee_pct": fill.get("fee_pct"),
            "paper_slippage_pct": fill.get("slippage_pct"),
            "paper_spread_pct": fill.get("spread_pct"),
            "paper_market_impact_pct": fill.get("market_impact_pct"),
            "liquidity_value": fill.get("liquidity_value"),
            "participation_rate": fill.get("participation_rate"),
            "broker_estimated_price": broker_estimate,
            **context,
        }
        try:
            record_shadow_order(
                paper_fill_id=fill_id or None,
                decision_id=str(context.get("decision_id") or "") or None,
                proposal_id=proposal_id,
                symbol=symbol,
                side=str(fill.get("side") or ""),
                quantity=float(fill.get("quantity") or 0.0),
                oracle_reference_price=float(fill.get("reference_price") or fill.get("fill_price") or 0.0),
                paper_fill_price=float(fill.get("fill_price") or 0.0),
                broker_quote=quote,
                market="crypto",
                payload=payload,
            )
            captured += 1
        except Exception as exc:
            if "duplicate" not in str(exc).lower() and "unique" not in str(exc).lower():
                log.warning("Shadow proposal capture failed for %s: %s", symbol, exc.__class__.__name__)
            skipped += 1
    return {"status": "PASS", "captured": captured, "skipped": skipped}


def _due_shadow_orders(horizon_minutes: int, limit: int = 50) -> list[dict[str, Any]]:
    cutoff = datetime.now(timezone.utc) - timedelta(minutes=max(1, int(horizon_minutes)))
    return rows(
        """
        SELECT * FROM shadow_broker_orders
        WHERE status='OPEN' AND created_at <= %s
        ORDER BY created_at ASC
        LIMIT %s
        """,
        (cutoff.isoformat(), max(1, int(limit))),
    )


def evaluate_due_shadow_orders(client: RobinhoodCryptoClient | None = None, *, horizon_minutes: int | None = None) -> dict[str, Any]:
    if not _enabled():
        return {"status": "DISABLED", "evaluated": 0}
    horizon = horizon_minutes or max(5, int(os.getenv("SHADOW_BROKER_OUTCOME_HORIZON_MINUTES", "60")))
    due = _due_shadow_orders(horizon)
    if not due:
        return {"status": "NO_DUE_ORDERS", "evaluated": 0}
    client = client or RobinhoodCryptoClient()
    symbols = sorted({str(item.get("symbol") or "").upper().strip() for item in due if item.get("symbol")})
    quote_map = {
        str(item.get("symbol") or "").upper().strip(): item
        for item in client.best_bid_ask_quotes(*symbols)
        if isinstance(item, dict) and item.get("symbol")
    }
    evaluated = 0
    for item in due:
        symbol = str(item.get("symbol") or "").upper().strip()
        book = best_bid_ask(quote_map.get(symbol) or {})
        if book is None:
            continue
        try:
            evaluate_shadow_order(
                str(item.get("shadow_order_id")),
                float(book["mid"]),
                payload={"followup_source": "Robinhood best bid/ask", "horizon_minutes": horizon},
            )
            evaluated += 1
        except Exception as exc:
            log.warning("Shadow evaluation failed for %s: %s", symbol, exc.__class__.__name__)
    return {"status": "PASS", "evaluated": evaluated, "due": len(due)}


def maintain_shadow_broker_evidence() -> dict[str, Any]:
    global _LAST_MAINTENANCE
    if not _enabled():
        return {"status": "DISABLED"}
    cadence = max(60, int(os.getenv("SHADOW_BROKER_MAINTENANCE_SECONDS", "300")))
    now = time.monotonic()
    with _LOCK:
        if now - _LAST_MAINTENANCE < cadence:
            return {"status": "COOLDOWN"}
        _LAST_MAINTENANCE = now
    client = RobinhoodCryptoClient()
    capture = capture_recent_paper_fills(client)
    evaluate = evaluate_due_shadow_orders(client)
    return {"status": "PASS", "capture": capture, "evaluate": evaluate}


def install_shadow_broker_tracking(worker: Any) -> None:
    """Generate proposals immediately after crypto paper executions, then maintain shadow evidence."""
    if getattr(worker, "_shadow_broker_tracking_installed", False):
        return
    original = worker.process_signals

    def wrapped_process_signals(market: str, signals: Any, prices: Any = None, *args: Any, **kwargs: Any):
        contexts = _proposal_context(signals) if str(market or "").lower() == "crypto" else {}
        result = original(market, signals, prices, *args, **kwargs)
        if str(market or "").lower() == "crypto":
            try:
                capture_recent_paper_fills(proposal_context=contexts)
            except Exception as exc:
                log.warning("Trade proposal capture unavailable: %s", exc.__class__.__name__)
            try:
                maintain_shadow_broker_evidence()
            except Exception as exc:
                log.warning("Shadow broker maintenance unavailable: %s", exc.__class__.__name__)
        return result

    worker.process_signals = wrapped_process_signals
    worker._shadow_broker_tracking_installed = True
