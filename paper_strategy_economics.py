from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import logging
import math
import os
import re
import time
from typing import Any


log = logging.getLogger("paper-strategy-economics")
_CACHE: dict[str, tuple[float, "StrategyEconomics"]] = {}
_EPOCH_NAME = "post-churn-capacity-economics-v1"
_ORACLE_COUNCIL_V3_KEY = "oracle_council_v3"


@dataclass(frozen=True)
class StrategyEconomics:
    strategy: str
    sample_count: int
    net_pnl: float
    gross_pnl: float
    fees: float
    win_rate: float
    average_win: float
    average_loss: float
    profit_factor: float
    expectancy: float
    average_holding_minutes: float
    size_multiplier: float
    model_validated: bool
    epoch_name: str = _EPOCH_NAME


def _truthy(name: str, default: str = "false") -> bool:
    return str(os.getenv(name, default) or default).strip().lower() in {"1", "true", "yes", "on"}


def active() -> bool:
    return bool(
        str(os.getenv("EXECUTION_MODE", "paper") or "paper").strip().lower() == "paper"
        and _truthy("PAPER_AUTONOMOUS_LEARNING")
        and not _truthy("ENABLE_BROKER_SUBMISSION")
        and not _truthy("LIVE_TRADING_ARMED")
    )


def _number(value: Any, default: float = 0.0) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return number if math.isfinite(number) else default


def normalize_strategy_identity(value: Any) -> str:
    """Return a stable economics key for strategy provenance.

    Crypto Oracle Council signals carry a human-readable rationale in `strategy`
    whose momentum/RSI/volatility numbers change every scan. Position lots
    correctly preserve the entry-time text, so exact string matching against a
    later scan can never accumulate closed-trade samples. Always-on market-pulse
    signals have the same problem because the engine's rationale is dynamic and
    the base OracleSignal has no dedicated strategy field. Collapse only these
    known dynamic provenance families to stable keys; preserve explicit strategy
    names verbatim so unrelated strategies remain independently attributed.
    """
    raw = str(value or "").strip()
    if not raw:
        return "unattributed"
    lowered = raw.lower()
    if "oracle council v3" in lowered:
        return _ORACLE_COUNCIL_V3_KEY
    pulse = re.match(r"^always-on\s+([0-9]+(?:\.[0-9]+)?[mhd])\s+market pulse\.", lowered)
    if pulse:
        interval = pulse.group(1).replace(".", "_")
        return f"always_on_{interval}_market_pulse"
    return raw[:160]


def strategy_identity(signal: Any) -> str:
    def value(name: str, default: Any = None) -> Any:
        if isinstance(signal, dict):
            return signal.get(name, default)
        return getattr(signal, name, default)

    # Prefer explicit stable strategy provenance when available. `strategy` may
    # be a dynamic rationale, which normalize_strategy_identity handles below.
    for name in ("strategy_name", "source_strategy", "strategy", "advisor_action", "reason"):
        candidate = str(value(name, "") or "").strip()
        if candidate:
            return normalize_strategy_identity(candidate)
    return "unattributed"


def _model_identity(signal: Any) -> tuple[str, str]:
    def value(name: str, default: Any = None) -> Any:
        if isinstance(signal, dict):
            return signal.get(name, default)
        return getattr(signal, name, default)

    model = str(value("model", "") or value("forecast_model", "") or "").strip()
    version = str(value("model_version", "") or value("forecast_model_version", "") or "").strip()
    return model, version


def model_validation_ok(signal: Any) -> bool:
    """Permit positive size expansion only when current model governance passes.

    Failure or missing evidence never blocks paper exploration; it merely prevents
    economics from increasing size above the neutral 1.0 multiplier.
    """
    model, version = _model_identity(signal)
    if not model:
        return False
    try:
        from capital_model_governance import model_governance_assessment

        assessment = model_governance_assessment(model, version)
        return bool(assessment.eligible_for_approval)
    except Exception:
        return False


