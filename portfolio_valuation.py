from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import logging
import math
import os
import time
from typing import Any

from database import connect
from market_data import MarketSnapshot, _duplicate_price_quarantine, get_snapshot
from market_sessions import completed_daily_bar_is_fresh, exchange_from_symbol, market_session_state, parse_utc
from provider_router import normalize_symbol


log = logging.getLogger("portfolio-valuation")
VALUATION_REFRESH_SECONDS = max(60, int(os.getenv("PORTFOLIO_VALUATION_REFRESH_SECONDS", "300")))
REGULAR_SESSION_EXECUTION_GRACE_SECONDS = max(
    30,
    int(os.getenv("PORTFOLIO_VALUATION_EXECUTION_GRACE_SECONDS", "120")),
)
_LAST_REFRESH_MONOTONIC: dict[str, float] = {}


@dataclass(frozen=True)
class ValuationRefreshResult:
    updated: int
    providers: tuple[str, ...]
    session_state: str
    skipped: bool = False


def _finite_positive(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(number) or number <= 0:
        return None
    return number


def _daily_like(interval: Any) -> bool:
    text = str(interval or "").strip().lower()
    return text.endswith("d") or text in {"1wk", "1mo", "1w", "weekly", "monthly"}


def _exchange_for_symbol(symbol: str) -> str:
    return exchange_from_symbol(symbol) or "NYSE"


def valuation_snapshot_is_safe(
    snapshot: MarketSnapshot | None,
    symbol: str,
    *,
    now: datetime | None = None,
) -> bool:
    """Accept a completed market bar for valuation without upgrading it for execution.

    This deliberately does not inspect or mutate ``quote_verified``. Execution paths
    remain responsible for fresh provider verification. A valuation mark only needs
    exact symbol identity, a positive finite price, and the latest completed daily
    bar for the instrument's exchange.
    """
    if snapshot is None:
        return False
    requested = normalize_symbol(symbol)
    if not requested:
        return False
    if (
        normalize_symbol(snapshot.symbol) != requested
        or normalize_symbol(snapshot.requested_symbol) != requested
        or normalize_symbol(snapshot.provider_symbol) != requested
    ):
        return False
    if _finite_positive(snapshot.price) is None:
        return False
    if not _daily_like(snapshot.interval):
        return False
    provider = str(snapshot.provider or "").strip().lower()
    if not provider or provider == "unknown":
        return False
    if parse_utc(snapshot.timestamp) is None:
        return False
    return completed_daily_bar_is_fresh(
        snapshot.timestamp,
        now=now,
        exchange=_exchange_for_symbol(requested),
        symbol=requested,
    )


def _updated_recently(value: Any, *, now: datetime | None = None) -> bool:
    parsed = parse_utc(value)
    if parsed is None:
        return False
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    age = max(0.0, (current.astimezone(timezone.utc) - parsed).total_seconds())
    return age <= REGULAR_SESSION_EXECUTION_GRACE_SECONDS


def _positions_needing_valuation(
    market: str,
    *,
    now: datetime | None = None,
) -> tuple[list[dict[str, Any]], str]:
    if str(market or "").lower() != "cash":
        return [], "not-applicable"
    state = market_session_state(now, exchange="NYSE")
    with connect() as conn:
        positions = list(
            conn.execute(
                "SELECT symbol,current_price,updated_at FROM positions WHERE market=%s",
                ("cash",),
            ).fetchall()
        )
    if state != "regular":
        return positions, state
    pending = [
        position
        for position in positions
        if _finite_positive(position.get("current_price")) is None
        or not _updated_recently(position.get("updated_at"), now=now)
    ]
    return pending, state


def refresh_portfolio_valuation(
    market: str = "cash",
    *,
    now: datetime | None = None,
    force: bool = False,
) -> ValuationRefreshResult:
    """Mark open positions using the latest completed daily market bar.

    The mark is valuation-only: it updates ``positions.current_price`` but never
    ``highest_price`` or ``updated_at``. That keeps trailing-stop state and
    execution freshness tied exclusively to execution-grade quotes.
    """
    market = str(market or "").lower()
    if market != "cash":
        return ValuationRefreshResult(0, (), "not-applicable", skipped=True)

    monotonic_now = time.monotonic()
    if not force:
        last = _LAST_REFRESH_MONOTONIC.get(market, 0.0)
        if monotonic_now - last < VALUATION_REFRESH_SECONDS:
            return ValuationRefreshResult(0, (), market_session_state(now, exchange="NYSE"), skipped=True)
    _LAST_REFRESH_MONOTONIC[market] = monotonic_now

    positions, session_state = _positions_needing_valuation(market, now=now)
    symbols = [normalize_symbol(position.get("symbol")) for position in positions]
    symbols = [symbol for symbol in dict.fromkeys(symbols) if symbol]
    if not symbols:
        return ValuationRefreshResult(0, (), session_state)

    snapshots: dict[str, MarketSnapshot] = {}
    for symbol in symbols:
        try:
            candidate = get_snapshot(symbol)
        except Exception as exc:
            log.debug("Valuation history unavailable | symbol=%s | error=%s", symbol, exc)
            continue
        if valuation_snapshot_is_safe(candidate, symbol, now=now):
            snapshots[symbol] = candidate

    quarantined = _duplicate_price_quarantine(snapshots)
    for symbol in quarantined:
        snapshots.pop(symbol, None)

    if not snapshots:
        return ValuationRefreshResult(0, (), session_state)

    updated = 0
    providers: set[str] = set()
    with connect() as conn:
        for symbol, valuation in snapshots.items():
            price = _finite_positive(valuation.price)
            if price is None:
                continue
            result = conn.execute(
                "UPDATE positions SET current_price=%s WHERE market=%s AND symbol=%s",
                (price, market, symbol),
            )
            if getattr(result, "rowcount", 0):
                updated += int(result.rowcount)
                providers.add(str(valuation.provider or "unknown"))
                log.info(
                    "PORTFOLIO_VALUATION_MARK | market=%s | symbol=%s | price=%.8f | as_of=%s | provider=%s | execution_eligible=false",
                    market,
                    symbol,
                    price,
                    valuation.timestamp,
                    valuation.provider,
                )

    return ValuationRefreshResult(updated, tuple(sorted(providers)), session_state)


def install_closed_market_valuation_pulse(market_worker_module: Any) -> None:
    """Add valuation marking around the existing execution-grade position pulse."""
    original = market_worker_module.live_position_pulse
    if getattr(original, "_portfolio_valuation_wrapped", False):
        return

    def valuation_aware_pulse(market: str):
        actions, refreshed, provider_text = original(market)
        if str(market or "").lower() != "cash":
            return actions, refreshed, provider_text
        try:
            result = refresh_portfolio_valuation("cash")
            if result.updated:
                from oracle_bot import snapshot as persist_portfolio_snapshot

                persist_portfolio_snapshot("cash")
                valuation_provider = ", ".join(result.providers) if result.providers else "market history"
                if provider_text in {"", "none", "unavailable"}:
                    provider_text = f"valuation-only {valuation_provider}"
                else:
                    provider_text = f"{provider_text}; valuation {valuation_provider}"
                refreshed = max(refreshed, result.updated)
        except Exception as exc:
            log.warning("Portfolio valuation refresh failed: %s", exc)
        return actions, refreshed, provider_text

    valuation_aware_pulse._portfolio_valuation_wrapped = True
    market_worker_module.live_position_pulse = valuation_aware_pulse
