from __future__ import annotations

"""Research-only three-layer exit challenger and paired episode econometrics.

The challenger changes no execution path. It observes canonical paper positions,
uses only point-in-time persisted signal/forecast evidence, freezes the first
qualified forward-evidence exit, and writes one finalized comparison row per
exact-provenance closed lot.

Layer 1 (hard risk) and Layer 3 (existing profit protection) remain the champion
behavior. This challenger isolates only Layer 2 so any measured delta is
attributable to the forward-evidence exit rather than multiple simultaneous
policy changes.
"""

from dataclasses import dataclass
from datetime import datetime, timezone
import json
import logging
import math
import os
import threading
import uuid
from typing import Any

import numpy as np
import pandas as pd

log = logging.getLogger("paper-shadow-exit-challenger")

MODEL_VERSION = "shadow-exit-v6-decision-time-evidence"
COST_MODEL_VERSION = "explicit-paper-fill-fees-v2"
GENERATION = 3
_SCHEMA_LOCK = "garibaldi_shadow_exit_schema_v2"
_THREAD: threading.Thread | None = None
_STOP = threading.Event()
_STATE_LOCK = threading.Lock()
_POSITION_STATE: dict[str, dict[str, Any]] = {}


def _truthy(name: str, default: str = "false") -> bool:
    return str(os.getenv(name, default) or default).strip().lower() in {"1", "true", "yes", "on"}


def active() -> bool:
    return bool(
        str(os.getenv("EXECUTION_MODE", "paper") or "paper").strip().lower() == "paper"
        and _truthy("PAPER_AUTONOMOUS_LEARNING")
        and not _truthy("ENABLE_BROKER_SUBMISSION")
        and not _truthy("LIVE_TRADING_ARMED")
    )


def _num(value: Any, default: float = 0.0) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return default
    return result if math.isfinite(result) else default


