from __future__ import annotations

from pathlib import Path

import oracle_brain_learning_v3 as v3


def test_feature_schema_hash_is_key_based_and_value_hash_is_value_sensitive():
    a = {"momentum_20d": 0.10, "volume_ratio": 1.4}
    b = {"volume_ratio": 9.9, "momentum_20d": -0.20}
    assert v3.feature_schema_hash(a) == v3.feature_schema_hash(b)
    assert v3.feature_value_hash(a) != v3.feature_value_hash(b)


def test_bootstrap_interval_is_deterministic_and_sign_sensitive():
    values = [1.2, 0.8, 1.0, 1.5, 0.6, 1.1] * 8
    first = v3.deterministic_bootstrap_interval(values, seed_text="cohort-a")
    second = v3.deterministic_bootstrap_interval(values, seed_text="cohort-a")
    assert first == second
    assert first[0] is not None and first[0] > 0
    assert first[1] is not None and first[1] > first[0]


def test_statistical_summary_requires_uncertainty_to_clear_zero():
    strong = v3.statistical_summary([1.0, 1.2, 0.8, 1.4, 0.7] * 8, seed_text="strong")
    mixed = v3.statistical_summary([1.0, -1.0, 0.8, -0.8, 0.2, -0.2] * 8, seed_text="mixed")
    assert strong["robust_polarity"] == "positive"
    assert strong["expectancy_ci_low"] > 0
    assert mixed["robust_polarity"] == "uncertain"
    assert mixed["expectancy_ci_low"] <= 0 <= mixed["expectancy_ci_high"]


def test_wilson_interval_is_bounded():
    low, high = v3.wilson_interval(7, 10)
    assert 0 <= low <= high <= 1
    assert low < 0.7 < high


def test_title_clustering_recognizes_same_event_without_exact_text_match():
    a = v3._normalize_title_tokens("Federal Reserve cuts rates after September meeting")
    b = v3._normalize_title_tokens("September Federal Reserve meeting: rates cut")
    c = v3._normalize_title_tokens("Bitcoin protocol developer releases wallet update")
    assert v3._jaccard(a, b) > v3._jaccard(a, c)
    assert v3._jaccard(a, b) >= 0.5


def test_v3_schema_is_research_only_and_has_no_execution_path():
    migration = Path("migrations/20260920_oracle_brain_learning_v3.sql").read_text(encoding="utf-8")
    source = Path("oracle_brain_learning_v3.py").read_text(encoding="utf-8")
    worker = Path("market_worker.py").read_text(encoding="utf-8")
    learner = Path("oracle_brain_learning.py").read_text(encoding="utf-8")

    for table in (
        "oracle_brain_source_clusters",
        "oracle_brain_provider_reputation",
        "oracle_brain_counterfactuals",
        "oracle_brain_drift_events",
        "oracle_brain_working_memory",
        "oracle_brain_experiments",
        "oracle_brain_learning_runs",
    ):
        assert table in migration
    assert migration.count("CHECK (execution_impact = 'NONE')") >= 7

    forbidden = (
        "submit_order(",
        "process_signals(",
        "LIVE_TRADING_ARMED=true",
        "ENABLE_BROKER_SUBMISSION=true",
        "live_order_proposals",
        "live_order_approvals",
    )
    for token in forbidden:
        assert token not in source

    brain_submit = worker.index(
        "brain_learning_future = brain_learning_executor.submit(_run_brain_learning_sync, market)"
    )
    market_submit = worker.index("deep_future = deep_executor.submit(scan_market, market)")
    assert brain_submit < market_submit
    assert 'ORACLE_BRAIN_EPISODE_BATCH", "75"' in learner
    assert "last_episode_exit_at" in learner
    assert "curated_crypto_history" in learner


def test_v3_counterfactual_and_experiment_language_stays_research_only():
    source = Path("oracle_brain_learning_v3.py").read_text(encoding="utf-8")
    page = Path("pages/3_Oracle_Brain.py").read_text(encoding="utf-8")
    assert "missed_winner" in source
    assert "avoided_loss" in source
    assert "ready_for_review" in source
    assert "execution_impact" in source
    assert "Rejected/watched candidates are evaluated only as counterfactual research" in page
    assert "experiments cannot self-promote into trading authority" in page
