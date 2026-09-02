from __future__ import annotations

import os
import threading
import time
from typing import Any

import oracle_bot
from crypto_execution_guard import _coinbase_reference_validation, _paper_yahoo_reference, _symbol
from database import row
from paper_execution_reality import simulate_fill
from robinhood_crypto_api import RobinhoodCryptoClient, best_bid_ask
from shadow_broker import record_shadow_order
from shadow_broker_runtime import evaluate_due_shadow_orders


_LOCK = threading.Lock()
_SEEN: set[tuple[str, str, str]] = set()
_LAST_EVALUATION = 0.0


def _enabled() -> bool:
    return (
        os.getenv("EXECUTION_MODE", "paper").strip().lower() == "paper"
        and os.getenv("ROBINHOOD_CRYPTO_ENABLED", "false").strip().lower() == "true"
    )


def _sample_limit() -> int:
    try:
        return max(1, min(12, int(os.getenv("SHADOW_PASSIVE_SAMPLE_SIZE", "12"))))
    except ValueError:
        return 12


def _sample_notional() -> float:
    try:
        return max(1.0, min(1000.0, float(os.getenv("SHADOW_PASSIVE_SAMPLE_NOTIONAL", "25"))))
    except ValueError:
        return 25.0


def _already_recorded(symbol: str, side: str, quote_timestamp: str) -> bool:
    try:
        found = row(
            """
            SELECT shadow_order_id
            FROM shadow_broker_orders
            WHERE symbol=%s AND side=%s
              AND payload->>'evidence_kind'='passive_paper_execution_model'
              AND payload->>'oracle_quote_timestamp'=%s
            ORDER BY created_at DESC
            LIMIT 1
            """,
            (symbol, side, quote_timestamp),
        )
        return bool(found)
    except Exception:
        # Persistence/query uncertainty must never create broker activity. The
        # in-memory key still limits duplicates inside the current process.
        return False


def _due_for_sample(key: tuple[str, str, str]) -> bool:
    with _LOCK:
        if key in _SEEN:
            return False
    if _already_recorded(*key):
        with _LOCK:
            _SEEN.add(key)
        return False
    return True


def _filter_robinhood_tradable_candidates(
    candidates: list[tuple[str, dict[str, Any], dict[str, Any], list[str]]],
    client: Any,
) -> tuple[list[tuple[str, dict[str, Any], dict[str, Any], list[str]]], int, str | None]:
    """Keep passive observations within Robinhood's advertised API pair universe.

    This is evidence collection only. It does not make a symbol strategy-tradable
    and does not alter the Oracle execution universe. The filter prevents one
    unsupported symbol from invalidating the entire read-only batch request.
    """
    trading_pairs = getattr(client, "trading_pairs", None)
    if not callable(trading_pairs):
        # Test/dummy clients used by local callers may expose only market data.
        return candidates, 0, None
    try:
        pairs = list(trading_pairs() or [])
    except Exception as exc:
        return [], sum(len(item[3]) for item in candidates), exc.__class__.__name__
    supported = {
        str(pair.get("symbol") or "").upper().strip()
        for pair in pairs
        if isinstance(pair, dict) and pair.get("tradable") is True and pair.get("symbol")
    }
    filtered = [item for item in candidates if item[0] in supported]
    skipped = sum(len(item[3]) for item in candidates if item[0] not in supported)
    return filtered, skipped, None


