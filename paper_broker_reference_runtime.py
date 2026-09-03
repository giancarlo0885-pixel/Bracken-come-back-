from __future__ import annotations

import logging
import math
import os
from typing import Any


log = logging.getLogger("paper-broker-reference")
PAPER_EXECUTION_MODEL_VERSION = "v2-robinhood-book"


def _finite(value: Any, default: float = 0.0) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return number if math.isfinite(number) else default


def _paper_crypto_mode() -> bool:
    return (
        os.getenv("EXECUTION_MODE", "paper").strip().lower() == "paper"
        and os.getenv("ROBINHOOD_CRYPTO_ENABLED", "false").strip().lower() == "true"
    )


def _broker_limits() -> tuple[float, float]:
    try:
        price_tolerance = max(0.0, float(os.getenv("ROBINHOOD_BROKER_PRICE_TOLERANCE_PCT", "0.75")))
    except ValueError:
        price_tolerance = 0.75
    try:
        max_spread = max(0.0, float(os.getenv("ROBINHOOD_BROKER_MAX_SPREAD_PCT", "1.50")))
    except ValueError:
        max_spread = 1.50
    return price_tolerance, max_spread


def _verified_quote_as_broker_book(symbol: str, payload: dict[str, Any] | None) -> dict[str, Any] | None:
    """Convert an already-verified execution payload back into broker-book shape.

    The crypto worker has already authenticated and identity-checked the Robinhood
    book before a candidate reaches the final paper-fill boundary. Reusing that
    exact book prevents a second network read from turning a valid decision into
    a false BROKER_QUOTE_INVALID/BROKER_QUOTE_UNAVAILABLE rejection.
    """
    if not isinstance(payload, dict):
        return None
    requested = str(symbol or "").upper().strip()
    payload_symbol = str(
        payload.get("provider_symbol")
        or payload.get("requested_symbol")
        or payload.get("symbol")
        or requested
    ).upper().strip()
    provider = str(payload.get("current_data_provider") or payload.get("provider") or "").strip()
    verified = payload.get("provider_quote_verified") is True or payload.get("quote_verified") is True
    bid = _finite(payload.get("execution_bid") if payload.get("execution_bid") is not None else payload.get("bid"))
    ask = _finite(payload.get("execution_ask") if payload.get("execution_ask") is not None else payload.get("ask"))
    if (
        not requested
        or payload_symbol != requested
        or provider != "Robinhood Crypto"
        or not verified
        or bid <= 0
        or ask <= 0
        or ask < bid
    ):
        return None
    return {
        "symbol": requested,
        "bid": str(bid),
        "ask": str(ask),
        "bid_price": str(bid),
        "ask_price": str(ask),
    }


