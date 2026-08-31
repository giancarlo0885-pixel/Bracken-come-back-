from __future__ import annotations

import json
from contextlib import contextmanager
from datetime import datetime, timezone

import pytest

import database
import market_memory
from advisor_engine import AdvisorProfile, generate_recommendation
from oracle_decision_identity import (
    build_oracle_judgment,
    canonical_oracle_action,
    guard_oracle_action,
)


def _quote(symbol: str = "AAPL", price: float = 100.0) -> dict:
    now = datetime.now(timezone.utc).isoformat()
    return {
        "symbol": symbol,
        "requested_symbol": symbol,
        "provider_symbol": symbol,
        "provider": "unit",
        "price": price,
        "quote_timestamp": now,
        "interval": "5m",
        "quote_verified": True,
        "currency": "USD",
        "exchange": "NASDAQ",
    }


def _advisor_candidate(**overrides) -> dict:
    candidate = {
        "symbol": "AAPL",
        "name": "Apple",
        "market": "cash",
        "verified_quote": _quote("AAPL", 200.0),
        "confidence": 90.0,
        "opportunity_score": 92.0,
        "expected_return": 6.0,
        "expected_downside": 3.0,
        "data_quality_score": 90.0,
        "liquidity_value": 2_000_000.0,
        "validation_status": "approved",
        "catalyst": "Confirmed supplied catalyst",
        "investment_thesis": "Supplied thesis",
        "risk_factors": ["Market risk"],
        "thesis_invalidation_conditions": ["Verified evidence no longer supports the thesis"],
    }
    candidate.update(overrides)
    return candidate


def test_oracle_aliases_normalize_to_shared_vocabulary():
    assert canonical_oracle_action("STRONG_BUY") == "STRONG BUY"
    assert canonical_oracle_action("ACCUMULATE") == "BUY"
    assert canonical_oracle_action("WATCH") == "WAIT"
    assert canonical_oracle_action("made-up-action") == "UNKNOWN"


def test_guard_never_promotes_non_entry_action():
    result = guard_oracle_action(
        "WAIT",
        expected_return=20,
        risk_reward_ratio=10,
        opportunity_score=99,
        confidence=99,
        quote_verified=True,
        forecast_approved=True,
        data_quality_score=99,
        liquidity_available=True,
    )
    assert result.action == "WAIT"
    assert result.entry_allowed is False


def test_negative_expected_return_cannot_be_buy():
    result = guard_oracle_action(
        "STRONG BUY",
        expected_return=-1.0,
        expected_downside=2.0,
        risk_reward_ratio=-0.5,
        opportunity_score=99,
        confidence=99,
        quote_verified=True,
        forecast_approved=True,
        data_quality_score=99,
        liquidity_available=True,
    )
    assert result.action == "AVOID"
    assert result.entry_allowed is False
    assert "NON_POSITIVE_EXPECTED_RETURN" in result.reason_codes


def test_missing_critical_evidence_cannot_be_buy():
    result = guard_oracle_action(
        "BUY",
        expected_return=5,
        expected_downside=2,
        risk_reward_ratio=2.5,
        opportunity_score=90,
        confidence=90,
        quote_verified=False,
        forecast_approved=False,
        data_quality_score=90,
        liquidity_available=False,
    )
    assert result.action == "WAIT"
    assert result.entry_allowed is False
    assert {"QUOTE_NOT_VERIFIED", "FORECAST_NOT_APPROVED", "LIQUIDITY_UNKNOWN"} <= set(result.reason_codes)


def test_advisor_buy_has_ten_question_oracle_judgment():
    rec = generate_recommendation(_advisor_candidate(), AdvisorProfile(available_capital=10_000))
    assert rec.action in {"BUY", "STRONG BUY"}
    assert len(rec.oracle_judgment) == 10
    assert rec.oracle_judgment["final_judgment"]["action"] == rec.action
    assert rec.oracle_judgment["final_judgment"]["entry_allowed"] is True
    assert rec.oracle_judgment["certainty"]["kind"] == "HEURISTIC_SCORE"
    assert rec.oracle_judgment["certainty"]["is_calibrated_probability"] is False


def test_advisor_high_score_with_negative_edge_is_not_buy():
    rec = generate_recommendation(
        _advisor_candidate(expected_return=-3.0, expected_downside=2.0),
        AdvisorProfile(available_capital=10_000),
    )
    assert rec.action not in {"BUY", "STRONG BUY"}
    assert rec.oracle_judgment["final_judgment"]["entry_allowed"] is False
    assert "NON_POSITIVE_EXPECTED_RETURN" in rec.oracle_judgment["final_judgment"]["reason_codes"]


def test_advisor_missing_forecast_remains_non_entry():
    rec = generate_recommendation(
        _advisor_candidate(validation_status="shadow", forecast_approved=False),
        AdvisorProfile(available_capital=10_000),
    )
    assert rec.action not in {"BUY", "STRONG BUY"}
    assert rec.oracle_judgment["final_judgment"]["entry_allowed"] is False


