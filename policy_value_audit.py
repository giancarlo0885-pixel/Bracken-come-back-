"""Read-only, fixed-horizon LONG/FLAT policy-value decomposition.

This is an event-level research benchmark, not a portfolio backtest or an
execution gate. No database, provider, model, or trading runtime is imported.
Inspired by https://arxiv.org/abs/2610.04040 (2026-10-02).
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
from typing import Any


SCHEMA_VERSION = "oracle-policy-value-events-v1"
REPORT_VERSION = "oracle-policy-value-audit-v1"
MAX_EVENTS = 25_000
MAX_INPUT_BYTES = 32 * 1024 * 1024
DETAIL_LIMIT = 100
COST_COMPONENTS = ("fees_bps", "spread_bps", "slippage_bps", "impact_bps")
ACTIONS = {"LONG", "FLAT"}


class EvidenceError(ValueError):
    """An event cannot be scored without guessing or using later information."""


def _text(value: Any, reason: str) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > 256:
        raise EvidenceError(reason)
    return value.strip()


def _time(value: Any, reason: str) -> datetime:
    try:
        result = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if result.tzinfo is None or result.utcoffset() is None:
            raise ValueError
        return result.astimezone(timezone.utc)
    except (AttributeError, TypeError, ValueError, OverflowError):
        raise EvidenceError(reason) from None


def _number(value: Any, reason: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise EvidenceError(reason)
    try:
        result = float(value)
    except (ValueError, OverflowError):
        raise EvidenceError(reason) from None
    if not math.isfinite(result):
        raise EvidenceError(reason)
    return result


def _quote(value: Any, row: dict[str, Any], as_of: datetime) -> tuple[datetime, float]:
    if not isinstance(value, dict) or value.get("verified") is not True:
        raise EvidenceError("unverified_quote")
    for field in ("market", "symbol", "venue"):
        if value.get(field) != row[field]:
            raise EvidenceError("quote_identity_mismatch")
    _text(value.get("source_ref"), "missing_quote_source")
    observed = _time(value.get("observed_at"), "invalid_quote_time")
    received = _time(value.get("received_at"), "invalid_quote_time")
    if not observed <= received <= as_of:
        raise EvidenceError("invalid_quote_chronology")
    bid = _number(value.get("bid"), "invalid_quote_price")
    ask = _number(value.get("ask"), "invalid_quote_price")
    if not 0 < bid <= ask:
        raise EvidenceError("crossed_or_nonpositive_quote")
    # This is a reference midpoint; modeled costs below include the spread.
    return observed, bid + (ask - bid) / 2


def _event(row: dict[str, Any], spec: dict[str, Any], as_of: datetime) -> dict[str, Any]:
    for field in ("event_id", "symbol", "venue", "pool_id", "episode_id", "decision_ref"):
        _text(row.get(field), f"missing_{field}")
    if not isinstance(row.get("market"), str) or row["market"] not in {"cash", "crypto"}:
        raise EvidenceError("invalid_market")
    baseline, policy = row.get("baseline_action"), row.get("policy_action")
    if (not isinstance(baseline, str) or not isinstance(policy, str)
            or baseline not in ACTIONS or policy not in ACTIONS):
        raise EvidenceError("invalid_action")
    decision = _time(row.get("decision_at"), "invalid_decision_time")
    information = _time(row.get("information_at"), "invalid_information_time")
    assigned = _time(row.get("pool_assigned_at"), "invalid_pool_time")
    frozen = _time(spec.get("specification_at"), "invalid_specification_time")
    if not frozen <= assigned <= decision or not information <= decision <= as_of:
        raise EvidenceError("postdecision_information_or_pool")
    start, start_mid = _quote(row.get("start_quote"), row, as_of)
    end, end_mid = _quote(row.get("end_quote"), row, as_of)
    # A tied timestamp has no ordering evidence; reject it instead of guessing.
    if not decision < start < end <= as_of:
        raise EvidenceError("nonforward_outcome")
    if (end - start).total_seconds() != spec["horizon_seconds"]:
        raise EvidenceError("horizon_mismatch")
    costs = row.get("costs")
    if not isinstance(costs, dict):
        raise EvidenceError("missing_cost_evidence")
    _text(costs.get("model_ref"), "missing_cost_model")
    if _time(costs.get("estimated_at"), "invalid_cost_time") > decision:
        raise EvidenceError("postdecision_cost_model")
    components = [_number(costs.get(key), "missing_or_invalid_cost") for key in COST_COMPONENTS]
    if any(value < 0 for value in components):
        raise EvidenceError("negative_cost")
    try:
        gross = (end_mid / start_mid - 1) * 10_000
        cost = math.fsum(components)
        net = gross - cost
    except OverflowError:
        raise EvidenceError("nonfinite_payoff") from None
    if not all(math.isfinite(value) for value in (gross, cost, net)):
        raise EvidenceError("nonfinite_payoff")
    baseline_net = net if baseline == "LONG" else 0.0
    policy_net = net if policy == "LONG" else 0.0
    return {"event_id": row["event_id"], "episode_id": row["episode_id"],
            "key": (row["market"], row["symbol"], row["venue"], row["pool_id"], baseline),
            "changed": baseline != policy, "baseline_net": baseline_net,
            "policy_net": policy_net, "deployment": policy_net - baseline_net,
            "switch_payoff": net if baseline == "FLAT" else -net}


def _mean(values: list[float]) -> float:
    # Scale before summing to avoid overflowing sums of otherwise finite values.
    return math.fsum(value / len(values) for value in values)


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate_json_key")
        result[key] = value
    return result


def audit_policy_value(payload: dict[str, Any], *, as_of: str) -> dict[str, Any]:
    """Decompose matched modeled returns; never assert significance or promotion.

    Pools preserve market, symbol, venue, the declared predecision stratum, and
    baseline action. Both changed and unchanged decisions are needed in a pool.
    LONG means a fresh fixed-notional round trip for this event; FLAT earns zero.
    It does not mean a BUY/HOLD/SELL action on an existing portfolio.
    """
    cutoff = _time(as_of, "invalid_as_of")
    if not isinstance(payload, dict) or payload.get("schema_version") != SCHEMA_VERSION:
        raise ValueError("unsupported_schema")
    spec = payload.get("specification")
    if not isinstance(spec, dict):
        raise ValueError("missing_specification")
    for field in ("universe_ref", "baseline_version", "policy_version", "pool_definition"):
        _text(spec.get(field), f"missing_{field}")
    if _time(spec.get("specification_at"), "invalid_specification_time") > cutoff:
        raise ValueError("future_specification")
    horizon, expected = spec.get("horizon_seconds"), spec.get("expected_event_count")
    if type(horizon) is not int or horizon <= 0:
        raise ValueError("invalid_horizon")
    if type(expected) is not int or not 0 <= expected <= MAX_EVENTS:
        raise ValueError("invalid_expected_event_count")
    rows = payload.get("events")
    if not isinstance(rows, list) or len(rows) > MAX_EVENTS:
        raise ValueError("invalid_or_oversized_event_list")

    exclusions: Counter[str] = Counter()
    unique: dict[str, dict[str, Any]] = {}
    conflicts: set[str] = set()
    duplicate_rows = 0
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError("event_must_be_object")
        # Without an identity, neither deduplication nor manifest coverage works.
        identity = _text(row.get("event_id"), "missing_event_id")
        if identity in unique:
            if row == unique[identity]:
                duplicate_rows += 1
            else:
                conflicts.add(identity)
        else:
            unique[identity] = row
    if len(unique) != expected:
        raise ValueError("universe_count_mismatch")

    groups: dict[tuple[str, ...], list[dict[str, Any]]] = defaultdict(list)
    excluded_ids = []
    for identity, row in sorted(unique.items()):
        try:
            if identity in conflicts:
                raise EvidenceError("conflicting_event_identity")
            event = _event(row, spec, cutoff)
        except EvidenceError as exc:
            exclusions[str(exc)] += 1
            if len(excluded_ids) < DETAIL_LIMIT:
                excluded_ids.append({"event_id": identity, "reason": str(exc)})
            continue
        groups[event["key"]].append(event)

    qualified = sum(map(len, groups.values()))
    audited, benchmarks, pools = [], [], []
    for key, events in sorted(groups.items()):
        size = len(events)
        changed = sum(event["changed"] for event in events)
        if changed == 0 or changed == size:
            exclusions["pool_without_changed_and_unchanged"] += size
            continue
        # Exact expected value of uniformly reassigning the observed number of
        # interventions. Random trials are unnecessary for this expectation.
        composition = (changed / size) * _mean([event["switch_payoff"] for event in events])
        deployment = _mean([event["deployment"] for event in events])
        audited.extend(events)
        benchmarks.extend([composition] * size)
        pools.append({"market": key[0], "symbol": key[1], "venue": key[2],
                      "pool_id": key[3], "baseline_action": key[4], "events": size,
                      "changed": changed, "unchanged": size - changed,
                      "episodes": len({event["episode_id"] for event in events}),
                      "deployment_value_bps": deployment,
                      "composition_benchmark_bps": composition,
                      "selection_value_bps": deployment - composition})

    count = len(audited)
    deployment = _mean([event["deployment"] for event in audited]) if count else None
    composition = _mean(benchmarks) if count else None
    report = {
        "version": REPORT_VERSION, "as_of": cutoff.isoformat(), "specification": dict(spec),
        "status": "descriptive_only" if count else "insufficient_evidence",
        "execution_impact": "NONE", "promotion_evidence_ready": False,
        "input_rows": len(rows), "unique_events": len(unique), "duplicate_rows": duplicate_rows,
        "qualified_events": qualified, "audited_events": count,
        "coverage_fraction": count / len(unique) if unique else 0.0,
        "audited_episodes": len({event["episode_id"] for event in audited}),
        "excluded_events": len(unique) - count, "exclusions": dict(sorted(exclusions.items())),
        "excluded_event_examples": excluded_ids,
        "baseline_net_bps": _mean([event["baseline_net"] for event in audited]) if count else None,
        "policy_net_bps": _mean([event["policy_net"] for event in audited]) if count else None,
        "deployment_value_bps": deployment, "composition_benchmark_bps": composition,
        "selection_value_bps": deployment - composition if count else None,
        "pool_count": len(pools), "pools": pools[:DETAIL_LIMIT],
        "pool_details_truncated": len(pools) > DETAIL_LIMIT,
        "p_value": None, "confidence_interval": None,
        "limitations": [
            "Modeled fixed-notional event returns; not actual fills or portfolio P&L.",
            "The supplied manifest, predecision evidence and cost model require external verification.",
            "Excluded events can bias this qualified-subset comparison; inspect coverage.",
            "Exchangeability, dependent episodes and repeated model selection are not validated.",
            "No statistical significance, sequential-policy value or trading readiness is inferred.",
        ],
    }
    # Refuse invalid JSON if extreme input causes overflow in an aggregate.
    json.dumps(report, allow_nan=False)
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path, help="Versioned event JSON; no database connection is used")
    parser.add_argument("--as-of", required=True, help="Timezone-aware evidence cutoff")
    args = parser.parse_args()
    try:
        with args.input.open("rb") as source:
            raw = source.read(MAX_INPUT_BYTES + 1)
        if len(raw) > MAX_INPUT_BYTES:
            raise ValueError("input_exceeds_32_MiB")
        report = audit_policy_value(json.loads(raw, object_pairs_hook=_unique_object), as_of=args.as_of)
        report["input_sha256"] = hashlib.sha256(raw).hexdigest()
        print(json.dumps(report, indent=2, sort_keys=True, allow_nan=False))
    except (OSError, ValueError, TypeError, OverflowError) as exc:
        parser.exit(2, f"policy-value audit refused input: {exc}\n")


if __name__ == "__main__":
    main()
