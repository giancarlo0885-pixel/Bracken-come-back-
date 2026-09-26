from __future__ import annotations

import logging
from typing import Any, Callable


def _state(checks: dict[str, Any], name: str) -> str:
    item = checks.get(name) or {}
    if item.get("ok") is True:
        return "PASS"
    return str(item.get("status") or "FAIL")


def _paper_model_state(model: str, model_version: str) -> dict[str, Any]:
    """Best-effort paper-only model classification for sanitized observability.

    Failure to classify never changes capital readiness and never grants paper or
    broker authority. Real-capital approval remains owned by capital governance.
    """
    try:
        from paper_model_governance import paper_model_governance_assessment

        assessment = paper_model_governance_assessment(model, model_version)
        thresholds = dict(assessment.thresholds or {})
        return {
            "tier": str(assessment.tier or "RESEARCH"),
            "exploratory_eligible": bool(assessment.exploratory_eligible),
            "paper_qualified": bool(assessment.paper_qualified),
            "capital_qualified": bool(assessment.capital_qualified),
            "minimum_brier_skill": thresholds.get("exploratory_min_brier_skill"),
            "paper_qualified_min_brier_skill": thresholds.get("paper_qualified_min_brier_skill"),
            "capital_min_brier_skill": thresholds.get("capital_min_brier_skill"),
            "sample_count": int(assessment.sample_count or 0),
            "directional_accuracy": assessment.directional_accuracy,
            "expected_calibration_error": assessment.expected_calibration_error,
            "brier_skill_score": assessment.brier_skill_score,
            "temporal_leakage_ok": bool(assessment.temporal_leakage_ok),
            "recent_walk_forward_runs": int(assessment.recent_walk_forward_runs or 0),
            "distinct_symbols": int(assessment.distinct_symbols or 0),
        }
    except Exception:
        return {
            "tier": "UNAVAILABLE",
            "exploratory_eligible": False,
            "paper_qualified": False,
            "capital_qualified": False,
            "minimum_brier_skill": None,
            "paper_qualified_min_brier_skill": None,
            "capital_min_brier_skill": None,
            "sample_count": 0,
            "directional_accuracy": None,
            "expected_calibration_error": None,
            "brier_skill_score": None,
            "temporal_leakage_ok": False,
            "recent_walk_forward_runs": 0,
            "distinct_symbols": 0,
        }