def _validated_broker_reference(
    symbol: str,
    oracle_price: float,
    *,
    client: Any | None = None,
    broker_quote: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Return validated read-only Robinhood executable-market truth.

    This helper never previews, places, replaces, or cancels an order. It only
    reads best bid/ask and applies the same price/spread divergence checks used
    by the live execution guard.
    """
    from robinhood_crypto_api import (
        RobinhoodCryptoClient,
        best_bid_ask,
        validate_broker_market_reference,
    )

    requested = str(symbol or "").upper().strip()
    reference = _finite(oracle_price)
    if not requested or reference <= 0:
        return {"ok": False, "reason": "PAPER_BROKER_REFERENCE_INVALID"}

    quote = broker_quote
    try:
        if quote is None:
            client = client or RobinhoodCryptoClient()
            records = client.best_bid_ask_quotes(requested)
            quote = next(
                (
                    item
                    for item in records or []
                    if isinstance(item, dict)
                    and str(item.get("symbol") or "").upper().strip() == requested
                ),
                None,
            )
    except Exception as exc:
        return {"ok": False, "reason": f"PAPER_BROKER_QUOTE_UNAVAILABLE:{exc.__class__.__name__}"}

    if not isinstance(quote, dict):
        return {"ok": False, "reason": "PAPER_BROKER_QUOTE_MISSING"}

    tolerance, max_spread = _broker_limits()
    validation = validate_broker_market_reference(
        requested,
        reference,
        quote,
        max_price_difference_pct=tolerance,
        max_spread_pct=max_spread,
    )
    if validation.get("ok") is not True:
        return dict(validation)

    book = best_bid_ask(quote)
    if book is None:
        return {"ok": False, "reason": "PAPER_BROKER_QUOTE_INVALID"}

    return {
        "ok": True,
        "reason": "PAPER_BROKER_REFERENCE_CONFIRMED",
        "symbol": requested,
        "bid": float(book["bid"]),
        "ask": float(book["ask"]),
        "mid": float(book["mid"]),
        "spread_pct_points": float(book["spread_pct"]),
        "spread_fraction": float(book["spread_pct"]) / 100.0,
        "difference_pct": _finite(validation.get("difference_pct")),
        "raw_quote": quote,
    }


def _broker_anchored_quote(
    quote: dict[str, Any] | None,
    *,
    oracle_price: float,
    reference: dict[str, Any],
) -> dict[str, Any]:
    data = dict(quote or {})
    data["paper_oracle_reference_price"] = float(oracle_price)
    data["paper_broker_reference_verified"] = True
    data["paper_broker_reference_provider"] = "Robinhood Crypto"
    data["paper_broker_bid"] = float(reference["bid"])
    data["paper_broker_ask"] = float(reference["ask"])
    data["paper_broker_mid"] = float(reference["mid"])
    data["paper_broker_spread_pct"] = float(reference["spread_pct_points"])
    data["paper_broker_difference_pct"] = float(reference.get("difference_pct") or 0.0)
    data["paper_execution_model_version"] = PAPER_EXECUTION_MODEL_VERSION
    # paper_execution_reality consumes bid/ask and spread as a fraction.
    data["bid"] = float(reference["bid"])
    data["ask"] = float(reference["ask"])
    data["spread_pct"] = float(reference["spread_fraction"])
    return data


def _versioned_shadow_summary(*, minimum_samples: int = 100, maximum_paper_error_pct: float = 1.0) -> dict[str, Any]:
    from database import row

    try:
        stats = row(
            """
            SELECT
                COUNT(*) FILTER (WHERE status='EVALUATED')::int AS evaluated,
                COUNT(*) FILTER (WHERE status='OPEN')::int AS open_count,
                AVG(outcome_return_pct) FILTER (WHERE status='EVALUATED') AS avg_outcome,
                AVG(paper_vs_broker_error_pct) FILTER (WHERE paper_vs_broker_error_pct IS NOT NULL) AS avg_paper_error,
                PERCENTILE_CONT(0.95) WITHIN GROUP (ORDER BY paper_vs_broker_error_pct)
                    FILTER (WHERE paper_vs_broker_error_pct IS NOT NULL) AS p95_paper_error
            FROM shadow_broker_orders
            WHERE payload->>'evidence_kind'='passive_paper_execution_model'
              AND payload->>'paper_execution_model_version'=%s
            """,
            (PAPER_EXECUTION_MODEL_VERSION,),
        ) or {}
        evaluated = int(stats.get("evaluated") or 0)
        p95_error = stats.get("p95_paper_error")
        p95_ok = p95_error is not None and float(p95_error) <= float(maximum_paper_error_pct)
        ok = evaluated >= int(minimum_samples) and p95_ok
        return {
            "ok": ok,
            "evaluated_samples": evaluated,
            "open_samples": int(stats.get("open_count") or 0),
            "minimum_samples": int(minimum_samples),
            "average_outcome_return_pct": _finite(stats.get("avg_outcome")),
            "average_paper_vs_broker_error_pct": None if stats.get("avg_paper_error") is None else _finite(stats.get("avg_paper_error")),
            "p95_paper_vs_broker_error_pct": None if p95_error is None else _finite(p95_error),
            "maximum_paper_error_pct": float(maximum_paper_error_pct),
            "paper_execution_model_version": PAPER_EXECUTION_MODEL_VERSION,
            "status": "PASS" if ok else "INSUFFICIENT_FORWARD_EVIDENCE",
        }
    except Exception as exc:
        return {
            "ok": False,
            "evaluated_samples": 0,
            "minimum_samples": int(minimum_samples),
            "paper_execution_model_version": PAPER_EXECUTION_MODEL_VERSION,
            "status": "UNAVAILABLE",
            "reason": exc.__class__.__name__,
        }


def install_paper_broker_reference(worker: Any) -> bool:
    """Anchor paper fills and forward evidence to Robinhood's read-only book.

    Upstream Yahoo/Coinbase quote verification, forecast approval, V39 capital
    approval, risk checks, liquidity, concentration, reserve, drawdown, and
    execution-claim controls remain untouched. This layer runs only at the final
    paper-fill simulation boundary and fails closed if broker market truth cannot
    be validated. Broker submission remains completely disarmed.
    """
    if not _paper_crypto_mode():
        return False
    if getattr(worker, "_paper_broker_reference_installed", False):
        return False

    import oracle_bot
    import oracle_readiness
    import shadow_broker
    import shadow_forward_sampler
    from paper_execution_reality import simulate_fill

    current_buy = oracle_bot._buy
    current_close = oracle_bot._execute_close_position
    current_shadow_record = shadow_forward_sampler.record_shadow_order

    def broker_anchored_buy(
        market: str,
        symbol: str,
        price: float,
        signal: Any,
        *args: Any,
        **kwargs: Any,
    ):
        if str(market or "").lower() != "crypto":
            return current_buy(market, symbol, price, signal, *args, **kwargs)
        verified_quote = kwargs.get("verified_quote")
        broker_quote = _verified_quote_as_broker_book(symbol, verified_quote)
        reference = _validated_broker_reference(symbol, price, broker_quote=broker_quote)
        if reference.get("ok") is not True:
            reason = str(reference.get("reason") or "PAPER_BROKER_REFERENCE_REJECTED")
            log.warning("PAPER BROKER REFERENCE BLOCK | side=BUY | symbol=%s | reason=%s", symbol, reason)
            return False, f"paper broker reference blocked: {reason}", None
        quote = _broker_anchored_quote(
            verified_quote,
            oracle_price=float(price),
            reference=reference,
        )
        kwargs["verified_quote"] = quote
        return current_buy(market, symbol, float(reference["mid"]), signal, *args, **kwargs)

    def broker_anchored_close(
        market: str,
        position: dict[str, Any],
        price: float,
        reason: str,
        quote_metadata: dict[str, Any] | None = None,
    ) -> bool:
        if str(market or "").lower() != "crypto":
            return current_close(market, position, price, reason, quote_metadata=quote_metadata)
        symbol = str(position.get("symbol") or "").upper().strip()
        broker_quote = _verified_quote_as_broker_book(symbol, quote_metadata)
        reference = _validated_broker_reference(symbol, price, broker_quote=broker_quote)
        if reference.get("ok") is not True:
            block_reason = str(reference.get("reason") or "PAPER_BROKER_REFERENCE_REJECTED")
            log.warning("PAPER BROKER REFERENCE BLOCK | side=SELL | symbol=%s | reason=%s", symbol, block_reason)
            return False
        quote = _broker_anchored_quote(
            quote_metadata,
            oracle_price=float(price),
            reference=reference,
        )
        return current_close(
            market,
            position,
            float(reference["mid"]),
            reason,
            quote_metadata=quote,
        )

    def versioned_shadow_record(*args: Any, **kwargs: Any):
        payload = dict(kwargs.get("payload") or {})
        market = str(kwargs.get("market") or "crypto").lower()
        if market == "crypto" and payload.get("evidence_kind") == "passive_paper_execution_model":
            oracle_price = _finite(kwargs.get("oracle_reference_price"))
            broker_quote = kwargs.get("broker_quote")
            symbol = str(kwargs.get("symbol") or "").upper().strip()
            side = str(kwargs.get("side") or "").upper().strip()
            reference = _validated_broker_reference(
                symbol,
                oracle_price,
                broker_quote=broker_quote if isinstance(broker_quote, dict) else None,
            )
            if reference.get("ok") is not True:
                raise ValueError(str(reference.get("reason") or "passive broker reference rejected"))
            notional = _finite(payload.get("sample_notional"), 25.0)
            simulated = simulate_fill(
                side=side,
                market="crypto",
                reference_price=float(reference["mid"]),
                quote={
                    "bid": float(reference["bid"]),
                    "ask": float(reference["ask"]),
                    "spread_pct": float(reference["spread_fraction"]),
                },
                order_value=notional,
            )
            kwargs["paper_fill_price"] = simulated.fill_price
            payload.update(
                {
                    "paper_execution_model_version": PAPER_EXECUTION_MODEL_VERSION,
                    "paper_oracle_reference_price": oracle_price,
                    "paper_broker_mid": float(reference["mid"]),
                    "paper_broker_bid": float(reference["bid"]),
                    "paper_broker_ask": float(reference["ask"]),
                    "paper_broker_difference_pct": float(reference.get("difference_pct") or 0.0),
                    "paper_adverse_cost_pct": simulated.adverse_cost_pct,
                    "paper_spread_pct": simulated.spread_pct,
                    "paper_slippage_pct": simulated.slippage_pct,
                    "paper_fee_pct": simulated.fee_pct,
                }
            )
            kwargs["payload"] = payload
        return current_shadow_record(*args, **kwargs)

    oracle_bot._buy = broker_anchored_buy
    oracle_bot._execute_close_position = broker_anchored_close
    shadow_forward_sampler.record_shadow_order = versioned_shadow_record
    shadow_broker.shadow_readiness_summary = _versioned_shadow_summary
    oracle_readiness.shadow_readiness_summary = _versioned_shadow_summary
    worker._paper_broker_reference_installed = True
    log.info(
        "Installed broker-anchored paper execution reference | version=%s | broker_submission=NONE",
        PAPER_EXECUTION_MODEL_VERSION,
    )
    return True
