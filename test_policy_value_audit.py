"""Mathematical and evidence-boundary tests with explicitly synthetic events."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import itertools
import json
from pathlib import Path
import subprocess
import sys

import pytest

from policy_value_audit import SCHEMA_VERSION, audit_policy_value


START = datetime(2026, 10, 9, 12, tzinfo=timezone.utc)
AS_OF = (START + timedelta(days=1)).isoformat()


def event(identity, return_bps, policy="LONG", baseline="FLAT", *, cost=0, pool="range"):
    quote_time = START + timedelta(seconds=1)
    quote = dict(market="crypto", symbol="TEST-USD", venue="synthetic", verified=True,
                 observed_at=quote_time.isoformat(), received_at=quote_time.isoformat(),
                 source_ref=f"synthetic:{identity}:start", bid=100, ask=100)
    end_time = quote_time + timedelta(minutes=5)
    price = 100 * (1 + return_bps / 10_000)
    return dict(event_id=identity, market="crypto", symbol="TEST-USD", venue="synthetic",
                baseline_action=baseline, policy_action=policy, pool_id=pool,
                episode_id="same-dependent-episode", decision_ref=f"synthetic:{identity}:decision",
                decision_at=START.isoformat(), information_at=START.isoformat(),
                pool_assigned_at=START.isoformat(), start_quote=quote,
                end_quote={**quote, "observed_at": end_time.isoformat(),
                           "received_at": end_time.isoformat(), "source_ref": f"synthetic:{identity}:end",
                           "bid": price, "ask": price},
                costs=dict(estimated_at=START.isoformat(), model_ref="synthetic-cost-v1",
                           fees_bps=cost, spread_bps=0, slippage_bps=0, impact_bps=0))


def payload(*rows, expected=None):
    return dict(schema_version=SCHEMA_VERSION,
                specification=dict(universe_ref="synthetic-full-event-manifest-v1",
                                   baseline_version="synthetic-baseline-v1", policy_version="synthetic-policy-v1",
                                   pool_definition="symbol, venue and predecision regime",
                                   specification_at=(START - timedelta(days=1)).isoformat(),
                                   horizon_seconds=300,
                                   expected_event_count=len(rows) if expected is None else expected),
                events=list(rows))


def audit(*rows):
    return audit_policy_value(payload(*rows), as_of=AS_OF)


def test_positive_market_exposure_is_not_selection_skill():
    result = audit(event("a", 100), event("b", 100, "FLAT"))
    assert result["policy_net_bps"] == pytest.approx(50)
    assert result["deployment_value_bps"] == pytest.approx(50)
    assert result["composition_benchmark_bps"] == pytest.approx(50)
    assert result["selection_value_bps"] == pytest.approx(0)
    assert result["audited_episodes"] == 1
    assert result["p_value"] is result["confidence_interval"] is None
    assert result["promotion_evidence_ready"] is False
    assert result["execution_impact"] == "NONE"


def test_positive_gross_return_can_have_negative_postcost_selection_value():
    result = audit(event("a", 20, cost=10), event("b", 100, "FLAT", cost=10))
    assert result["policy_net_bps"] == pytest.approx(5)
    assert result["composition_benchmark_bps"] == pytest.approx(25)
    assert result["selection_value_bps"] == pytest.approx(-20)


def test_higher_cost_on_selected_event_reverses_gross_selection_advantage():
    result = audit(event("a", 100, cost=120), event("b", 50, "FLAT"))
    assert result["deployment_value_bps"] == pytest.approx(-10)
    assert result["composition_benchmark_bps"] == pytest.approx(7.5)
    assert result["selection_value_bps"] == pytest.approx(-17.5)


def test_analytic_benchmark_matches_exhaustive_reassignment():
    payoffs = [10, 20, -30, 80]
    rows = [event(str(i), value, "LONG" if i < 2 else "FLAT") for i, value in enumerate(payoffs)]
    all_assignments = [sum(payoffs[i] for i in chosen) / 4
                       for chosen in itertools.combinations(range(4), 2)]
    result = audit(*rows)
    assert result["composition_benchmark_bps"] == pytest.approx(sum(all_assignments) / len(all_assignments))
    assert result["deployment_value_bps"] == pytest.approx(7.5)


def test_baseline_long_and_unequal_pools_use_event_weighting():
    # Flat baseline pool: deployment=15, composition=10 over two events.
    # Long baseline pool: deployment=20, composition=5 over four events.
    result = audit(event("a", 30), event("b", 10, "FLAT"),
                   event("c", -80, "FLAT", "LONG"), event("d", 0, "LONG", "LONG"),
                   event("e", 0, "LONG", "LONG"), event("f", 0, "LONG", "LONG"))
    assert result["pool_count"] == 2
    assert result["deployment_value_bps"] == pytest.approx(110 / 6)
    assert result["composition_benchmark_bps"] == pytest.approx(40 / 6)
    assert result["selection_value_bps"] == pytest.approx(70 / 6)


@pytest.mark.parametrize("path,value,reason", [
    (("information_at",), (START + timedelta(seconds=1)).isoformat(), "postdecision_information_or_pool"),
    (("pool_assigned_at",), (START + timedelta(seconds=1)).isoformat(), "postdecision_information_or_pool"),
    (("decision_at",), START.replace(tzinfo=None).isoformat(), "invalid_decision_time"),
    (("start_quote", "observed_at"), START.isoformat(), "nonforward_outcome"),
    (("start_quote", "verified"), "true", "unverified_quote"),
    (("start_quote", "bid"), 101, "crossed_or_nonpositive_quote"),
    (("start_quote", "bid"), True, "invalid_quote_price"),
    (("start_quote", "bid"), float("nan"), "invalid_quote_price"),
    (("end_quote", "ask"), float("inf"), "invalid_quote_price"),
    (("end_quote", "symbol"), "OTHER-USD", "quote_identity_mismatch"),
    (("end_quote", "venue"), "another-venue", "quote_identity_mismatch"),
    (("end_quote", "source_ref"), "", "missing_quote_source"),
    (("end_quote", "received_at"), START.isoformat(), "invalid_quote_chronology"),
    (("end_quote", "received_at"), (START + timedelta(days=2)).isoformat(), "invalid_quote_chronology"),
    (("costs", "fees_bps"), None, "missing_or_invalid_cost"),
    (("costs", "fees_bps"), -1, "negative_cost"),
    (("costs", "impact_bps"), True, "missing_or_invalid_cost"),
    (("costs", "model_ref"), "", "missing_cost_model"),
    (("costs", "estimated_at"), (START + timedelta(seconds=1)).isoformat(), "postdecision_cost_model"),
    (("policy_action",), "HOLD", "invalid_action"),
    (("policy_action",), ["LONG"], "invalid_action"),
    (("market",), ["crypto"], "invalid_market"),
    (("episode_id",), "", "missing_episode_id"),
    (("decision_ref",), "", "missing_decision_ref"),
])
def test_incomplete_or_mistimed_evidence_is_excluded(path, value, reason):
    bad = event("bad", 10)
    target = bad
    for key in path[:-1]:
        target = target[key]
    target[path[-1]] = value
    result = audit(bad, event("good-1", 100), event("good-2", 100, "FLAT"))
    assert result["exclusions"] == {reason: 1}
    assert result["audited_events"] == 2
    assert result["coverage_fraction"] == pytest.approx(2 / 3)


def test_incomplete_horizon_and_overflow_never_produce_returns():
    bad = event("bad", 10)
    bad["end_quote"]["observed_at"] = (START + timedelta(seconds=300)).isoformat()
    assert audit(bad)["exclusions"]["horizon_mismatch"] == 1
    huge = event("huge", 1)
    huge["start_quote"].update(bid=1e-300, ask=1e-300)
    huge["end_quote"].update(bid=1e300, ask=1e300)
    result = audit(huge)
    assert result["exclusions"]["nonfinite_payoff"] == 1
    assert result["selection_value_bps"] is None
    assert result["status"] == "insufficient_evidence"


def test_duplicates_do_not_inflate_evidence_and_conflicts_exclude_both_versions():
    a, b = event("a", 100), event("b", 50, "FLAT")
    result = audit_policy_value(payload(a, b, deepcopy(a), expected=2), as_of=AS_OF)
    assert result["duplicate_rows"] == 1
    assert result["audited_events"] == 2
    conflicting = {**a, "policy_action": "FLAT"}
    result = audit_policy_value(payload(a, b, conflicting, expected=2), as_of=AS_OF)
    assert result["exclusions"]["conflicting_event_identity"] == 1
    assert result["audited_events"] == 0


def test_market_symbol_venue_and_baseline_pools_cannot_be_silently_mixed():
    for field, other in (("symbol", "OTHER-USD"), ("venue", "other"), ("market", "cash")):
        a, b = event("a", 100), event("b", 50, "FLAT")
        b[field] = other
        b["start_quote"][field] = b["end_quote"][field] = other
        assert audit(a, b)["audited_events"] == 0
    assert audit(event("a", 100), event("b", 50))["selection_value_bps"] is None


def test_order_independence_and_input_is_not_modified():
    rows = [event("a", 100), event("b", 50, "FLAT"), event("c", -10)]
    source = payload(*rows)
    before = deepcopy(source)
    forward = audit_policy_value(source, as_of=AS_OF)
    reverse = audit(*reversed(rows))
    assert forward == reverse
    assert source == before


def test_manifest_mismatch_and_bad_specification_refuse_a_score():
    with pytest.raises(ValueError, match="universe_count_mismatch"):
        audit_policy_value(payload(event("a", 100), expected=2), as_of=AS_OF)
    for key, value in (("horizon_seconds", True), ("expected_event_count", True),
                       ("pool_definition", ""), ("specification_at", "2026-10-11T00:00:00Z")):
        source = payload(event("a", 100))
        source["specification"][key] = value
        with pytest.raises(ValueError):
            audit_policy_value(source, as_of=AS_OF)
    result = audit()
    assert result["status"] == "insufficient_evidence"
    assert result["deployment_value_bps"] is None


def test_cli_produces_strict_json_and_input_fingerprint(tmp_path):
    import hashlib
    source = tmp_path / "synthetic-events.json"
    raw = json.dumps(payload(event("a", 100), event("b", 100, "FLAT"))).encode()
    source.write_bytes(raw)
    script = Path(__file__).with_name("policy_value_audit.py")
    result = subprocess.run([sys.executable, str(script), str(source), "--as-of", AS_OF],
                            capture_output=True, text=True, check=True)
    report = json.loads(result.stdout)
    assert report["input_sha256"] == hashlib.sha256(raw).hexdigest()
    assert source.read_bytes() == raw
    source.write_text('{"schema_version": "unsupported"}')
    rejected = subprocess.run([sys.executable, str(script), str(source), "--as-of", AS_OF],
                              capture_output=True, text=True)
    assert rejected.returncode == 2
    assert rejected.stdout == ""
    assert "unsupported_schema" in rejected.stderr
    source.write_text('{"schema_version":"old","schema_version":"new"}')
    duplicate = subprocess.run([sys.executable, str(script), str(source), "--as-of", AS_OF],
                               capture_output=True, text=True)
    assert duplicate.returncode == 2
    assert "duplicate_json_key" in duplicate.stderr


def test_quote_midpoint_spread_and_other_costs_are_applied_once():
    a, b = event("a", 100), event("b", 100, "FLAT")
    for row in (a, b):
        row["start_quote"].update(bid=99, ask=101)
        row["end_quote"].update(bid=100, ask=102)
        row["costs"].update(fees_bps=1, spread_bps=2, slippage_bps=3, impact_bps=4)
    result = audit(a, b)
    assert result["policy_net_bps"] == pytest.approx(45)
    assert result["composition_benchmark_bps"] == pytest.approx(45)


def test_limits_bound_memory_and_report_details(monkeypatch):
    import policy_value_audit as module
    monkeypatch.setattr(module, "MAX_EVENTS", 3)
    rows = [event(str(i), 100) for i in range(4)]
    with pytest.raises(ValueError, match="invalid_expected_event_count"):
        audit(*rows)
    with pytest.raises(ValueError, match="invalid_or_oversized_event_list"):
        audit_policy_value(payload(*rows, expected=3), as_of=AS_OF)
    monkeypatch.setattr(module, "MAX_EVENTS", 100)
    monkeypatch.setattr(module, "DETAIL_LIMIT", 1)
    result = audit(event("a", 100, pool="one"), event("b", 100, "FLAT", pool="one"),
                   event("c", 100, pool="two"), event("d", 100, "FLAT", pool="two"))
    assert result["pool_count"] == 2
    assert len(result["pools"]) == 1
    assert result["pool_details_truncated"] is True
    assert result["audited_events"] == 4