def test_judgment_keeps_unknown_inputs_unknown():
    guarded = guard_oracle_action("WAIT")
    judgment = build_oracle_judgment(action_result=guarded)
    assert judgment["what_is_happening"] == "UNKNOWN"
    assert judgment["why_might_it_continue"] == "UNKNOWN"
    assert judgment["estimated_upside_pct"] is None
    assert judgment["certainty"]["value"] is None


class _Result:
    def __init__(self, *, one=None, many=None):
        self._one = one
        self._many = list(many or [])

    def fetchone(self):
        return self._one

    def fetchall(self):
        return list(self._many)


class _ProvenanceConn:
    def __init__(self, *, include_exact_audit: bool = True):
        self.include_exact_audit = include_exact_audit
        self.inserted_trade_dna = None
        self.audit_queries: list[tuple[str, tuple]] = []

    def execute(self, sql: str, params=()):
        normalized = " ".join(sql.split())
        if "FROM position_lots" in normalized:
            return _Result(
                many=[
                    {
                        "id": 1,
                        "lot_id": "lot-a",
                        "opened_at": "2026-08-01T00:00:00+00:00",
                        "quantity_opened": 2.0,
                        "entry_price": 100.0,
                        "decision_id": "signal-A",
                        "entry_signal_id": None,
                        "entry_forecast_id": None,
                        "entry_quote_id": None,
                        "entry_model": None,
                        "entry_model_version": None,
                        "entry_quote_timestamp": None,
                        "entry_provider": None,
                        "entry_correlation_id": None,
                        "entry_feature_snapshot": None,
                        "entry_decision_snapshot": None,
                    }
                ]
            )
        if "FROM oracle_decision_audit" in normalized:
            self.audit_queries.append((normalized, tuple(params)))
            signal_id = str(params[-1])
            if not self.include_exact_audit or signal_id != "signal-A":
                return _Result(one=None)
            # A later decision B exists conceptually, but the exact SQL predicate
            # must resolve only A. Its very different feature values must never win.
            payload_a = {
                "signal_id": "signal-A",
                "forecast_id": "forecast-A",
                "features": {"alpha": 0.61, "confidence": 0.72, "trend": 0.2},
                "quant": {
                    "alpha_score": 61.0,
                    "execution_score": 80.0,
                    "risk_score": 75.0,
                    "relative_value_score": 64.0,
                    "trade_quality": 70.0,
                    "net_expected_value_pct": 0.02,
                    "estimated_cost_pct": 0.002,
                    "adverse_selection_score": 25.0,
                },
                "opportunity_score": 70.0,
                "probability_of_profit": 58.0,
                "risk_reward_ratio": 1.8,
                "market_regime": "neutral",
                "reason": "entry decision A",
            }
            return _Result(one={"payload": payload_a, "opportunity_score": 70.0, "created_at": "2026-08-01T00:00:00+00:00"})
        if normalized.startswith("INSERT INTO trade_dna"):
            self.inserted_trade_dna = tuple(params)
            return _Result(one=None)
        raise AssertionError(f"Unexpected SQL in provenance test: {normalized}")


@contextmanager
def _fake_connect(conn):
    yield conn


def test_closed_trade_memory_uses_exact_entry_decision_not_latest(monkeypatch):
    conn = _ProvenanceConn(include_exact_audit=True)
    monkeypatch.setattr(database, "connect", lambda: _fake_connect(conn))
    written = market_memory.record_closed_trade_memory(
        market="cash",
        symbol="AAPL",
        position={"opened_at": "2026-08-01T00:00:00+00:00", "average_price": 100.0},
        exit_price=110.0,
        pnl=20.0,
        exit_reason="unit-close",
        quantity=2.0,
    )
    assert written is True
    assert conn.inserted_trade_dna is not None
    assert conn.audit_queries
    sql, params = conn.audit_queries[0]
    assert "payload->>'signal_id'=%s" in sql
    assert params[-1] == "signal-A"
    dna = json.loads(conn.inserted_trade_dna[-2])
    assert dna["provenance_status"] == market_memory.EXACT_PROVENANCE_STATUS
    assert dna["entry_signal_ids"] == ["signal-A"]
    assert dna["entry_forecast_ids"] == ["forecast-A"]
    assert dna["features"]["alpha"] == pytest.approx(0.61)


def test_closed_trade_memory_fails_closed_when_exact_entry_missing(monkeypatch):
    conn = _ProvenanceConn(include_exact_audit=False)
    monkeypatch.setattr(database, "connect", lambda: _fake_connect(conn))
    written = market_memory.record_closed_trade_memory(
        market="cash",
        symbol="AAPL",
        position={"opened_at": "2026-08-01T00:00:00+00:00", "average_price": 100.0},
        exit_price=110.0,
        pnl=20.0,
        exit_reason="unit-close",
        quantity=2.0,
    )
    assert written is False
    assert conn.inserted_trade_dna is None