def _json_obj(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return dict(value)
    if isinstance(value, str) and value.strip():
        try:
            parsed = json.loads(value)
        except Exception:
            return {}
        return dict(parsed) if isinstance(parsed, dict) else {}
    return {}


def _dt(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str) and value.strip():
        try:
            parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
        except ValueError:
            return None
    else:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _stop_loss_pct() -> float:
    try:
        import config
        value = _num(getattr(config, "STOP_LOSS_PCT", 0.06), 0.06)
    except Exception:
        value = 0.06
    return max(0.001, min(0.50, value))


def _signal_max_age_seconds(market: str) -> float:
    try:
        import config
        name = "DECISION_CRYPTO_MAX_AGE_MINUTES" if market == "crypto" else "DECISION_STOCK_MAX_AGE_MINUTES"
        minutes = _num(getattr(config, name, 10.0 if market == "crypto" else 30.0))
    except Exception:
        minutes = 10.0 if market == "crypto" else 30.0
    return max(60.0, minutes * 60.0)


@dataclass(frozen=True)
class ShadowExitConstraints:
    min_exit_ev_advantage_r: float = 0.05
    min_confirmations: int = 3
    min_episodes: int = 25
    min_trades: int = 50
    allowed_drawdown_limit_r: float = 5.0
    allowed_tail_loss_limit_r: float = -2.0
    allowed_cvar_loss_limit_r: float = -2.5
    min_cohort_trades: int = 30
    max_cohort_deterioration_r: float = 0.05

    @classmethod
    def from_env(cls) -> "ShadowExitConstraints":
        return cls(
            min_exit_ev_advantage_r=max(0.0, _num(os.getenv("SHADOW_EXIT_MIN_ADVANTAGE_R", "0.05"), 0.05)),
            min_confirmations=max(2, int(_num(os.getenv("SHADOW_EXIT_MIN_CONFIRMATIONS", "3"), 3))),
            min_episodes=max(10, int(_num(os.getenv("SHADOW_EXIT_MIN_EPISODES", "25"), 25))),
            min_trades=max(20, int(_num(os.getenv("SHADOW_EXIT_MIN_TRADES", "50"), 50))),
            allowed_drawdown_limit_r=max(0.1, _num(os.getenv("SHADOW_EXIT_MAX_DRAWDOWN_R", "5.0"), 5.0)),
            allowed_tail_loss_limit_r=min(0.0, _num(os.getenv("SHADOW_EXIT_TAIL_LIMIT_R", "-2.0"), -2.0)),
            allowed_cvar_loss_limit_r=min(0.0, _num(os.getenv("SHADOW_EXIT_CVAR_LIMIT_R", "-2.5"), -2.5)),
            min_cohort_trades=max(15, int(_num(os.getenv("SHADOW_EXIT_MIN_COHORT_TRADES", "30"), 30))),
            max_cohort_deterioration_r=max(
                0.0, _num(os.getenv("SHADOW_EXIT_MAX_COHORT_DETERIORATION_R", "0.05"), 0.05)
            ),
        )


def evidence_confirmation_is_new(current_state: dict[str, Any], observation_id: Any) -> bool:
    observation = str(observation_id or "").strip()
    if not observation:
        return False
    seen = current_state.setdefault("confirmation_observations", set())
    if observation in seen or observation == str(current_state.get("last_confirmation_observation") or ""):
        return False
    seen.add(observation)
    current_state["last_confirmation_observation"] = observation
    return True


def evaluate_forward_evidence_layer(
    current_state: dict[str, Any],
    telemetry: dict[str, Any],
    constraints: ShadowExitConstraints | None = None,
) -> tuple[bool, float]:
    """Evaluate Layer 2 only; no order or execution function is reachable here."""
    c = constraints or ShadowExitConstraints.from_env()
    hold_ev_r = _num(telemetry.get("hold_ev_r"), float("nan"))
    exit_ev_r = _num(telemetry.get("exit_ev_r"), float("nan"))
    if not math.isfinite(hold_ev_r) or not math.isfinite(exit_ev_r):
        return False, 0.0

    exit_advantage_r = exit_ev_r - hold_ev_r
    thesis_decay_confirmed = telemetry.get("thesis_decay_confirmed") is True
    condition = bool(
        exit_advantage_r >= c.min_exit_ev_advantage_r
        and thesis_decay_confirmed
    )

    if condition and evidence_confirmation_is_new(current_state, telemetry.get("observation_id")):
        current_state["evidence_persistence_counter"] = int(
            current_state.get("evidence_persistence_counter") or 0
        ) + 1
    elif not condition:
        current_state["evidence_persistence_counter"] = 0
        current_state["confirmation_observations"] = set()

    triggered = bool(
        condition
        and int(current_state.get("evidence_persistence_counter") or 0) >= c.min_confirmations
    )
    return triggered, exit_advantage_r


class CounterfactualSettlement:
    @staticmethod
    def calculate_immutable_challenger_r(
        position: dict[str, Any],
        *,
        market: str,
        trigger_price: float,
        quote: dict[str, Any],
        initial_risk_usd: float,
    ) -> tuple[float, dict[str, Any]]:
        """Freeze an adverse paper SELL using only trigger-time quote inputs."""
        from paper_execution_accounting import _simulate_fill_explicit_fee as simulate_fill

        entry_price = _num(position.get("entry_price") or position.get("average_price"))
        quantity = abs(_num(position.get("quantity")))
        risk = _num(initial_risk_usd)
        if entry_price <= 0 or quantity <= 0 or risk <= 0 or trigger_price <= 0:
            raise ValueError("counterfactual settlement requires positive entry, quantity, risk and trigger price")

        fill = simulate_fill(
            side="SELL",
            market=market,
            reference_price=trigger_price,
            quote=quote,
            order_value=quantity * trigger_price,
        )
        exit_fee = fill.fill_price * quantity * fill.fee_pct
        result_r = ((fill.fill_price - entry_price) * quantity - exit_fee) / risk
        payload = fill.to_dict()
        payload["fee_contract"] = COST_MODEL_VERSION
        payload["exit_fee_usd"] = exit_fee
        return result_r, payload


def _projected_hold_r(
    position: dict[str, Any],
    *,
    market: str,
    current_price: float,
    expected_edge_pct: float,
    quote: dict[str, Any],
    initial_risk_usd: float,
) -> tuple[float, dict[str, Any]]:
    """Estimate expected terminal R from trigger-time directional edge only."""
    from paper_execution_accounting import _simulate_fill_explicit_fee as simulate_fill

    entry = _num(position.get("entry_price") or position.get("average_price"))
    quantity = abs(_num(position.get("quantity")))
    projected_reference = current_price * (1.0 + expected_edge_pct / 100.0)
    if entry <= 0 or quantity <= 0 or initial_risk_usd <= 0 or projected_reference <= 0:
        raise ValueError("projected hold economics unavailable")

    # Current bid/ask cannot be reused as a future book. Preserve only the
    # trigger-time friction assumptions and liquidity fields.
    future_quote = {
        key: value
        for key, value in quote.items()
        if key not in {"bid", "ask", "price", "current_price", "last_price"}
    }
    fill = simulate_fill(
        side="SELL",
        market=market,
        reference_price=projected_reference,
        quote=future_quote,
        order_value=quantity * projected_reference,
    )
    exit_fee = fill.fill_price * quantity * fill.fee_pct
    hold_r = ((fill.fill_price - entry) * quantity - exit_fee) / initial_risk_usd
    payload = fill.to_dict()
    payload["fee_contract"] = COST_MODEL_VERSION
    payload["exit_fee_usd"] = exit_fee
    return hold_r, payload


def _confidence_bucket(value: Any) -> str:
    confidence = _num(value, float("nan"))
    if not math.isfinite(confidence):
        return "unknown"
    if confidence > 1.0:
        confidence /= 100.0
    confidence = max(0.0, min(1.0, confidence))
    low = int(confidence * 10) * 10
    if low >= 100:
        return "90-100"
    return f"{low:02d}-{low + 10:02d}"


def _position_key(market: str, position: dict[str, Any]) -> str:
    symbol = str(position.get("symbol") or "").upper()
    opened = str(position.get("opened_at") or position.get("updated_at") or "")
    return f"{market}:{symbol}:{opened}"


def _episode_id(market: str, event_time: Any) -> uuid.UUID:
    observed = _dt(event_time) or datetime.now(timezone.utc)
    # Conservative dependence buckets: one trading day for cash, four hours
    # for 24/7 crypto. Multiple trades in the same systemic window remain one
    # bootstrap unit rather than masquerading as independent evidence.
    seconds = 86400 if market == "cash" else 4 * 3600
    epoch = int(observed.timestamp())
    bucket = epoch - (epoch % seconds)
    label = f"garibaldi-shadow-exit:{market}:{bucket}:{seconds}"
    return uuid.uuid5(uuid.NAMESPACE_URL, label)


def _latest_signal(conn: Any, market: str, symbol: str) -> dict[str, Any] | None:
    row = conn.execute(
        """
        SELECT id,market,symbol,price,score,action,confidence,details,
               NULLIF(created_at,'')::timestamptz AS created_at
        FROM signals
        WHERE market=%s AND symbol=%s
          AND NULLIF(created_at,'')::timestamptz <= %s
        ORDER BY NULLIF(created_at,'')::timestamptz DESC, id DESC
        LIMIT 1
        """,
        (market, symbol, datetime.now(timezone.utc)),
    ).fetchone()
    if not row:
        return None
    item = dict(row)
    payload = _json_obj(item.get("details"))
    item["payload"] = payload

    from paper_strategy_economics import expected_edge_pct
    edge = expected_edge_pct(payload)
    if edge is None:
        # Only evidence available at the signal decision time is admissible.
        # A forecast attached later cannot retroactively qualify the signal.
        forecast = conn.execute(
            """
            SELECT expected_move_pct,created_at
            FROM forecasts
            WHERE market=%s AND symbol=%s AND signal_id=%s
              AND NULLIF(created_at,'')::timestamptz <= %s
            ORDER BY NULLIF(created_at,'')::timestamptz DESC, id DESC LIMIT 1
            """,
            (market, symbol, item.get("id"), item["created_at"]),
        ).fetchone()
        if forecast and forecast.get("expected_move_pct") is not None:
            edge = _num(forecast.get("expected_move_pct"), float("nan"))
            if math.isfinite(edge):
                payload["expected_edge_pct"] = edge
                payload["edge_provenance"] = "exact_signal_forecast_expected_move_pct"

    item["expected_edge_pct"] = edge
    return item


def _open_position_risk_context(
    conn: Any,
    market: str,
    symbol: str,
    position: dict[str, Any],
) -> tuple[float, str]:
    """Recover the aggregate immutable entry risk for currently open lots."""
    lots = list(
        conn.execute(
            """
            SELECT quantity_opened,quantity_remaining,entry_price,risk_snapshot
            FROM position_lots
            WHERE market=%s AND symbol=%s AND COALESCE(quantity_remaining,0)>0
            ORDER BY opened_at ASC,id ASC
            """,
            (market, symbol),
        ).fetchall()
    )
    total_risk = 0.0
    exact = True
    for raw in lots:
        lot = dict(raw)
        remaining = abs(_num(lot.get("quantity_remaining")))
        opened = abs(_num(lot.get("quantity_opened")))
        entry = _num(lot.get("entry_price"))
        if remaining <= 0 or entry <= 0:
            continue
        snapshot = _json_obj(lot.get("risk_snapshot"))
        snapshot_risk = _num(snapshot.get("initial_risk_usd"))
        snapshot_qty = abs(_num(snapshot.get("quantity"), opened))
        if snapshot_risk > 0 and snapshot_qty > 0:
            total_risk += snapshot_risk * min(1.0, remaining / snapshot_qty)
        else:
            exact = False
            total_risk += entry * remaining * _stop_loss_pct()

    if total_risk > 0:
        return total_risk, "exact_entry_stop_lots" if exact and lots else "mixed_entry_stop_and_configured_fallback"

    entry = _num(position.get("average_price") or position.get("entry_price"))
    quantity = abs(_num(position.get("quantity")))
    fallback = entry * quantity * _stop_loss_pct()
    return fallback, "configured_stop_loss_pct"


def _lot_risk_context(item: dict[str, Any]) -> tuple[float, float, str]:
    """Return lot-scaled initial risk USD, risk percent and provenance."""
    entry = _num(item.get("entry_price"))
    quantity = abs(_num(item.get("quantity")))
    if entry <= 0 or quantity <= 0:
        return 0.0, 0.0, "invalid"

    snapshot = _json_obj(item.get("risk_snapshot"))
    snapshot_risk = _num(snapshot.get("initial_risk_usd"))
    snapshot_qty = abs(_num(snapshot.get("quantity"), quantity))
    if snapshot_risk > 0 and snapshot_qty > 0:
        risk_usd = snapshot_risk * min(1.0, quantity / snapshot_qty)
        source = str(snapshot.get("risk_basis_source") or "entry_risk_snapshot")
    else:
        risk_usd = entry * quantity * _stop_loss_pct()
        source = "configured_stop_loss_pct_legacy_fallback"

    risk_pct = (risk_usd / (entry * quantity)) * 100.0 if entry * quantity > 0 else 0.0
    return risk_usd, risk_pct, source


def _trigger_telemetry(
    market: str,
    position: dict[str, Any],
    signal: dict[str, Any],
    *,
    initial_risk_usd: float,
    risk_basis_source: str,
    exclusions: dict[str, int] | None = None,
) -> dict[str, Any] | None:
    def reject(reason: str) -> None:
        if exclusions is not None:
            exclusions[reason] = exclusions.get(reason, 0) + 1
        return None
    payload = _json_obj(signal.get("payload"))
    route = _json_obj(payload.get("market_data_route"))
    quote = {**route, **payload}
    quote_verified = quote.get("quote_verified") is True
    # A signal insertion timestamp is not market quote provenance. Fail closed.
    quote_time = quote.get("quote_timestamp") or quote.get("source_quote_timestamp")
    from paper_exit_research import timestamp
    observed_at = timestamp(quote_time)
    now = datetime.now(timezone.utc)
    if not quote_verified or observed_at is None:
        return reject("unverified_quote_or_timestamp")
    if observed_at > now:
        return reject("future_quote")
    if (now - observed_at).total_seconds() > _signal_max_age_seconds(market):
        return reject("stale_quote")

    current_price = _num(quote.get("price") or signal.get("price"))
    entry_price = _num(position.get("average_price") or position.get("entry_price"))
    quantity = abs(_num(position.get("quantity")))
    expected_edge_pct = signal.get("expected_edge_pct")
    if expected_edge_pct is None:
        return reject("missing_expected_edge")
    expected_edge_pct = _num(expected_edge_pct, float("nan"))
    if (
        current_price <= 0
        or entry_price <= 0
        or quantity <= 0
        or not math.isfinite(expected_edge_pct)
    ):
        return reject("invalid_price_quantity_or_edge")

    if initial_risk_usd <= 0:
        return reject("unavailable_initial_risk")

    exit_r, exit_fill = CounterfactualSettlement.calculate_immutable_challenger_r(
        {
            "entry_price": entry_price,
            "quantity": quantity,
        },
        market=market,
        trigger_price=current_price,
        quote=quote,
        initial_risk_usd=initial_risk_usd,
    )
    try:
        hold_r, projected_fill = _projected_hold_r(
            {
                "entry_price": entry_price,
                "quantity": quantity,
            },
            market=market,
            current_price=current_price,
            expected_edge_pct=expected_edge_pct,
            quote=quote,
            initial_risk_usd=initial_risk_usd,
        )
    except ValueError:
        return reject("unavailable_projected_hold")

    action = str(signal.get("action") or payload.get("action") or "HOLD").upper()
    buy_actions = {"BUY", "STRONG_BUY", "STRONG BUY", "ACCUMULATE", "LONG"}
    thesis_decay_confirmed = bool(expected_edge_pct < 0.0 and action not in buy_actions)
    observation_id = str(quote_time or f"signal:{signal.get('id')}")

    return {
        "observation_id": observation_id,
        "current_timestamp": observed_at.isoformat(),
        "current_price": current_price,
        "hold_ev_r": hold_r,
        "exit_ev_r": exit_r,
        "expected_edge_pct": expected_edge_pct,
        "thesis_decay_confirmed": thesis_decay_confirmed,
        "action": action,
        "confidence": signal.get("confidence") if signal.get("confidence") is not None else payload.get("confidence"),
        "entry_pattern": payload.get("entry_pattern") or payload.get("schwager_pattern_tag") or "unclassified",
        "regime": payload.get("regime") or payload.get("market_regime") or payload.get("crypto_regime") or "unknown",
        "strategy": payload.get("strategy") or payload.get("strategy_name") or payload.get("reason") or "unattributed",
        "initial_risk_usd": initial_risk_usd,
        "risk_basis_source": risk_basis_source,
        "stop_loss_pct": _stop_loss_pct(),
        "quote": {
            key: quote.get(key)
            for key in (
                "provider", "provider_symbol", "requested_symbol", "quote_timestamp",
                "bid", "ask", "spread_pct", "estimated_slippage_pct", "slippage_pct",
                "estimated_fees_pct", "estimated_market_impact_pct", "avg_dollar_volume",
                "liquidity_value", "quote_verified",
            )
            if quote.get(key) is not None
        },
        "counterfactual_fill": exit_fill,
        "projected_hold_fill": projected_fill,
        "edge_provenance": payload.get("edge_provenance") or "signal_payload",
    }


def ensure_schema() -> None:
    if not active():
        return
    # The challenger finalizer consumes exact-cost regime metrics. Establish that
    # shared research schema first so worker start order cannot race the relation.
    from paper_regime_economics_shadow import ensure_schema as ensure_regime_schema
    ensure_regime_schema()
    from database import connect
    with connect() as conn:
        conn.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", (_SCHEMA_LOCK,))
        from paper_exit_research import ensure_reconciliation_schema
        ensure_reconciliation_schema(conn)
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS garibaldi_shadow_exit_epochs (
                model_version TEXT PRIMARY KEY,
                generation INTEGER NOT NULL,
                started_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                note TEXT NOT NULL,
                execution_impact TEXT NOT NULL DEFAULT 'NONE'
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS garibaldi_shadow_exit_triggers (
                position_key TEXT NOT NULL,
                model_version TEXT NOT NULL,
                market TEXT NOT NULL,
                symbol TEXT NOT NULL,
                opened_at TIMESTAMPTZ,
                trigger_at TIMESTAMPTZ NOT NULL,
                trigger_snapshot JSONB NOT NULL,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                PRIMARY KEY(position_key,model_version)
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS garibaldi_shadow_experiments (
                experiment_id BIGSERIAL PRIMARY KEY,
                trade_id TEXT NOT NULL,
                episode_id UUID NOT NULL,
                generation INTEGER NOT NULL,
                market TEXT NOT NULL,
                symbol TEXT NOT NULL,
                entry_pattern TEXT NOT NULL,
                regime TEXT NOT NULL,
                confidence_bucket TEXT NOT NULL,
                actual_exit_type TEXT NOT NULL,
                challenger_exit_type TEXT NOT NULL,
                actual_realized_r_net NUMERIC(12,6) NOT NULL,
                challenger_counterfactual_r_net NUMERIC(12,6) NOT NULL,
                delta_r NUMERIC(12,6) GENERATED ALWAYS AS
                    (challenger_counterfactual_r_net - actual_realized_r_net) STORED,
                mfe_r NUMERIC(12,6),
                mae_r NUMERIC(12,6),
                holding_time BIGINT,
                entry_time TIMESTAMPTZ,
                exit_time TIMESTAMPTZ NOT NULL,
                initial_risk_usd NUMERIC(14,6),
                risk_basis_source TEXT,
                model_version TEXT NOT NULL,
                cost_model_version TEXT NOT NULL,
                trigger_snapshot JSONB,
                execution_impact TEXT NOT NULL DEFAULT 'NONE',
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                CONSTRAINT unique_shadow_exit_trade_experiment
                    UNIQUE (trade_id, generation, model_version)
            )
            """
        )
        conn.execute("ALTER TABLE garibaldi_shadow_experiments ADD COLUMN IF NOT EXISTS entry_time TIMESTAMPTZ")
        conn.execute("ALTER TABLE garibaldi_shadow_experiments ADD COLUMN IF NOT EXISTS initial_risk_usd NUMERIC(14,6)")
        conn.execute("ALTER TABLE garibaldi_shadow_experiments ADD COLUMN IF NOT EXISTS risk_basis_source TEXT")
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_shadow_exit_episode ON garibaldi_shadow_experiments(episode_id)"
        )
        conn.execute(
            """CREATE INDEX IF NOT EXISTS idx_shadow_exit_cohorts
               ON garibaldi_shadow_experiments(entry_pattern,regime,confidence_bucket)"""
        )
        conn.execute(
            """CREATE INDEX IF NOT EXISTS idx_shadow_exit_market_time
               ON garibaldi_shadow_experiments(market,exit_time DESC)"""
        )
        conn.execute(
            """
            INSERT INTO garibaldi_shadow_exit_epochs(model_version,generation,note,execution_impact)
            VALUES (%s,%s,%s,'NONE')
            ON CONFLICT(model_version) DO NOTHING
            """,
            (
                MODEL_VERSION,
                GENERATION,
                "Forward-only Layer-2 challenger with exact entry-risk provenance and restart-safe triggers; champion hard-stop and profit-protection layers unchanged.",
            ),
        )


def sample_open_positions(market: str) -> int:
    """Update in-memory persistence only; never writes heartbeat telemetry rows."""
    if not active():
        return 0
    normalized_market = "cash" if str(market).lower() in {"cash", "stock"} else "crypto"
    from database import connect

    observed = 0
    exclusions = {"missing_symbol": 0, "missing_signal": 0, "ineligible_telemetry": 0}
    with connect() as conn:
        positions = list(
            conn.execute(
                """
                SELECT market,symbol,quantity,entry_price,average_price,current_price,
                       highest_price,opened_at,updated_at
                FROM positions
                WHERE market=%s AND COALESCE(quantity,0)>0
                """,
                (normalized_market,),
            ).fetchall()
        )
        for raw in positions:
            position = dict(raw)
            symbol = str(position.get("symbol") or "").upper()
            if not symbol:
                exclusions["missing_symbol"] += 1
                continue
            signal = _latest_signal(conn, normalized_market, symbol)
            if not signal:
                exclusions["missing_signal"] += 1
                continue
            initial_risk_usd, risk_basis_source = _open_position_risk_context(
                conn,
                normalized_market,
                symbol,
                position,
            )
            telemetry = _trigger_telemetry(
                normalized_market,
                position,
                signal,
                initial_risk_usd=initial_risk_usd,
                risk_basis_source=risk_basis_source,
                exclusions=exclusions,
            )
            if not telemetry:
                # Exact entry-lot identifiers for provenance diagnosis only.
                # No legacy edge reconstruction or eligibility changes.
                entry_lots = list(conn.execute(
                    """SELECT entry_signal_id,entry_forecast_id,decision_timestamp,
                              quote_timestamp,opened_at
                       FROM position_lots
                       WHERE market=%s AND symbol=%s
                         AND COALESCE(quantity_remaining,0)>0
                       ORDER BY opened_at ASC,id ASC LIMIT 8""",
                    (normalized_market, symbol),
                ).fetchall())
                log.warning(
                    "SHADOW_ENTRY_LOT_PROVENANCE | market=%s | symbol=%s | lots=%s",
                    normalized_market, symbol,
                    [dict(lot) for lot in entry_lots],
                )
                # Entry-time provenance is attribution only; it must never
                # replace the edge required at the current exit decision.
                entry_forecast_ids = [
                    str(lot["entry_forecast_id"])
                    for lot in entry_lots if lot.get("entry_forecast_id")
                ]
                payload = _json_obj(signal.get("payload"))
                log.warning(
                    "SHADOW_SIGNAL_PROVENANCE_EXCLUSION | market=%s | symbol=%s | "
                    "opened_at=%s | entry_forecast_ids=%s | current_signal_id=%s | "
                    "current_signal_at=%s | current_forecast_id=%s | "
                    "current_edge_source=%s | current_edge_present=%s | "
                    "current_forecast_unavailable_reason=%s | current_quote_at=%s | "
                    "current_quote_verified=%s",
                    normalized_market, symbol, position.get("opened_at"),
                    entry_forecast_ids, signal.get("id"), signal.get("created_at"),
                    payload.get("forecast_id"), payload.get("edge_provenance"),
                    signal.get("expected_edge_pct") is not None,
                    payload.get("forecast_unavailable_reason"),
                    payload.get("quote_timestamp"), payload.get("quote_verified"),
                )
                exclusions["ineligible_telemetry"] += 1
                continue

            key = _position_key(normalized_market, position)
            pending = conn.execute(
                """
                SELECT trigger_snapshot
                FROM garibaldi_shadow_exit_triggers
                WHERE position_key=%s AND model_version=%s
                """,
                (key, MODEL_VERSION),
            ).fetchone() or {}
            pending_snapshot = _json_obj(pending.get("trigger_snapshot"))
            with _STATE_LOCK:
                state = _POSITION_STATE.setdefault(
                    key,
                    {
                        "market": normalized_market,
                        "symbol": symbol,
                        "opened_at": position.get("opened_at"),
                        "evidence_persistence_counter": 0,
                        "trigger_snapshot": pending_snapshot or None,
                    },
                )
                if state.get("trigger_snapshot") is None and pending_snapshot:
                    state["trigger_snapshot"] = pending_snapshot
                triggered, advantage_r = (evaluate_forward_evidence_layer(state, telemetry)
                                         if state.get("trigger_snapshot") is None else (False, 0.0))
                if triggered and state.get("trigger_snapshot") is None:
                    snapshot = {
                        "_pending_position_key": key,
                        "challenger_trigger_at": telemetry["current_timestamp"],
                        "challenger_trigger_price": telemetry["current_price"],
                        "hold_ev_r": telemetry["hold_ev_r"],
                        "exit_ev_r": telemetry["exit_ev_r"],
                        "exit_advantage_r": advantage_r,
                        "expected_edge_pct": telemetry["expected_edge_pct"],
                        "thesis_decay_confirmed": telemetry["thesis_decay_confirmed"],
                        "confirmation_count": state.get("evidence_persistence_counter"),
                        "observation_id": telemetry["observation_id"],
                        "initial_risk_usd": telemetry["initial_risk_usd"],
                        "risk_basis_source": telemetry["risk_basis_source"],
                        "stop_loss_pct": telemetry["stop_loss_pct"],
                        "confidence": telemetry.get("confidence"),
                        "entry_pattern": telemetry.get("entry_pattern"),
                        "regime": telemetry.get("regime"),
                        "strategy": telemetry.get("strategy"),
                        "edge_provenance": telemetry.get("edge_provenance"),
                        "quote": telemetry.get("quote"),
                        "counterfactual_fill": telemetry.get("counterfactual_fill"),
                        "projected_hold_fill": telemetry.get("projected_hold_fill"),
                        "counterfactual_exit_r_net": telemetry["exit_ev_r"],
                        "model_version": MODEL_VERSION,
                        "cost_model_version": COST_MODEL_VERSION,
                        "execution_impact": "NONE",
                    }
                    state["trigger_snapshot"] = snapshot
                    conn.execute(
                        """
                        INSERT INTO garibaldi_shadow_exit_triggers(
                            position_key,model_version,market,symbol,opened_at,
                            trigger_at,trigger_snapshot
                        ) VALUES (%s,%s,%s,%s,%s,%s,%s::jsonb)
                        ON CONFLICT(position_key,model_version) DO NOTHING
                        """,
                        (
                            key,
                            MODEL_VERSION,
                            normalized_market,
                            symbol,
                            position.get("opened_at"),
                            snapshot.get("challenger_trigger_at"),
                            json.dumps(snapshot, default=str, sort_keys=True),
                        ),
                    )
                    log.info(
                        "SHADOW EXIT TRIGGER | market=%s | symbol=%s | advantage_r=%.4f | confirmations=%s | "
                        "mode=shadow | execution_impact=NONE | broker_submission=NONE | live_trading=DISARMED",
                        normalized_market,
                        symbol,
                        advantage_r,
                        state.get("evidence_persistence_counter"),
                    )
            observed += 1
    log.info("SHADOW EXIT SAMPLING | market=%s | open_positions=%s | eligible_observations=%s | exclusions=%s | execution_impact=NONE",
             normalized_market, len(positions), observed, json.dumps(exclusions, sort_keys=True))
    return observed


def _matching_trigger(
    conn: Any,
    market: str,
    symbol: str,
    entry_time: Any,
    exit_time: Any,
) -> dict[str, Any] | None:
    start = _dt(entry_time)
    end = _dt(exit_time)
    if start is None or end is None:
        return None
    candidates: list[tuple[datetime, dict[str, Any]]] = []
    with _STATE_LOCK:
        for state in _POSITION_STATE.values():
            if state.get("market") != market or state.get("symbol") != symbol:
                continue
            snapshot = state.get("trigger_snapshot")
            if not isinstance(snapshot, dict):
                continue
            trigger_at = _dt(snapshot.get("challenger_trigger_at"))
            if trigger_at is not None and start <= trigger_at <= end:
                candidates.append((trigger_at, dict(snapshot)))

    durable = list(
        conn.execute(
            """
            SELECT position_key,trigger_at,trigger_snapshot
            FROM garibaldi_shadow_exit_triggers
            WHERE model_version=%s AND market=%s AND symbol=%s
              AND trigger_at BETWEEN %s AND %s
            ORDER BY trigger_at ASC
            """,
            (MODEL_VERSION, market, symbol, start, end),
        ).fetchall()
    )
    for row in durable:
        snapshot = _json_obj(row.get("trigger_snapshot"))
        trigger_at = _dt(row.get("trigger_at") or snapshot.get("challenger_trigger_at"))
        if trigger_at is not None:
            snapshot["_pending_position_key"] = str(row.get("position_key") or "")
            candidates.append((trigger_at, snapshot))

    if not candidates:
        return None
    candidates.sort(key=lambda item: item[0])
    return candidates[0][1]

def _counterfactual_net_r(item: dict[str, Any], fill: dict[str, Any], risk: float) -> float | None:
    """Charge explicit simulated exit fees and attributed entry fees once.

    Frozen fills without this model's explicit fee contract are unqualified.
    """
    simulated_fee_pct = _num(fill.get("fee_pct"), float("nan"))
    if (fill.get("fee_contract") != COST_MODEL_VERSION
            or not math.isfinite(simulated_fee_pct) or simulated_fee_pct < 0):
        return None
    total = _num(item.get("round_trip_fees"), float("nan"))
    exit_fee = _num(item.get("actual_exit_fees"), float("nan"))
    price = _num(fill.get("fill_price"))
    entry = _num(item.get("entry_price"))
    quantity = abs(_num(item.get("quantity")))
    if (not math.isfinite(total) or not math.isfinite(exit_fee)
            or exit_fee < 0 or total < exit_fee or price <= 0
            or entry <= 0 or quantity <= 0 or risk <= 0):
        return None
    return ((price - entry) * quantity - price * quantity * simulated_fee_pct - (total - exit_fee)) / risk


def finalize_closed_trades(market: str, limit: int = 250) -> int:
    if not active():
        return 0
    normalized_market = "cash" if str(market).lower() in {"cash", "stock"} else "crypto"
    from database import connect

    created = 0
    exclusions = {"invalid_identity_or_quantity": 0, "invalid_risk": 0,
                  "no_matched_trigger": 0, "unavailable_fill_or_costs": 0}
    with connect() as conn:
        epoch = conn.execute(
            "SELECT started_at FROM garibaldi_shadow_exit_epochs WHERE model_version=%s",
            (MODEL_VERSION,),
        ).fetchone() or {}
        started_at = epoch.get("started_at")
        if started_at is None:
            return 0

        rows = list(
            conn.execute(
                """
                SELECT m.trade_id,m.market,m.symbol,m.strategy,m.regime,m.entry_pattern,
                       m.entry_time,m.exit_time,m.entry_price,m.exit_price,
                       m.round_trip_net_pnl,m.round_trip_fees,m.cost_provenance,
                       m.mfe_pct,m.mae_pct,m.excursion_sample_count,
                       l.quantity,l.order_id,l.feature_snapshot,l.risk_snapshot,
                       l.fees AS actual_exit_fees
                FROM paper_regime_trade_metrics m
                JOIN trade_ledger l ON l.trade_id=m.trade_id
                WHERE m.market=%s
                  AND l.broker_mode='PAPER' AND l.account_environment='PAPER'
                  AND m.exit_time >= %s
                  AND m.cost_provenance='exact_lot'
                  AND m.round_trip_net_pnl IS NOT NULL
                  AND m.entry_time IS NOT NULL
                  AND m.exit_time IS NOT NULL
                  AND NOT EXISTS (
                      SELECT 1 FROM garibaldi_shadow_experiments e
                      WHERE e.trade_id=m.trade_id
                        AND e.generation=%s
                        AND e.model_version=%s
                  )
                ORDER BY m.exit_time ASC
                LIMIT %s
                """,
                (normalized_market, started_at, GENERATION, MODEL_VERSION, max(1, int(limit))),
            ).fetchall()
        )

        consumed_pending_keys: set[str] = set()
        for raw in rows:
            item = dict(raw)
            trade_id = str(item.get("trade_id") or "").strip()
            symbol = str(item.get("symbol") or "").upper()
            entry_price = _num(item.get("entry_price"))
            quantity = abs(_num(item.get("quantity")))
            if not trade_id or not symbol or entry_price <= 0 or quantity <= 0:
                exclusions["invalid_identity_or_quantity"] += 1
                continue
            initial_risk_usd, initial_risk_pct, risk_basis_source = _lot_risk_context(item)
            if initial_risk_usd <= 0 or initial_risk_pct <= 0:
                exclusions["invalid_risk"] += 1
                continue

            actual_r = _num(item.get("round_trip_net_pnl")) / initial_risk_usd
            trigger = _matching_trigger(
                conn,
                normalized_market,
                symbol,
                item.get("entry_time"),
                item.get("exit_time"),
            )
            if trigger:
                fill = _json_obj(trigger.get("counterfactual_fill"))
                counterfactual_r = _counterfactual_net_r(item, fill, initial_risk_usd)
                if counterfactual_r is not None:
                    challenger_r = counterfactual_r
                    challenger_exit_type = "FORWARD_EVIDENCE_EXIT"
                    pending_key = str(trigger.get("_pending_position_key") or "")
                    if pending_key:
                        consumed_pending_keys.add(pending_key)
                else:
                    exclusions["unavailable_fill_or_costs"] += 1
                    challenger_r = actual_r
                    challenger_exit_type = "COUNTERFACTUAL_FILL_UNAVAILABLE"
                confidence_bucket = _confidence_bucket(trigger.get("confidence"))
            else:
                exclusions["no_matched_trigger"] += 1
                challenger_r = actual_r
                challenger_exit_type = "ACTUAL_EXIT_FALLBACK"
                confidence_bucket = _confidence_bucket(_json_obj(item.get("feature_snapshot")).get("confidence"))

            entry_time = _dt(item.get("entry_time"))
            exit_time = _dt(item.get("exit_time"))
            holding_seconds = (
                int(max(0.0, (exit_time - entry_time).total_seconds()))
                if entry_time is not None and exit_time is not None
                else None
            )
            mfe_r = (
                _num(item.get("mfe_pct")) / initial_risk_pct
                if item.get("mfe_pct") is not None and initial_risk_pct > 0
                else None
            )
            mae_r = (
                _num(item.get("mae_pct")) / initial_risk_pct
                if item.get("mae_pct") is not None and initial_risk_pct > 0
                else None
            )
            actual_exit_type = str(item.get("order_id") or "ACTUAL_EXIT")[:64]
            snapshot_json = json.dumps(trigger, default=str, sort_keys=True) if trigger else None

            conn.execute(
                """
                INSERT INTO garibaldi_shadow_experiments(
                    trade_id,episode_id,generation,market,symbol,entry_pattern,regime,
                    confidence_bucket,actual_exit_type,challenger_exit_type,
                    actual_realized_r_net,challenger_counterfactual_r_net,mfe_r,mae_r,
                    holding_time,entry_time,exit_time,initial_risk_usd,risk_basis_source,
                    model_version,cost_model_version,trigger_snapshot,execution_impact
                ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb,'NONE')
                ON CONFLICT (trade_id,generation,model_version) DO NOTHING
                """,
                (
                    trade_id,
                    _episode_id(normalized_market, item.get("entry_time")),
                    GENERATION,
                    normalized_market,
                    symbol,
                    str(item.get("entry_pattern") or "unclassified")[:80],
                    str(item.get("regime") or "unknown")[:80],
                    confidence_bucket,
                    actual_exit_type,
                    challenger_exit_type,
                    actual_r,
                    challenger_r,
                    mfe_r,
                    mae_r,
                    holding_seconds,
                    item.get("entry_time"),
                    item.get("exit_time"),
                    initial_risk_usd,
                    risk_basis_source,
                    MODEL_VERSION,
                    COST_MODEL_VERSION,
                    snapshot_json,
                ),
            )
            created += 1

        for pending_key in consumed_pending_keys:
            conn.execute(
                """
                DELETE FROM garibaldi_shadow_exit_triggers
                WHERE position_key=%s AND model_version=%s
                """,
                (pending_key, MODEL_VERSION),
            )
            with _STATE_LOCK:
                _POSITION_STATE.pop(pending_key, None)
    log.info(
        "SHADOW EXIT FINALIZATION | market=%s | exact_cost_candidates=%s | "
        "processed=%s | exclusions=%s | execution_impact=NONE",
        normalized_market, len(rows), created, json.dumps(exclusions, sort_keys=True),
    )
    return created


class BrainCohortAnalyzerV4:
    """Paired episode bootstrap plus portfolio-safety vetoes.

    A positive statistical result is evidence only. This class never promotes,
    sizes, submits, or modifies a trade.
    """

    def __init__(
        self,
        completed_experiments_list: list[dict[str, Any]],
        constraints: ShadowExitConstraints | None = None,
    ):
        self.df = pd.DataFrame(completed_experiments_list)
        self.c = constraints or ShadowExitConstraints.from_env()

    @staticmethod
    def _safe_profit_factor(series: pd.Series) -> float:
        values = pd.to_numeric(series, errors="coerce").dropna()
        profits = values[values > 0].sum()
        losses = abs(values[values < 0].sum())
        if losses > 0:
            return float(profits / losses)
        return float("inf") if profits > 0 else 1.0

    def _episode_returns(self, column: str) -> pd.DataFrame:
        if self.df.empty or column not in self.df:
            return pd.DataFrame(columns=["episode_id", "exit_time", column])
        frame = self.df[["episode_id", "exit_time", column]].copy()
        frame[column] = pd.to_numeric(frame[column], errors="coerce")
        frame["exit_time"] = pd.to_datetime(frame["exit_time"], utc=True, errors="coerce")
        frame = frame.dropna(subset=["episode_id", column])
        return (
            frame.groupby("episode_id", as_index=False)
            .agg(exit_time=("exit_time", "max"), **{column: (column, "sum")})
            .sort_values("exit_time", kind="stable")
        )

    def _max_drawdown_r(self, column: str) -> float:
        if self.df.empty or column not in self.df:
            return 0.0
        frame = self.df[["exit_time", column]].copy()
        frame[column] = pd.to_numeric(frame[column], errors="coerce")
        frame["exit_time"] = pd.to_datetime(frame["exit_time"], utc=True, errors="coerce")
        frame = frame.dropna(subset=["exit_time", column]).sort_values("exit_time", kind="stable")
        if frame.empty:
            return 0.0
        values = frame[column].astype(float)
        cumulative = values.cumsum()
        # Include a zero starting-equity anchor so an initial losing sequence is
        # counted as drawdown rather than incorrectly becoming the first peak.
        running_max = cumulative.cummax().clip(lower=0.0)
        drawdown = cumulative - running_max
        return abs(float(drawdown.min())) if not drawdown.empty else 0.0

    def bootstrap_clustered_delta_ci(
        self,
        frame: pd.DataFrame | None = None,
        *,
        num_resamples: int = 5000,
        alpha: float = 0.05,
        seed: int = 42,
    ) -> tuple[float, float]:
        source = self.df if frame is None else frame
        if source.empty or "episode_id" not in source or "delta_r" not in source:
            return float("nan"), float("nan")

        episode_means = (
            source.assign(delta_r=pd.to_numeric(source["delta_r"], errors="coerce"))
            .dropna(subset=["episode_id", "delta_r"])
            .groupby("episode_id")["delta_r"]
            .mean()
            .to_numpy(dtype=float)
        )
        if len(episode_means) < 10:
            return float("nan"), float("nan")

        rng = np.random.default_rng(seed)
        boot_means = np.empty(max(100, int(num_resamples)), dtype=float)
        for i in range(len(boot_means)):
            boot_means[i] = rng.choice(
                episode_means,
                size=len(episode_means),
                replace=True,
            ).mean()
        return (
            float(np.quantile(boot_means, alpha / 2.0)),
            float(np.quantile(boot_means, 1.0 - alpha / 2.0)),
        )

    def _tail_metrics(self, column: str, alpha: float = 0.05) -> tuple[float, float]:
        episodes = self._episode_returns(column)
        if episodes.empty:
            return float("nan"), float("nan")
        values = pd.to_numeric(episodes[column], errors="coerce").dropna()
        if values.empty:
            return float("nan"), float("nan")
        var = float(values.quantile(alpha))
        tail = values[values <= var]
        cvar = float(tail.mean()) if not tail.empty else var
        return var, cvar

    def evaluate_portfolio_safety_gate(self) -> dict[str, Any]:
        required = {
            "episode_id",
            "exit_time",
            "entry_pattern",
            "regime",
            "actual_realized_r_net",
            "challenger_counterfactual_r_net",
        }
        if self.df.empty or not required.issubset(self.df.columns):
            return {
                "promotion_evidence_ready": False,
                "reason": "insufficient_or_invalid_dataset",
                "execution_impact": "NONE",
            }

        self.df = self.df.copy()
        observed_trade_count = int(len(self.df))
        # Only rows where the challenger actually fired are paired treatment
        # evidence. ACTUAL_EXIT_FALLBACK rows are useful audit/control records,
        # but challenger_r == actual_r by construction; counting them would
        # manufacture zero-delta samples and inflate episode/trade gates.
        if "challenger_exit_type" in self.df.columns:
            self.df = self.df[
                self.df["challenger_exit_type"] == "FORWARD_EVIDENCE_EXIT"
            ].copy()

        self.df["actual_realized_r_net"] = pd.to_numeric(
            self.df["actual_realized_r_net"], errors="coerce"
        )
        self.df["challenger_counterfactual_r_net"] = pd.to_numeric(
            self.df["challenger_counterfactual_r_net"], errors="coerce"
        )
        self.df = self.df.dropna(
            subset=["actual_realized_r_net", "challenger_counterfactual_r_net", "episode_id"]
        )
        self.df["delta_r"] = (
            self.df["challenger_counterfactual_r_net"]
            - self.df["actual_realized_r_net"]
        )

        trade_count = int(len(self.df))
        episode_count = int(self.df["episode_id"].nunique())
        boot_low, boot_high = self.bootstrap_clustered_delta_ci()
        actual_pf = self._safe_profit_factor(self.df["actual_realized_r_net"])
        challenger_pf = self._safe_profit_factor(self.df["challenger_counterfactual_r_net"])
        actual_dd = self._max_drawdown_r("actual_realized_r_net")
        challenger_dd = self._max_drawdown_r("challenger_counterfactual_r_net")
        tail_var, tail_cvar = self._tail_metrics("challenger_counterfactual_r_net")

        harmed_cohorts: list[dict[str, Any]] = []
        for (entry_pattern, regime), group in self.df.groupby(["entry_pattern", "regime"], dropna=False):
            if len(group) < self.c.min_cohort_trades:
                continue
            mean_delta = float(group["delta_r"].mean())
            if mean_delta < -self.c.max_cohort_deterioration_r:
                harmed_cohorts.append(
                    {
                        "entry_pattern": str(entry_pattern),
                        "regime": str(regime),
                        "samples": int(len(group)),
                        "mean_delta_r": mean_delta,
                    }
                )

        conditions = {
            "sufficient_episodes": episode_count >= self.c.min_episodes,
            "sufficient_trades": trade_count >= self.c.min_trades,
            "paired_episode_ci_positive": bool(math.isfinite(boot_low) and boot_low > 0.0),
            "profit_factor_improving": challenger_pf > actual_pf,
            "drawdown_bounded": challenger_dd <= self.c.allowed_drawdown_limit_r,
            "tail_var_protected": bool(
                math.isfinite(tail_var) and tail_var >= self.c.allowed_tail_loss_limit_r
            ),
            "cvar_protected": bool(
                math.isfinite(tail_cvar) and tail_cvar >= self.c.allowed_cvar_loss_limit_r
            ),
            "no_mature_cohort_materially_harmed": not harmed_cohorts,
        }
        ready = all(conditions.values())
        return {
            "promotion_evidence_ready": ready,
            "conditions": conditions,
            "trade_count": trade_count,
            "observed_trade_count": observed_trade_count,
            "episode_count": episode_count,
            "paired_mean_delta_r": float(self.df["delta_r"].mean()) if trade_count else 0.0,
            "bootstrap_delta_ci": (boot_low, boot_high),
            "actual_profit_factor": actual_pf,
            "challenger_profit_factor": challenger_pf,
            "actual_max_drawdown_r": actual_dd,
            "challenger_max_drawdown_r": challenger_dd,
            "challenger_episode_var_05_r": tail_var,
            "challenger_episode_cvar_05_r": tail_cvar,
            "harmed_cohorts": harmed_cohorts,
            "execution_impact": "NONE",
            "promotion_action": "NONE",
        }


def shadow_evidence_report(market: str) -> dict[str, Any]:
    if not active():
        return {"active": False, "execution_impact": "NONE"}
    normalized_market = "cash" if str(market).lower() in {"cash", "stock"} else "crypto"
    from database import connect
    with connect() as conn:
        rows = list(
            conn.execute(
                """
                SELECT trade_id,episode_id,generation,market,symbol,entry_pattern,regime,
                       confidence_bucket,actual_exit_type,challenger_exit_type,
                       actual_realized_r_net,challenger_counterfactual_r_net,delta_r,
                       mfe_r,mae_r,holding_time,entry_time,exit_time,initial_risk_usd,
                       risk_basis_source,model_version,cost_model_version
                FROM garibaldi_shadow_experiments
                WHERE market=%s AND model_version=%s
                ORDER BY exit_time ASC
                """,
                (normalized_market, MODEL_VERSION),
            ).fetchall()
        )
    report = BrainCohortAnalyzerV4([dict(row) for row in rows]).evaluate_portfolio_safety_gate()
    return {
        "active": True,
        "market": normalized_market,
        "model_version": MODEL_VERSION,
        **report,
    }


def emit_summary(market: str) -> None:
    try:
        report = shadow_evidence_report(market)
        if not report.get("active"):
            return
        from paper_exit_research import emit_reconciliation
        try:
            emit_reconciliation(market)
        except Exception as exc:
            log.warning("PAPER FIFO RECONCILIATION | market=%s | status=UNAVAILABLE | reason=%s | execution_impact=NONE",
                        market, exc.__class__.__name__)
        low, high = report.get("bootstrap_delta_ci", (float("nan"), float("nan")))
        log.info(
            "SHADOW EXIT EVIDENCE | market=%s | trades=%s | episodes=%s | observed_trades=%s | mean_delta_r=%s | "
            "ci_low=%s | ci_high=%s | promotion_evidence_ready=%s | execution_impact=NONE | "
            "promotion_action=NONE | live_trading=DISARMED",
            report.get("market"),
            report.get("trade_count", 0),
            report.get("episode_count", 0),
            report.get("observed_trade_count", 0),
            report.get("paired_mean_delta_r", 0.0),
            low,
            high,
            report.get("promotion_evidence_ready", False),
        )
    except Exception as exc:
        log.warning(
            "SHADOW EXIT EVIDENCE | status=UNAVAILABLE | reason=%s | execution_impact=NONE",
            exc.__class__.__name__,
        )


def _loop(interval_seconds: float, market: str) -> None:
    cycles = 0
    while not _STOP.wait(interval_seconds):
        if not active():
            return
        try:
            sample_open_positions(market)
            finalize_closed_trades(market)
            cycles += 1
            if cycles == 1 or cycles % 10 == 0:
                emit_summary(market)
        except Exception as exc:
            log.warning(
                "SHADOW EXIT CHALLENGER | sampler=ERROR | market=%s | reason=%s | execution_impact=NONE",
                market,
                exc.__class__.__name__,
            )


def install_paper_shadow_exit_challenger(market: str = "crypto") -> bool:
    """Start research-only Layer-2 measurement with no execution authority."""
    global _THREAD
    if not active():
        return False
    normalized_market = "cash" if str(market).lower() in {"cash", "stock"} else "crypto"
    try:
        ensure_schema()
        sample_open_positions(normalized_market)
        finalize_closed_trades(normalized_market)
        emit_summary(normalized_market)
    except Exception as exc:
        log.warning(
            "SHADOW EXIT CHALLENGER | startup=ERROR | market=%s | reason=%s | "
            "execution_impact=NONE | broker_submission=NONE | live_trading=DISARMED",
            normalized_market,
            exc.__class__.__name__,
        )
        return False

    if _THREAD and _THREAD.is_alive():
        return True
    interval = max(30.0, _num(os.getenv("SHADOW_EXIT_SAMPLE_SECONDS", "60"), 60.0))
    _STOP.clear()
    _THREAD = threading.Thread(
        target=_loop,
        args=(interval, normalized_market),
        name=f"shadow-exit-{normalized_market}",
        daemon=True,
    )
    _THREAD.start()
    log.info(
        "SHADOW EXIT CHALLENGER | active=True | market=%s | version=%s | generation=%s | "
        "min_advantage_r=%.4f | confirmations=%s | mode=shadow | execution_impact=NONE | "
        "broker_submission=NONE | live_trading=DISARMED",
        normalized_market,
        MODEL_VERSION,
        GENERATION,
        ShadowExitConstraints.from_env().min_exit_ev_advantage_r,
        ShadowExitConstraints.from_env().min_confirmations,
    )
    return True
