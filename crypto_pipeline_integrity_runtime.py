from __future__ import annotations

from datetime import datetime, timezone
import logging
import os
from typing import Any


def _truthy(name: str, default: str = "false") -> bool:
    return str(os.getenv(name, default) or default).strip().lower() == "true"


def _paper_learning_active() -> bool:
    return (
        str(os.getenv("EXECUTION_MODE", "paper") or "paper").strip().lower() == "paper"
        and _truthy("PAPER_AUTONOMOUS_LEARNING")
        and not _truthy("ENABLE_BROKER_SUBMISSION")
        and not _truthy("LIVE_TRADING_ARMED")
    )


def _finite(value: Any, default: float = 0.0) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return number if number == number else default


def _paper_account_limits_state(worker: Any, portfolio: dict[str, Any]) -> dict[str, Any]:
    """Read the same daily paper-learning cadence that execution enforces."""
    from paper_crypto_learning_relaxation import _paper_limits

    max_turnover, max_entries, _ = _paper_limits()
    cutoff = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    equity = max(
        0.01,
        _finite(portfolio.get("equity") or portfolio.get("total_equity") or portfolio.get("cash"), 0.01),
    )
    with worker.connect() as conn:
        record = conn.execute(
            """
            SELECT
                COUNT(*) FILTER (WHERE side='BUY') AS entries,
                COALESCE(SUM(ABS(value)), 0) AS turnover_value
            FROM trades
            WHERE market='crypto'
              AND created_at >= %s
            """,
            (cutoff,),
        ).fetchone() or {}
    entries = max(0, int(_finite(record.get("entries"), 0.0)))
    turnover_value = max(0.0, _finite(record.get("turnover_value"), 0.0))
    turnover_pct = turnover_value / equity
    reasons: list[str] = []
    if turnover_pct > max_turnover:
        reasons.append(f"daily_turnover:{turnover_pct:.4f}>{max_turnover:.4f}")
    if entries >= max_entries:
        reasons.append(f"daily_entries:{entries}>={max_entries}")
    return {
        "blocked": bool(reasons),
        "reasons": reasons,
        "entries": entries,
        "max_entries": max_entries,
        "turnover_pct": turnover_pct,
        "max_turnover_pct": max_turnover,
    }


def _install_optimizer_account_limit_sync(worker: Any) -> None:
    original = worker.adaptive_portfolio_optimizer
    if getattr(original, "_oracle_account_limit_synced", False):
        return

    def synced_optimizer(
        opportunities: list[dict[str, Any]],
        portfolio: dict[str, Any],
        positions: list[dict[str, Any]],
        *,
        engine: str,
    ) -> dict[str, Any]:
        plan = original(opportunities, portfolio, positions, engine=engine)
        if str(engine or "").strip().lower() != "crypto":
            return plan

        # Make strategic overrides explicit: only tactical authorization reasons
        # may be waived. Hard-risk, execution, liquidity and account limits remain
        # authoritative.
        from strategic_rebalance_optimizer_bridge import _strategic_rebalance_gate

        by_symbol = {
            str(item.get("symbol") or "").upper().strip(): item
            for item in opportunities or []
            if str(item.get("symbol") or "").strip()
        }
        for allocation in plan.get("allocations") or []:
            symbol = str(allocation.get("symbol") or "").upper().strip()
            item = by_symbol.get(symbol) or {}
            gate = _strategic_rebalance_gate(item) if item else {}
            tactical_reasons = list(gate.get("tactical_authorization_reasons") or [])
            allocation["strategic_override_scope"] = "tactical_authorization_only"
            allocation["waived_tactical_reasons"] = tactical_reasons
            allocation["hard_risk_waivers"] = []

        if not _paper_learning_active():
            return plan

        try:
            limits = _paper_account_limits_state(worker, portfolio)
        except Exception as exc:
            # Fail closed only for proposed paper entries. Existing positions and
            # risk exits are not touched by this optimizer wrapper.
            limits = {
                "blocked": True,
                "reasons": [f"account_limit_state_unavailable:{exc.__class__.__name__}"],
                "entries": None,
                "max_entries": None,
                "turnover_pct": None,
                "max_turnover_pct": None,
            }

        plan["paper_account_limits"] = limits
        if not limits.get("blocked"):
            return plan

        removed = list(plan.get("allocations") or [])
        if removed:
            rejections = list(plan.get("rejections") or [])
            for allocation in removed:
                rejections.append(
                    {
                        "symbol": allocation.get("symbol"),
                        "reason": "paper_account_limit",
                        "risk_reasons": list(limits.get("reasons") or []),
                        "account_limit_state": limits,
                    }
                )
            plan["rejections"] = rejections
            plan["allocations"] = []
            worker.log.info(
                "CRYPTO_OPTIMIZER_ACCOUNT_LIMIT | status=BLOCKED_BEFORE_PROMOTION | entries=%s/%s | "
                "turnover=%.2f%%/%.2f%% | symbols=%s | broker_submission=NONE | live_trading=DISARMED",
                limits.get("entries"),
                limits.get("max_entries"),
                _finite(limits.get("turnover_pct")) * 100.0,
                _finite(limits.get("max_turnover_pct")) * 100.0,
                ",".join(str(item.get("symbol") or "") for item in removed),
            )
        return plan

    synced_optimizer._oracle_account_limit_synced = True
    worker.adaptive_portfolio_optimizer = synced_optimizer


