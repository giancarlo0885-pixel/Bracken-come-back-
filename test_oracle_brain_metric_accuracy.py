from pathlib import Path

import oracle_brain


def test_brain_does_not_present_counterfactuals_as_executed_profit():
    source = Path("pages/3_Oracle_Brain.py").read_text(encoding="utf-8")
    assert '"Counterfactual missed opportunities"' in source
    assert "counterfactual_missed_opportunities" in source
    assert "simulated research evidence, not executed trades or verified post-cost profit" in source
    assert 'metric("Missed winners"' not in source


def test_brain_regime_economics_use_complete_canonical_round_trip_costs():
    queries = []
    oracle_brain.build_oracle_brain_snapshot(lambda query, params=(): queries.append(query) or [])
    regime = next(query for query in queries if "FROM paper_regime_trade_metrics" in query)
    assert "SUM(round_trip_net_pnl) AS net_pnl" in regime
    assert "SUM(round_trip_fees) AS fees" in regime
    assert "AVG(round_trip_net_pnl) AS expectancy" in regime
    assert "cost_provenance='exact_lot'" in regime
    assert "entry_time <= exit_time AND exit_time <= NOW()" in regime
    episode_reads = [query for query in queries if "FROM oracle_brain_episodes" in query and "WHERE provenance_status" in query]
    assert episode_reads
    assert any("outcome_snapshot->>'accounting_version'='exact_round_trip_v1'" in query for query in episode_reads)


def test_brain_does_not_attribute_legacy_exit_only_profits_as_full_cost_outcomes():
    def fetch(query, params=()):
        if "SELECT episode_key" in query:
            return [{"episode_key": "legacy", "provenance_status": "exact", "net_pnl": 100,
                     "fees": 0, "return_pct": 1, "feature_snapshot": {"expected_edge_pct": 1},
                     "outcome_snapshot": {}}]
        return []

    snapshot = oracle_brain.build_oracle_brain_snapshot(fetch)
    assert snapshot["episodes"] == snapshot["outcome_attribution"] == []
    assert snapshot["summary"]["exact_episodes"] == 0


def test_current_validation_display_does_not_use_unversioned_legacy_rollups():
    queries = []

    def fetch(query, params=()):
        queries.append(query)
        assert "oracle_setup_validation" not in query
        assert "oracle_calibration_buckets" not in query
        assert "oracle_validation_weaknesses" not in query
        if "GROUP BY market,strategy,regime" in query:
            return [{"samples": 30, "expectancy": -0.01}]
        return []

    snapshot = oracle_brain.build_learning_validation_snapshot(fetch, market_scope="crypto")
    assert snapshot["setup_cohorts"] == snapshot["negative_cohorts"] == 1
    assert snapshot["calibration_error"] is None
    assert snapshot["calibration_samples"] == 0
    assert all("market IN ('crypto','US_CRYPTO','us_crypto')" in query for query in queries)
    assert all("outcome_snapshot->>'accounting_version'='exact_round_trip_v1'" in query for query in queries)
    assert all("outcome_snapshot->>'cost_provenance'='exact_lot'" in query for query in queries)


def test_brain_page_shows_only_versioned_challengers_and_current_source_metrics():
    page = Path("pages/3_Oracle_Brain.py").read_text(encoding="utf-8")
    assert "build_learning_validation_snapshot(_brain_rows, market_scope=market_scope)" in page
    assert page.count("evidence->>'accounting_version'='exact_round_trip_v1'") == 2
    assert page.count("evidence->>'cost_provenance'='exact_lot'") == 2
    for table in ("oracle_setup_validation", "oracle_calibration_buckets", "oracle_validation_weaknesses"):
        assert f"FROM {table}" not in page
    assert "legacy rollups remain observational and unavailable for profitability claims" in page