def capture_passive_shadow_samples(
    worker: Any,
    signals: Any,
    prices: dict[str, Any] | None,
    *,
    max_symbols: int | None = None,
    client: RobinhoodCryptoClient | None = None,
) -> dict[str, Any]:
    """Compare the real paper fill model with live Robinhood market truth.

    These are passive execution-model observations, not strategy trades. A sample
    is created only from a fresh execution-eligible Yahoo paper reference that
    independently passes Coinbase consensus. The exact paper fill simulator is
    then run without mutating the portfolio and compared with Robinhood bid/ask.
    No preview/place/cancel broker endpoint is called.
    """
    if not _enabled():
        return {"status": "DISABLED", "captured": 0}

    quote_map = prices or {}
    limit = _sample_limit() if max_symbols is None else max(1, min(12, int(max_symbols)))
    candidates: list[tuple[str, dict[str, Any], dict[str, Any], list[str]]] = []
    seen_symbols: set[str] = set()

    for signal in list(signals or []):
        if len(candidates) >= limit:
            break
        symbol = _symbol(signal)
        if not symbol or symbol in seen_symbols:
            continue
        seen_symbols.add(symbol)
        quote = oracle_bot._verified_quote_for(symbol, quote_map, "crypto")
        if quote is None or str(quote.get("provider") or "").strip().lower() != "yahoo finance":
            continue
        if not _paper_yahoo_reference(quote):
            continue
        validation = _coinbase_reference_validation(symbol, quote.get("price"))
        if validation.get("ok") is not True:
            continue
        quote_timestamp = str(quote.get("quote_timestamp") or quote.get("timestamp") or "").strip()
        if not quote_timestamp:
            continue
        sides = [
            side
            for side in ("BUY", "SELL")
            if _due_for_sample((symbol, side, quote_timestamp))
        ]
        if not sides:
            continue
        candidates.append((symbol, quote, validation, sides))

    if not candidates:
        return {"status": "NO_NEW_REFERENCE_BARS", "captured": 0}

    client = client or RobinhoodCryptoClient()
    candidates, unsupported_skipped, discovery_error = _filter_robinhood_tradable_candidates(candidates, client)
    if discovery_error:
        return {
            "status": "BROKER_PAIR_DISCOVERY_UNAVAILABLE",
            "captured": 0,
            "skipped": unsupported_skipped,
            "reason": discovery_error,
        }
    if not candidates:
        return {
            "status": "NO_BROKER_TRADABLE_CANDIDATES",
            "captured": 0,
            "skipped": unsupported_skipped,
        }

    broker_quotes = client.best_bid_ask_quotes(*[item[0] for item in candidates])
    broker_map = {
        str(item.get("symbol") or "").upper().strip(): item
        for item in broker_quotes
        if isinstance(item, dict) and item.get("symbol")
    }
    captured = 0
    skipped = unsupported_skipped
    notional = _sample_notional()

    for symbol, oracle_quote, validation, sides in candidates:
        broker_quote = broker_map.get(symbol)
        if not broker_quote or best_bid_ask(broker_quote) is None:
            skipped += len(sides)
            continue
        try:
            reference = float(oracle_quote.get("price") or 0.0)
        except (TypeError, ValueError):
            reference = 0.0
        if reference <= 0:
            skipped += len(sides)
            continue
        quote_timestamp = str(oracle_quote.get("quote_timestamp") or oracle_quote.get("timestamp") or "")
        for side in sides:
            key = (symbol, side, quote_timestamp)
            try:
                simulated = simulate_fill(
                    side=side,
                    market="crypto",
                    reference_price=reference,
                    quote=oracle_quote,
                    order_value=notional,
                )
                quantity = notional / reference
                record_shadow_order(
                    symbol=symbol,
                    side=side,
                    quantity=quantity,
                    oracle_reference_price=reference,
                    broker_quote=broker_quote,
                    paper_fill_price=simulated.fill_price,
                    market="crypto",
                    payload={
                        "evidence_kind": "passive_paper_execution_model",
                        "oracle_quote_timestamp": quote_timestamp,
                        "oracle_provider": oracle_quote.get("provider"),
                        "coinbase_reference_provider": validation.get("reference_provider"),
                        "coinbase_reference_timestamp": validation.get("reference_timestamp"),
                        "coinbase_difference_pct": validation.get("difference_pct"),
                        "sample_notional": notional,
                        "paper_adverse_cost_pct": simulated.adverse_cost_pct,
                        "paper_spread_pct": simulated.spread_pct,
                        "paper_slippage_pct": simulated.slippage_pct,
                        "paper_fee_pct": simulated.fee_pct,
                    },
                )
                with _LOCK:
                    _SEEN.add(key)
                captured += 1
            except Exception:
                skipped += 1

    if captured or skipped:
        worker.log.info(
            "CRYPTO | PASSIVE SHADOW EXECUTION EVIDENCE | captured=%d | skipped=%d | symbols=%d | broker_submission=NONE",
            captured,
            skipped,
            len(candidates),
        )
    return {"status": "PASS", "captured": captured, "skipped": skipped}


def maintain_passive_shadow_evidence(
    worker: Any,
    signals: Any,
    prices: dict[str, Any] | None,
) -> dict[str, Any]:
    """Capture new unique reference bars and periodically evaluate due samples."""
    global _LAST_EVALUATION
    if not _enabled():
        return {"status": "DISABLED"}
    client = RobinhoodCryptoClient()
    capture = capture_passive_shadow_samples(worker, signals, prices, client=client)

    try:
        cadence = max(60, int(os.getenv("SHADOW_PASSIVE_EVALUATION_SECONDS", "300")))
    except ValueError:
        cadence = 300
    now = time.monotonic()
    evaluate: dict[str, Any] = {"status": "COOLDOWN", "evaluated": 0}
    with _LOCK:
        due = now - _LAST_EVALUATION >= cadence
        if due:
            _LAST_EVALUATION = now
    if due:
        try:
            evaluate = evaluate_due_shadow_orders(client)
            if int(evaluate.get("evaluated") or 0) > 0:
                worker.log.info(
                    "CRYPTO | PASSIVE SHADOW FORWARD EVALUATION | evaluated=%d | due=%d",
                    int(evaluate.get("evaluated") or 0),
                    int(evaluate.get("due") or 0),
                )
        except Exception as exc:
            evaluate = {"status": "UNAVAILABLE", "evaluated": 0, "reason": exc.__class__.__name__}
    return {"status": "PASS", "capture": capture, "evaluate": evaluate}