def emit_capital_readiness_report(
    logger: logging.Logger,
    report_builder: Callable[[], dict[str, Any]] | None = None,
) -> dict[str, Any] | None:
    """Emit a sanitized, read-only production readiness summary.

    The report intentionally exposes only readiness states and aggregate evidence
    metrics. It never logs credentials, account identifiers, order payloads, or
    live-capital authorization values beyond the explicit fail-closed state.
    """
    if report_builder is None:
        from oracle_readiness import build_readiness_report

        report_builder = build_readiness_report

    try:
        report = report_builder()
    except Exception as exc:
        logger.warning(
            "CAPITAL READINESS | overall=UNAVAILABLE | reason=%s | capital_authorized=NO",
            exc.__class__.__name__,
        )
        return None

    checks = report.get("checks") or {}
    broker = checks.get("broker") or {}
    shadow = checks.get("shadow_forward") or {}
    models = checks.get("models") or {}
    data_integrity = checks.get("data_integrity") or {}
    paper = checks.get("paper_lifecycle") or {}

    logger.info(
        "CAPITAL READINESS | overall=%s | database=%s | safety=%s | accounting=%s | "
        "data_integrity=%s | models=%s | durable_order_journal=%s | reconciliation=%s | "
        "broker_connectivity=%s | broker_funding=%s | shadow_forward=%s | paper_lifecycle=%s | "
        "paper_entry=%s | paper_exit=%s | paper_buys=%s | paper_sells=%s | portfolio_reloads=%s | "
        "shadow_evaluated=%s/%s | shadow_p95_error_pct=%s | model_count=%s | "
        "capital_authorized=NO | human_authorization_required=true",
        str(report.get("overall_status") or "NOT_READY"),
        _state(checks, "database"),
        _state(checks, "safety"),
        _state(checks, "accounting"),
        _state(checks, "data_integrity"),
        _state(checks, "models"),
        _state(checks, "durable_order_journal"),
        _state(checks, "reconciliation"),
        "PASS" if broker.get("connectivity_ok") is True else str(broker.get("status") or "FAIL"),
        "PASS" if broker.get("funding_ok") is True else str(broker.get("buying_power_state") or broker.get("status") or "FAIL"),
        _state(checks, "shadow_forward"),
        _state(checks, "paper_lifecycle"),
        bool(paper.get("entry_proven")),
        bool(paper.get("exit_proven")),
        int(paper.get("buy_fills") or 0),
        int(paper.get("sell_fills") or 0),
        int(paper.get("portfolio_reload_events") or 0),
        int(shadow.get("evaluated_samples") or 0),
        int(shadow.get("minimum_samples") or 0),
        shadow.get("p95_paper_vs_broker_error_pct"),
        len(models.get("models") or []),
    )

    if data_integrity.get("ok") is not True:
        providers = data_integrity.get("providers") or {}
        quotes = data_integrity.get("quote_integrity") or {}
        news = data_integrity.get("news") or {}
        logger.info(
            "CAPITAL READINESS DATA | provider_status=%s | configured_providers=%s | healthy_providers=%s | "
            "quote_status=%s | quote_samples=%s | quote_confirmed=%s | quote_rejected=%s | "
            "unsafe_confirmed_divergent=%s | safely_rejected_divergent=%s | news_status=%s",
            str(providers.get("status") or "UNKNOWN"),
            int(providers.get("configured_providers") or 0),
            int(providers.get("healthy_providers") or 0),
            str(quotes.get("status") or "UNKNOWN"),
            int(quotes.get("sample_count") or 0),
            int(quotes.get("confirmed") or 0),
            int(quotes.get("rejected") or 0),
            int(quotes.get("unsafe_confirmed_divergent") or 0),
            int(quotes.get("safely_rejected_divergent") or 0),
            str(news.get("status") or "UNKNOWN"),
        )

    if models.get("ok") is not True:
        for item in list(models.get("models") or [])[:10]:
            governance = item.get("governance") or {}
            calibration = item.get("calibration") or {}
            model = str(item.get("model") or "unknown")
            version = str(item.get("model_version") or "")
            paper_model = _paper_model_state(model, version)
            logger.info(
                "CAPITAL READINESS MODEL | model=%s | version=%s | governance_status=%s | "
                "eligible_for_approval=%s | calibration_status=%s | calibration_samples=%s | "
                "ece=%s | brier_skill=%s | directional_accuracy=%s | walk_forward_ok=%s | "
                "temporal_leakage_ok=%s | paper_tier=%s | paper_exploratory=%s | "
                "paper_qualified=%s | paper_capital_qualified=%s | paper_samples=%s | "
                "paper_ece=%s | paper_brier_skill=%s | paper_directional_accuracy=%s | "
                "paper_temporal_leakage_ok=%s | paper_walk_forward_runs=%s | paper_distinct_symbols=%s | "
                "paper_min_brier=%s | paper_qualified_min_brier=%s | capital_min_brier=%s",
                model,
                version,
                str(governance.get("recommended_status") or "unknown"),
                bool(governance.get("eligible_for_approval")),
                str(calibration.get("status") or "unknown"),
                int(calibration.get("sample_count") or 0),
                calibration.get("expected_calibration_error"),
                calibration.get("brier_skill_score"),
                calibration.get("directional_accuracy"),
                bool(item.get("walk_forward_ok")),
                bool(item.get("temporal_leakage_ok")),
                paper_model.get("tier"),
                bool(paper_model.get("exploratory_eligible")),
                bool(paper_model.get("paper_qualified")),
                bool(paper_model.get("capital_qualified")),
                int(paper_model.get("sample_count") or 0),
                paper_model.get("expected_calibration_error"),
                paper_model.get("brier_skill_score"),
                paper_model.get("directional_accuracy"),
                bool(paper_model.get("temporal_leakage_ok")),
                int(paper_model.get("recent_walk_forward_runs") or 0),
                int(paper_model.get("distinct_symbols") or 0),
                paper_model.get("minimum_brier_skill"),
                paper_model.get("paper_qualified_min_brier_skill"),
                paper_model.get("capital_min_brier_skill"),
            )

    return report
