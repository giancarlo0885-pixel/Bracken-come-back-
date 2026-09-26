from __future__ import annotations

import logging

from readiness_observability import emit_capital_readiness_report


def _report() -> dict:
    return {
        "overall_status": "SHADOW_READY",
        "checks": {
            "database": {"ok": True},
            "safety": {"ok": True},
            "accounting": {"ok": True},
            "data_integrity": {"ok": True},
            "models": {
                "ok": False,
                "models": [
                    {
                        "model": "oracle-model",
                        "model_version": "v1",
                        "governance": {
                            "eligible_for_approval": False,
                            "recommended_status": "shadow",
                        },
                        "calibration": {
                            "status": "INSUFFICIENT_OR_FAILED_EVIDENCE",
                            "sample_count": 12,
                            "expected_calibration_error": 0.2,
                            "brier_skill_score": -0.1,
                            "directional_accuracy": 0.5,
                        },
                        "walk_forward_ok": False,
                        "temporal_leakage_ok": False,
                    }
                ],
            },
            "durable_order_journal": {"ok": True},
            "reconciliation": {"ok": True},
            "broker": {
                "ok": True,
                "connectivity_ok": True,
                "funding_ok": True,
                "status": "PASS",
                "buying_power_state": "POSITIVE",
            },
            "shadow_forward": {
                "ok": False,
                "status": "INSUFFICIENT_FORWARD_EVIDENCE",
                "evaluated_samples": 7,
                "minimum_samples": 100,
                "p95_paper_vs_broker_error_pct": 0.4,
            },
        },
    }


def test_emit_capital_readiness_report_logs_sanitized_gate_evidence(caplog, monkeypatch):
    monkeypatch.setattr(
        "readiness_observability._paper_model_state",
        lambda model, version: {
            "tier": "PAPER_EXPLORATORY",
            "exploratory_eligible": True,
            "paper_qualified": False,
            "capital_qualified": False,
            "minimum_brier_skill": -0.01,
            "paper_qualified_min_brier_skill": 0.0,
            "capital_min_brier_skill": 0.02,
            "sample_count": 1000,
            "directional_accuracy": 0.529,
            "expected_calibration_error": 0.0225,
            "brier_skill_score": -0.0088,
            "temporal_leakage_ok": True,
            "recent_walk_forward_runs": 3,
            "distinct_symbols": 3,
        },
    )
    logger = logging.getLogger("test-capital-readiness")
    with caplog.at_level(logging.INFO, logger=logger.name):
        returned = emit_capital_readiness_report(logger, report_builder=_report)

    assert returned == _report()
    text = caplog.text
    assert "CAPITAL READINESS | overall=SHADOW_READY" in text
    assert "database=PASS" in text
    assert "broker_connectivity=PASS" in text
    assert "broker_funding=PASS" in text
    assert "shadow_forward=INSUFFICIENT_FORWARD_EVIDENCE" in text
    assert "shadow_evaluated=7/100" in text
    assert "capital_authorized=NO" in text
    assert "CAPITAL READINESS MODEL | model=oracle-model" in text
    assert "calibration_samples=12" in text
    assert "paper_tier=PAPER_EXPLORATORY" in text
    assert "paper_exploratory=True" in text
    assert "paper_qualified=False" in text
    assert "paper_capital_qualified=False" in text
    assert "paper_samples=1000" in text
    assert "paper_brier_skill=-0.0088" in text
    assert "paper_walk_forward_runs=3" in text
    assert "paper_distinct_symbols=3" in text
    assert "paper_min_brier=-0.01" in text
    assert "paper_qualified_min_brier=0.0" in text
    assert "capital_min_brier=0.02" in text


def test_emit_capital_readiness_report_fails_observably_without_raising(caplog):
    logger = logging.getLogger("test-capital-readiness-error")

    def broken_report():
        raise RuntimeError("database unavailable")

    with caplog.at_level(logging.WARNING, logger=logger.name):
        returned = emit_capital_readiness_report(logger, report_builder=broken_report)

    assert returned is None
    assert "CAPITAL READINESS | overall=UNAVAILABLE | reason=RuntimeError" in caplog.text
    assert "capital_authorized=NO" in caplog.text
