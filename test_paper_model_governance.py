from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

import paper_model_governance as paper_gov
from capital_model_governance import assess_model_evidence
from paper_model_governance import (
    PAPER_EXPLORATORY,
    PAPER_QUALIFIED,
    RESEARCH,
    classify_paper_model_metrics,
)


def _metrics(
    *,
    brier: float,
    samples: int = 1000,
    accuracy: float = 0.526,
    ece: float = 0.0188,
    closed_paper_trades: int = 30,
    paper_expectancy: float | None = 0.01,
    paper_profit_factor: float | None = 1.10,
):
    return {
        "sample_count": samples,
        "directional_accuracy": accuracy,
        "expected_calibration_error": ece,
        "brier_skill_score": brier,
        "closed_paper_trades": closed_paper_trades,
        "paper_expectancy": paper_expectancy,
        "paper_profit_factor": paper_profit_factor,
    }


def test_v43_like_negative_brier_is_paper_exploratory_only(monkeypatch):
    monkeypatch.setenv("PAPER_EXPLORATORY_MIN_BRIER_SKILL", "-0.01")
    monkeypatch.setenv("PAPER_QUALIFIED_MIN_BRIER_SKILL", "0.00")
    monkeypatch.setenv("PAPER_MODEL_MIN_VALIDATION_SAMPLES", "1000")
    monkeypatch.setenv("PAPER_MODEL_MIN_DIRECTIONAL_ACCURACY", "0.52")
    monkeypatch.setenv("PAPER_MODEL_MAX_ECE", "0.05")
    monkeypatch.setenv("PAPER_MODEL_MIN_SYMBOLS", "2")

    result = classify_paper_model_metrics(
        "crypto selective sign transition",
        "v43-selective-transition",
        _metrics(brier=-0.0094),
        temporal_leakage_ok=True,
        recent_walk_forward_runs=3,
        distinct_symbols=3,
    )

    assert result.tier == PAPER_EXPLORATORY
    assert result.exploratory_eligible is True
    assert result.paper_qualified is False
    assert result.capital_qualified is False


def test_nonnegative_brier_can_be_paper_qualified(monkeypatch):
    monkeypatch.setenv("PAPER_EXPLORATORY_MIN_BRIER_SKILL", "-0.01")
    monkeypatch.setenv("PAPER_QUALIFIED_MIN_BRIER_SKILL", "0.00")

    result = classify_paper_model_metrics(
        "m",
        "v",
        _metrics(brier=0.001),
        temporal_leakage_ok=True,
        recent_walk_forward_runs=3,
        distinct_symbols=3,
    )

    assert result.tier == PAPER_QUALIFIED
    assert result.paper_qualified is True
    assert result.capital_qualified is False


def test_paper_qualification_requires_realized_after_cost_economics(monkeypatch):
    monkeypatch.setenv("PAPER_EXPLORATORY_MIN_BRIER_SKILL", "-0.01")
    monkeypatch.setenv("PAPER_QUALIFIED_MIN_BRIER_SKILL", "0.00")
    monkeypatch.setenv("PAPER_QUALIFIED_MIN_CLOSED_TRADES", "30")
    monkeypatch.setenv("PAPER_QUALIFIED_MIN_EXPECTANCY", "0.0")
    monkeypatch.setenv("PAPER_QUALIFIED_MIN_PROFIT_FACTOR", "1.0")

    result = classify_paper_model_metrics(
        "m",
        "v",
        _metrics(
            brier=0.05,
            closed_paper_trades=0,
            paper_expectancy=None,
            paper_profit_factor=None,
        ),
        temporal_leakage_ok=True,
        recent_walk_forward_runs=3,
        distinct_symbols=3,
    )

    assert result.tier == PAPER_EXPLORATORY
    assert result.exploratory_eligible is True
    assert result.paper_qualified is False
    assert result.closed_paper_trades == 0
    assert result.paper_expectancy is None
    assert result.paper_profit_factor is None
    assert any("closed paper trades" in reason for reason in result.reasons)


def test_paper_trade_economics_uses_realized_net_pnl(monkeypatch):
    monkeypatch.setattr(
        paper_gov,
        "rows",
        lambda sql, params=None: [
            {"net_pnl": 2.0},
            {"net_pnl": 1.0},
            {"net_pnl": -1.5},
            {"net_pnl": -0.5},
        ],
    )

    result = paper_gov._paper_trade_economics("m", "v")

    assert result["closed_paper_trades"] == 4
    assert result["paper_expectancy"] == 0.25
    assert result["paper_profit_factor"] == 1.5