def ensure_post_fix_epoch() -> None:
    """Create an auditable post-fix measurement epoch without deleting history."""
    if not active():
        return
    try:
        from database import connect

        with connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS paper_learning_epochs (
                    id BIGSERIAL PRIMARY KEY,
                    epoch_name TEXT NOT NULL UNIQUE,
                    market TEXT NOT NULL DEFAULT 'crypto',
                    started_at TIMESTAMPTZ NOT NULL,
                    ended_at TIMESTAMPTZ,
                    baseline_note TEXT NOT NULL,
                    live_trading_armed BOOLEAN NOT NULL DEFAULT FALSE,
                    broker_submission_enabled BOOLEAN NOT NULL DEFAULT FALSE
                )
                """
            )
            conn.execute(
                """
                INSERT INTO paper_learning_epochs(
                    epoch_name, market, started_at, baseline_note,
                    live_trading_armed, broker_submission_enabled
                )
                VALUES (%s, 'crypto', %s, %s, FALSE, FALSE)
                ON CONFLICT (epoch_name) DO NOTHING
                """,
                (
                    _EPOCH_NAME,
                    datetime.now(timezone.utc),
                    "Post churn/cooldown/capacity fixes; preserves all prior trades and losses for comparison.",
                ),
            )
    except Exception as exc:
        log.warning("PAPER ECONOMICS EPOCH | status=UNAVAILABLE | reason=%s", exc.__class__.__name__)


def _epoch_start() -> datetime | None:
    try:
        from database import row

        item = row(
            "SELECT started_at FROM paper_learning_epochs WHERE epoch_name=%s LIMIT 1",
            (_EPOCH_NAME,),
        )
        return item.get("started_at") if item else None
    except Exception:
        return None


def _ledger_records(strategy: str) -> list[dict[str, Any]]:
    """Read canonical lot-attributed closes using stable strategy identity.

    `trade_ledger` persists entry/exit timestamps as ISO text while the learning
    epoch boundary is TIMESTAMPTZ. Cast those text fields at the query boundary so
    PostgreSQL compares like types and Python receives typed datetimes for holding
    time. Strategy matching remains normalized in Python because Council rationale
    text legitimately changes scan to scan. The trades fallback uses the same
    timestamp conversion and normalization rules.
    """
    target = normalize_strategy_identity(strategy)
    try:
        from database import rows

        start = _epoch_start()
        if start is not None:
            records = rows(
                """
                SELECT strategy, symbol, net_pnl, gross_pnl, fees, return_pct,
                       NULLIF(entry_time,'')::timestamptz AS entry_time,
                       NULLIF(exit_time,'')::timestamptz AS exit_time,
                       model, model_version
                FROM trade_ledger
                WHERE market='crypto' AND side='SELL'
                  AND NULLIF(exit_time,'')::timestamptz >= %s
                ORDER BY NULLIF(exit_time,'')::timestamptz DESC
                LIMIT 1000
                """,
                (start,),
            )
        else:
            records = rows(
                """
                SELECT strategy, symbol, net_pnl, gross_pnl, fees, return_pct,
                       NULLIF(entry_time,'')::timestamptz AS entry_time,
                       NULLIF(exit_time,'')::timestamptz AS exit_time,
                       model, model_version
                FROM trade_ledger
                WHERE market='crypto' AND side='SELL'
                ORDER BY NULLIF(exit_time,'')::timestamptz DESC
                LIMIT 1000
                """
            )
        matched = [
            dict(item)
            for item in (records or [])
            if normalize_strategy_identity(item.get("strategy")) == target
        ]
        if matched:
            return matched[:250]
    except Exception as exc:
        log.warning(
            "PAPER STRATEGY ATTRIBUTION | source=trade_ledger | status=UNAVAILABLE | reason=%s",
            exc.__class__.__name__,
        )

    try:
        from database import rows

        start = _epoch_start()
        if start is not None:
            records = rows(
                """
                SELECT reason AS strategy, symbol, realized_pnl AS net_pnl,
                       COALESCE(gross_realized_pnl, realized_pnl) AS gross_pnl,
                       COALESCE(fees,0) AS fees, NULL AS return_pct,
                       NULL AS entry_time,
                       NULLIF(created_at,'')::timestamptz AS exit_time,
                       NULL AS model, NULL AS model_version
                FROM trades
                WHERE market='crypto' AND side='SELL'
                  AND NULLIF(created_at,'')::timestamptz >= %s
                ORDER BY NULLIF(created_at,'')::timestamptz DESC LIMIT 1000
                """,
                (start,),
            )
        else:
            records = rows(
                """
                SELECT reason AS strategy, symbol, realized_pnl AS net_pnl,
                       COALESCE(gross_realized_pnl, realized_pnl) AS gross_pnl,
                       COALESCE(fees,0) AS fees, NULL AS return_pct,
                       NULL AS entry_time,
                       NULLIF(created_at,'')::timestamptz AS exit_time,
                       NULL AS model, NULL AS model_version
                FROM trades
                WHERE market='crypto' AND side='SELL'
                ORDER BY NULLIF(created_at,'')::timestamptz DESC LIMIT 1000
                """
            )
        return [
            dict(item)
            for item in (records or [])
            if normalize_strategy_identity(item.get("strategy")) == target
        ][:250]
    except Exception as exc:
        log.warning(
            "PAPER STRATEGY ATTRIBUTION | source=trades | status=UNAVAILABLE | reason=%s",
            exc.__class__.__name__,
        )
        return []


def _holding_minutes(record: dict[str, Any]) -> float:
    entry = record.get("entry_time")
    exit_ = record.get("exit_time")
    if not entry or not exit_:
        return 0.0
    try:
        return max(0.0, (exit_ - entry).total_seconds() / 60.0)
    except Exception:
        return 0.0


def _multiplier(*, sample_count: int, expectancy: float, profit_factor: float, model_validated: bool) -> float:
    min_samples = max(3, int(os.getenv("PAPER_STRATEGY_ECON_MIN_SAMPLES", "8")))
    exploration_floor = min(1.0, max(0.10, _number(os.getenv("PAPER_STRATEGY_EXPLORATION_FLOOR", "0.35"), 0.35)))
    max_boost = max(1.0, min(1.50, _number(os.getenv("PAPER_STRATEGY_MAX_SIZE_MULTIPLIER", "1.25"), 1.25)))
    if sample_count < min_samples:
        return 1.0
    if expectancy < 0 or profit_factor < 0.90:
        # Keep exploration alive, but shrink clearly negative-expectancy behavior.
        severity = min(1.0, max(0.0, (0.90 - profit_factor) / 0.90))
        return round(max(exploration_floor, 0.75 - (0.40 * severity)), 4)
    if sample_count >= 30 and expectancy > 0 and profit_factor >= 1.20 and model_validated:
        return max_boost
    return 1.0


def strategy_economics(signal: Any) -> StrategyEconomics:
    strategy = strategy_identity(signal)
    ttl = max(15.0, _number(os.getenv("PAPER_STRATEGY_ECON_CACHE_SECONDS", "60"), 60.0))
    cached = _CACHE.get(strategy)
    now = time.monotonic()
    if cached and now - cached[0] <= ttl:
        return cached[1]

    records = _ledger_records(strategy)
    pnls = [_number(item.get("net_pnl")) for item in records]
    wins = [value for value in pnls if value > 0]
    losses = [value for value in pnls if value <= 0]
    gross_win = sum(wins)
    gross_loss = abs(sum(losses))
    pf = gross_win / gross_loss if gross_loss > 0 else (999.0 if gross_win > 0 else 0.0)
    validated = model_validation_ok(signal)
    expectancy = sum(pnls) / len(pnls) if pnls else 0.0
    multiplier = _multiplier(
        sample_count=len(records),
        expectancy=expectancy,
        profit_factor=pf,
        model_validated=validated,
    )
    result = StrategyEconomics(
        strategy=strategy,
        sample_count=len(records),
        net_pnl=sum(pnls),
        gross_pnl=sum(_number(item.get("gross_pnl")) for item in records),
        fees=sum(max(0.0, _number(item.get("fees"))) for item in records),
        win_rate=(len(wins) / len(pnls)) if pnls else 0.0,
        average_win=(sum(wins) / len(wins)) if wins else 0.0,
        average_loss=(sum(losses) / len(losses)) if losses else 0.0,
        profit_factor=pf,
        expectancy=expectancy,
        average_holding_minutes=(sum(_holding_minutes(item) for item in records) / len(records)) if records else 0.0,
        size_multiplier=multiplier,
        model_validated=validated,
    )
    _CACHE[strategy] = (now, result)
    return result


def expected_edge_pct(signal: Any) -> float | None:
    def value(name: str, default: Any = None) -> Any:
        if isinstance(signal, dict):
            return signal.get(name, default)
        return getattr(signal, name, default)

    for name in (
        "net_expected_value_pct",
        "expected_return_pct",
        "forecast_return_pct",
        "possible_move_pct",
        "expected_move_pct",
        "edge_pct",
    ):
        raw = value(name, None)
        if raw is None:
            continue
        parsed = _number(raw, float("nan"))
        if math.isfinite(parsed):
            return abs(parsed)
    return None


def estimated_round_trip_cost_pct(signal: Any) -> float:
    def value(name: str, default: Any = None) -> Any:
        if isinstance(signal, dict):
            return signal.get(name, default)
        return getattr(signal, name, default)

    explicit = value("estimated_cost_pct", None)
    if explicit is not None:
        return max(0.0, _number(explicit))
    slippage = max(0.0, _number(value("expected_slippage_pct", value("slippage_pct", 0.165)), 0.165))
    spread = max(0.0, _number(value("spread_pct", 0.0)))
    fee = max(0.0, _number(value("fee_pct", os.getenv("PAPER_ESTIMATED_FEE_PCT", "0.10")), 0.10))
    # Entry and exit each pay slippage/fees. Spread is counted once conservatively.
    return (2.0 * slippage) + (2.0 * fee) + spread


def fee_edge_allows_entry(signal: Any) -> tuple[bool, str, float | None, float]:
    """Reject only when explicit forecast edge cannot clear estimated costs.

    Missing edge evidence stays exploratory rather than being fabricated.
    """
    edge = expected_edge_pct(signal)
    cost = estimated_round_trip_cost_pct(signal)
    if edge is None:
        return True, "edge_unavailable_exploration", None, cost
    margin = max(1.0, _number(os.getenv("PAPER_MIN_EDGE_TO_COST_MULTIPLIER", "1.25"), 1.25))
    required = cost * margin
    if edge + 1e-12 < required:
        return False, f"edge_below_round_trip_cost:{edge:.4f}<{required:.4f}", edge, cost
    return True, "edge_clears_round_trip_cost", edge, cost


def adjusted_optimizer_target(signal: Any, target: float) -> tuple[float, StrategyEconomics, str]:
    """Apply performance sizing without turning paper exploration off."""
    target = max(0.0, _number(target))
    economics = strategy_economics(signal)
    if not active() or target <= 0:
        return target, economics, "inactive"
    adjusted = round(target * economics.size_multiplier, 2)
    reason = (
        "negative_expectancy_downsize"
        if economics.size_multiplier < 1.0
        else "validated_positive_expectancy_boost"
        if economics.size_multiplier > 1.0
        else "neutral_size"
    )
    return max(0.0, adjusted), economics, reason


def log_economics(economics: StrategyEconomics, *, symbol: str, original_target: float, adjusted_target: float, reason: str) -> None:
    log.info(
        "PAPER STRATEGY ECONOMICS | symbol=%s | strategy=%s | samples=%s | net_pnl=%.4f | fees=%.4f | "
        "win_rate=%.4f | profit_factor=%.4f | expectancy=%.6f | avg_hold_min=%.2f | model_validated=%s | "
        "size_multiplier=%.4f | original_target=%.2f | adjusted_target=%.2f | reason=%s | "
        "mode=paper | broker_submission=NONE | live_trading=DISARMED",
        str(symbol or "").upper(), economics.strategy, economics.sample_count, economics.net_pnl, economics.fees,
        economics.win_rate, economics.profit_factor, economics.expectancy, economics.average_holding_minutes,
        economics.model_validated, economics.size_multiplier, original_target, adjusted_target, reason,
    )


def install_paper_strategy_economics() -> bool:
    if not active():
        return False
    ensure_post_fix_epoch()
    log.info(
        "PAPER STRATEGY ECONOMICS | active=True | attribution=normalized_trade_ledger_with_trades_fallback | "
        "adaptive_sizing=ENABLED | fee_aware_edge=ENABLED | model_governance_boost_gate=ENABLED | "
        "epoch=%s | broker_submission=NONE | live_trading=DISARMED",
        _EPOCH_NAME,
    )
    return True
