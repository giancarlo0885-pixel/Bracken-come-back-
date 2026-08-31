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


def _recent_untracked_fills(limit: int = 25) -> list[dict[str, Any]]:
    return rows(
        """
        SELECT f.*
        FROM paper_fills f
        LEFT JOIN shadow_broker_orders s ON s.paper_fill_id=f.fill_id
        WHERE f.market='crypto' AND s.paper_fill_id IS NULL
        ORDER BY f.id DESC
        LIMIT %s
        """,
        (max(1, int(limit)),),
    )


def capture_recent_paper_fills(client: RobinhoodCryptoClient | None = None, *, limit: int = 25) -> dict[str, Any]:
    """Attach actual Robinhood bid/ask truth to new crypto paper fills.

    This function is strictly read-only with respect to Robinhood. It never
    previews, places, cancels, or modifies a broker order.
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
    for fill in reversed(fills):
        symbol = str(fill.get("symbol") or "").upper().strip()
        quote = quote_map.get(symbol)
        if not quote or best_bid_ask(quote) is None:
            skipped += 1
            continue
        try:
            record_shadow_order(
                paper_fill_id=str(fill.get("fill_id") or "") or None,
                symbol=symbol,
                side=str(fill.get("side") or ""),
                quantity=float(fill.get("quantity") or 0.0),
                oracle_reference_price=float(fill.get("reference_price") or fill.get("fill_price") or 0.0),
                paper_fill_price=float(fill.get("fill_price") or 0.0),
                broker_quote=quote,
                market="crypto",
                payload={
                    "paper_order_id": fill.get("order_id"),
                    "paper_quote_provider": fill.get("quote_provider"),
                    "paper_quote_timestamp": fill.get("quote_timestamp"),
                },
            )
            captured += 1
        except Exception as exc:
            # Duplicate paper_fill_id is safe and should not create duplicate
            # shadow observations. Any other failure remains visible in logs.
            if "duplicate" not in str(exc).lower() and "unique" not in str(exc).lower():
                log.warning("Shadow capture failed for %s: %s", symbol, exc.__class__.__name__)
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
    """Run read-only broker-shadow maintenance after crypto paper executions."""
    if getattr(worker, "_shadow_broker_tracking_installed", False):
        return
    original = worker.process_signals

    def wrapped_process_signals(market: str, signals: Any, prices: Any = None, *args: Any, **kwargs: Any):
        result = original(market, signals, prices, *args, **kwargs)
        if str(market or "").lower() == "crypto":
            try:
                maintain_shadow_broker_evidence()
            except Exception as exc:
                log.warning("Shadow broker maintenance unavailable: %s", exc.__class__.__name__)
        return result

    worker.process_signals = wrapped_process_signals
    worker._shadow_broker_tracking_installed = True