def test_brier_below_exploratory_floor_remains_research(monkeypatch):
    monkeypatch.setenv("PAPER_EXPLORATORY_MIN_BRIER_SKILL", "-0.01")

    result = classify_paper_model_metrics(
        "m",
        "v",
        _metrics(brier=-0.011),
        temporal_leakage_ok=True,
        recent_walk_forward_runs=3,
        distinct_symbols=3,
    )

    assert result.tier == RESEARCH
    assert result.exploratory_eligible is False


def test_paper_tier_fails_closed_on_leakage_or_insufficient_evidence(monkeypatch):
    monkeypatch.setenv("PAPER_EXPLORATORY_MIN_BRIER_SKILL", "-0.01")

    leaked = classify_paper_model_metrics(
        "m",
        "v",
        _metrics(brier=0.10),
        temporal_leakage_ok=False,
        recent_walk_forward_runs=3,
        distinct_symbols=3,
    )
    insufficient = classify_paper_model_metrics(
        "m",
        "v",
        _metrics(brier=0.10, samples=999),
        temporal_leakage_ok=True,
        recent_walk_forward_runs=3,
        distinct_symbols=3,
    )

    assert leaked.tier == RESEARCH
    assert insufficient.tier == RESEARCH


def test_paper_thresholds_cannot_lower_real_capital_brier_gate(monkeypatch):
    monkeypatch.setenv("PAPER_EXPLORATORY_MIN_BRIER_SKILL", "-1.0")
    monkeypatch.setenv("PAPER_QUALIFIED_MIN_BRIER_SKILL", "-1.0")
    monkeypatch.setenv("CAPITAL_MIN_MODEL_PASS_RUNS", "2")
    monkeypatch.setenv("CAPITAL_MIN_MODEL_SYMBOLS", "2")
    monkeypatch.setenv("CAPITAL_MIN_BRIER_SKILL", "0.02")
    monkeypatch.setenv("CAPITAL_MAX_ECE", "0.12")
    monkeypatch.setenv("CAPITAL_MIN_DIRECTIONAL_ACCURACY", "0.52")

    now = datetime.now(timezone.utc).isoformat()
    metrics = {
        "brier_skill_score": 0.0,
        "expected_calibration_error": 0.01,
        "directional_accuracy": 0.60,
        "beats_all_baselines": True,
    }
    leakage = {"strict_ordering": True, "future_mutation_probe": {"ok": True}}
    evidence = [
        {
            "run_id": symbol,
            "model": "m",
            "model_version": "v",
            "symbol": symbol,
            "status": "PASS",
            "metrics": metrics,
            "leakage_checks": leakage,
            "created_at": now,
        }
        for symbol in ("AAA", "BBB")
    ]

    capital = assess_model_evidence("m", "v", evidence, current_status="shadow")

    assert capital.eligible_for_approval is False
    assert capital.recommended_status == "shadow"
    assert capital.evidence["minimum_brier_skill"] == 0.02



def test_paper_assessment_uses_same_current_evidence_windows_as_readiness(monkeypatch):
    queries: list[str] = []

    def fake_rows(sql, params=None):
        queries.append(" ".join(str(sql).split()))
        return []

    expected = classify_paper_model_metrics(
        "m",
        "v",
        _metrics(brier=-0.009),
        temporal_leakage_ok=True,
        recent_walk_forward_runs=3,
        distinct_symbols=3,
    )
    monkeypatch.setattr(paper_gov, "rows", fake_rows)
    monkeypatch.setattr(paper_gov, "assess_paper_model_evidence", lambda *args, **kwargs: expected)
    monkeypatch.setattr(
        paper_gov,
        "model_governance_assessment",
        lambda *args, **kwargs: SimpleNamespace(eligible_for_approval=False),
    )

    result = paper_gov.paper_model_governance_assessment("m", "v")

    assert result.tier == PAPER_EXPLORATORY
    calibration_sql = next(sql for sql in queries if "FROM forecast_validation" in sql)
    walk_forward_sql = next(sql for sql in queries if "FROM walk_forward_validation_runs" in sql)
    assert "ORDER BY id DESC" in calibration_sql
    assert "LIMIT 1000" in calibration_sql
    assert "ORDER BY created_at DESC" in walk_forward_sql
    assert "LIMIT 20" in walk_forward_sql