def _install_v39_evidence_normalization(worker: Any) -> None:
    original = worker._v39_signal_opportunity
    if getattr(original, "_oracle_evidence_normalized", False):
        return

    def normalized_opportunity(
        market: str,
        signal: Any,
        prices: dict[str, Any],
        ranked_by_symbol: dict[str, dict[str, Any]],
        scan_type: str,
    ) -> dict[str, Any]:
        item = dict(original(market, signal, prices, ranked_by_symbol, scan_type) or {})
        symbol = str(item.get("symbol") or getattr(signal, "symbol", "") or "").upper().strip()
        requested = str(item.get("requested_symbol") or "").upper().strip()
        provider_symbol = str(item.get("provider_symbol") or "").upper().strip()
        risk_score = item.get("risk_score")
        try:
            risk_known = risk_score is not None and float(risk_score) == float(risk_score)
        except (TypeError, ValueError):
            risk_known = False
        quote = dict((prices or {}).get(symbol) or {})
        item["risk_known"] = bool(risk_known)
        item["identity_verified"] = bool(symbol and requested == symbol and provider_symbol == symbol)
        try:
            item["execution_fresh"] = bool(
                worker._execution_quote_eligible(
                    {
                        **quote,
                        "symbol": symbol,
                        "market": market,
                        "asset_class": "crypto" if str(market).lower() == "crypto" else "stock",
                    }
                )
            )
        except Exception:
            item["execution_fresh"] = False
        return item

    normalized_opportunity._oracle_evidence_normalized = True
    worker._v39_signal_opportunity = normalized_opportunity


def _install_universe_filter_diagnostics(worker: Any) -> None:
    original = worker._active_quarantined_symbols
    if getattr(original, "_oracle_universe_filter_observed", False):
        return
    last_signature: tuple[str, ...] | None = None

    def observed_quarantine() -> set[str]:
        nonlocal last_signature
        symbols = {
            str(symbol or "").upper().strip()
            for symbol in (original() or set())
            if str(symbol or "").strip()
        }
        signature = tuple(sorted(symbols))
        if signature != last_signature:
            last_signature = signature
            configured = len(getattr(worker, "WATCHLISTS", {}).get("crypto", {}) or {})
            worker.log.info(
                "CRYPTO_UNIVERSE_FILTER | configured=%d | active_quarantine=%d | expected_after_quarantine=%d | symbols=%s",
                configured,
                len(symbols),
                max(0, configured - len(symbols)),
                ",".join(signature[:20]) or "none",
            )
        return symbols

    observed_quarantine._oracle_universe_filter_observed = True
    worker._active_quarantined_symbols = observed_quarantine


class _ReferenceHandoffLabelFilter(logging.Filter):
    """Relabel paper-reference logs without touching quote eligibility."""

    def filter(self, record: logging.LogRecord) -> bool:
        if not isinstance(record.msg, str) or not record.msg.startswith("EXECUTION_QUOTE_HANDOFF |"):
            return True
        args = record.args if isinstance(record.args, tuple) else ()
        # runtime_integrity_patch handoff args: quote_eligible index 8,
        # provider_verified index 9, paper_reference_verified index 10,
        # verification_kind index 11.
        if len(args) > 11 and args[10] is True and args[9] is not True:
            record.msg = record.msg.replace("EXECUTION_QUOTE_HANDOFF |", "PAPER_REFERENCE_HANDOFF |", 1)
        return True


def _install_reference_handoff_label(worker: Any) -> None:
    if getattr(worker, "_oracle_reference_handoff_label_installed", False):
        return
    worker.log.addFilter(_ReferenceHandoffLabelFilter())
    worker._oracle_reference_handoff_label_installed = True


def install_crypto_pipeline_integrity_runtime(worker: Any) -> bool:
    """Align crypto planning, evidence, universe and quote observability.

    This module does not enable broker submission or change protective exits. The
    only behavioral change is paper-only: optimizer allocations are suppressed
    before promotion when the same autonomous-learning account limits enforced by
    execution are already exceeded.
    """
    if getattr(worker, "_crypto_pipeline_integrity_runtime_installed", False):
        return False
    _install_v39_evidence_normalization(worker)
    _install_optimizer_account_limit_sync(worker)
    _install_universe_filter_diagnostics(worker)
    _install_reference_handoff_label(worker)
    worker._crypto_pipeline_integrity_runtime_installed = True
    worker.log.info(
        "CRYPTO_PIPELINE_INTEGRITY | account_limit_sync=ON | v39_evidence=EXPLICIT | "
        "universe_filter_diagnostics=ON | paper_reference_label=ON | live_trading=UNCHANGED"
    )
    return True
