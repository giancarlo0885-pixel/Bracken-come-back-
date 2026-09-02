from __future__ import annotations

import os
from typing import Any


def _live_crypto_mode() -> bool:
    return (
        os.getenv("EXECUTION_MODE", "paper").strip().lower() == "live"
        and os.getenv("ROBINHOOD_CRYPTO_ENABLED", "false").strip().lower() == "true"
    )


def _position_rows(snapshot: Any) -> list[dict[str, Any]]:
    values = dict(getattr(snapshot, "position_values", {}) or {})
    tradable = dict(getattr(snapshot, "tradable_quantities", {}) or {})
    rows: list[dict[str, Any]] = []
    for symbol, market_value in sorted(values.items()):
        rows.append(
            {
                "market": "crypto",
                "symbol": str(symbol or "").upper(),
                "market_value": max(0.0, float(market_value or 0.0)),
                "quantity_available_for_trading": max(0.0, float(tradable.get(symbol, 0.0) or 0.0)),
                "broker_position": True,
                "broker_capital_source": getattr(snapshot, "source", "robinhood_crypto_v2"),
            }
        )
    return rows


def install_live_v39_broker_capital_bridge(worker: Any, oracle_module: Any | None = None) -> None:
    """Make V39 use verified Robinhood capital/holdings during live crypto planning.

    Paper mode is intentionally untouched. In live mode, V39 must not plan against
    the simulated database portfolio. It consumes the same fail-closed broker
    snapshot used by final live sizing. If the broker snapshot is incomplete,
    buying power is forced to zero so the optimizer cannot authorize a new entry.
    All downstream quote, risk, liquidity, concentration, correlation, reserve,
    margin, drawdown, duplicate-execution and manual-approval gates remain intact.
    """
    if getattr(worker, "_live_v39_broker_capital_bridge_installed", False):
        return

    if oracle_module is None:
        import oracle_bot as oracle_module

    original = worker._v39_position_rows

    def broker_aware_position_rows(market: str) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        if str(market or "").strip().lower() != "crypto" or not _live_crypto_mode():
            return original(market)

        provider = getattr(oracle_module, "_live_broker_capital_provider", None)
        if provider is None:
            worker.log.warning("LIVE V39 CAPITAL | blocked | reason=BROKER_CAPITAL_PROVIDER_MISSING")
            return (
                {
                    "market": "crypto",
                    "cash": 0.0,
                    "buying_power": 0.0,
                    "equity": 0.01,
                    "broker_capital_validated": False,
                    "broker_capital_reason": "BROKER_CAPITAL_PROVIDER_MISSING",
                },
                [],
            )

        snapshot = provider.snapshot(fresh=True)
        metrics = dict(snapshot.portfolio_metrics())
        metrics["market"] = "crypto"
        metrics["broker_capital_validated"] = bool(snapshot.sizing_allowed)
        positions = _position_rows(snapshot)

        if not snapshot.sizing_allowed:
            metrics["cash"] = 0.0
            metrics["buying_power"] = 0.0
            worker.log.warning(
                "LIVE V39 CAPITAL | blocked | reason=%s | equity=%.2f | known_positions=%d | missing_quotes=%d",
                str(getattr(snapshot, "reason", "BROKER_CAPITAL_INCOMPLETE")),
                float(metrics.get("equity") or 0.0),
                len(positions),
                len(getattr(snapshot, "missing_quotes", ()) or ()),
            )
            return metrics, positions

        worker.log.info(
            "LIVE V39 CAPITAL | verified | equity=%.2f | buying_power=%.2f | gross_exposure=%.2f | positions=%d | source=%s",
            float(metrics.get("equity") or 0.0),
            float(metrics.get("buying_power") or 0.0),
            float(metrics.get("gross_exposure") or 0.0),
            len(positions),
            str(metrics.get("broker_capital_source") or "robinhood_crypto_v2"),
        )
        return metrics, positions

    broker_aware_position_rows._oracle_live_broker_capital = True
    worker._v39_position_rows = broker_aware_position_rows
    worker._live_v39_broker_capital_bridge_installed = True
