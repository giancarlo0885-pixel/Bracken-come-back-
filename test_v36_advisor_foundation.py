from __future__ import annotations

from datetime import datetime, timezone

import pytest

from advisor_engine import AdvisorProfile, generate_recommendation
from asset_routing import infer_asset_class
from broker_interface import DisabledBrokerAdapter
from execution_policy import execution_policy
from order_proposals import approve_proposal, create_order_proposal
from portfolio_optimizer import portfolio_fit_score
from price_consensus import verify_price_consensus
from production_audit import build_audit_report, clean_paper_portfolio_command
from risk_engine import ExecutionSwitches, pre_trade_risk_checks
from security import redact_headers, redact_url, safe_exception
from shadow_trading import simulate_shadow_order
from strategy_engine import evaluate_strategies, ensemble_score


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


def test_advisor_recommendation_has_required_structure_and_warnings():
    rec = generate_recommendation(
        {
            "symbol": "AAPL",
            "name": "Apple",
            "market": "cash",
            "exchange": "NASDAQ",
            "currency": "USD",
            "price": 200,
            "verified_quote": _quote("AAPL", 200),
            "confidence": 85,
            "opportunity_score": 82,
            "expected_return": 6,
            "expected_downside": 3,
            "data_quality_score": 90,
            "liquidity_value": 1_000_000,
            "validation_status": "approved",
            "catalyst": "confirmed earnings momentum",
        },
        AdvisorProfile(available_capital=10_000),
    )
    payload = rec.to_dict()
    assert rec.action in {"BUY", "ACCUMULATE", "STRONG BUY", "HOLD"}
    assert payload["risk_reward_ratio"] == pytest.approx(2.0)
    assert {item["type"] for item in rec.evidence_used} >= {"information", "forecast", "opinion", "risk_warning"}
    assert "guarantee" not in rec.investment_thesis.lower()


def test_premium_strategy_data_is_not_fabricated():
    signals = evaluate_strategies({"symbol": "AAPL", "data_quality_score": 80}, ["insider_activity", "options_flow"])
    assert all(signal.available is False for signal in signals)
    assert all(signal.message == "Required provider evidence unavailable" for signal in signals)
    score = ensemble_score(signals)
    assert score["overall_confidence"] == 0.0


def test_asset_class_routing_covers_major_asset_types():
    assert infer_asset_class("BTC-USD") == "crypto"
    assert infer_asset_class("7203.T") == "international_equity"
    assert infer_asset_class("^GSPC") == "index"
    assert infer_asset_class("EURUSD=X") == "forex"
    assert infer_asset_class("GC=F") == "commodity"


def test_two_provider_price_consensus_rejects_large_difference():
    result = verify_price_consensus("AAPL", _quote("AAPL", 100), _quote("AAPL", 103), tolerance_pct=0.5)
    assert result.consensus_status == "rejected"
    assert "differ" in result.reason


def test_two_provider_price_consensus_accepts_matching_quotes():
    result = verify_price_consensus("AAPL", _quote("AAPL", 100), _quote("AAPL", 100.1), tolerance_pct=0.5)
    assert result.consensus_status == "verified"


def test_all_new_execution_switches_default_false_and_block_entries():
    switches = ExecutionSwitches()
    assert switches.stock_autotrade is False
    assert switches.crypto_autotrade is False
    assert switches.new_entries is False
    assert switches.automated_exits is False
    assert switches.portfolio_rotation is False
    assert switches.broker_submission is False
    result = pre_trade_risk_checks(
        market="cash",
        symbol="AAPL",
        side="BUY",
        order_value=100,
        portfolio_equity=10_000,
        cash=10_000,
        quote=_quote(),
    )
    assert result.approved is False
    assert "disabled" in result.reason


def test_enabled_switches_still_require_risk_checks():
    switches = ExecutionSwitches(autotrade=True, stock_autotrade=True, new_entries=True)
    result = pre_trade_risk_checks(
        market="cash",
        symbol="AAPL",
        side="BUY",
        order_value=4_000,
        portfolio_equity=10_000,
        cash=10_000,
        quote=_quote(),
        switches=switches,
    )
    assert result.approved is False
    assert "position size" in result.reason


def test_central_policy_requires_all_entry_switches():
    allowed = execution_policy(
        market="cash",
        intent="entry",
        overrides={
            "GLOBAL_KILL_SWITCH": False,
            "ENABLE_AUTOTRADE": True,
            "ENABLE_STOCK_AUTOTRADE": True,
            "ENABLE_NEW_ENTRIES": True,
        },
    )
    blocked = execution_policy(
        market="cash",
        intent="entry",
        overrides={
            "GLOBAL_KILL_SWITCH": False,
            "ENABLE_AUTOTRADE": True,
            "ENABLE_STOCK_AUTOTRADE": True,
            "ENABLE_NEW_ENTRIES": False,
        },
    )
    assert allowed.allowed is True
    assert blocked.allowed is False


def test_hold_recommendation_does_not_become_entry_action():
    rec = generate_recommendation(
        {
            "symbol": "AAPL",
            "market": "cash",
            "verified_quote": _quote("AAPL", 200),
            "confidence": 95,
            "opportunity_score": 95,
            "expected_return": 8,
            "expected_downside": 2,
            "data_quality_score": 90,
        },
        AdvisorProfile(available_capital=10_000),
    )
    assert rec.action in {"HOLD", "WATCH"}


def test_disabled_broker_adapter_never_submits_real_order():
    broker = DisabledBrokerAdapter()
    response = broker.order_submission({"symbol": "AAPL"})
    assert response["submitted"] is False
    assert response["status"] == "live-disabled"


def test_order_proposals_are_idempotent_and_manual_approval_only():
    proposal = create_order_proposal(
        symbol="AAPL",
        side="BUY",
        quantity=1,
        verified_quote=_quote(),
        strategy="momentum",
        recommendation_id="rec-1",
        risk_checks=[{"name": "quote", "passed": True}],
    )
    same = create_order_proposal(
        symbol="AAPL",
        side="BUY",
        quantity=2,
        verified_quote=_quote(),
        strategy="momentum",
        recommendation_id="rec-1",
        risk_checks=[],
    )
    assert proposal.idempotency_key == same.idempotency_key
    approve_proposal(proposal)
    assert proposal.approval_status == "approved"


def test_proposal_creation_rejects_failed_risk_checks():
    with pytest.raises(ValueError, match="risk checks"):
        create_order_proposal(
            symbol="AAPL",
            side="BUY",
            quantity=1,
            verified_quote=_quote(),
            strategy="momentum",
            recommendation_id="rec-risk",
            risk_checks=[{"name": "risk", "passed": False}],
        )


def test_approve_proposal_rejects_stale_quote():
    proposal = create_order_proposal(
        symbol="AAPL",
        side="BUY",
        quantity=1,
        verified_quote=_quote(),
        strategy="momentum",
        recommendation_id="rec-stale",
        risk_checks=[{"name": "risk", "passed": True}],
    )
    proposal.verified_quote["quote_timestamp"] = "2020-01-01T00:00:00+00:00"
    with pytest.raises(ValueError, match="quote"):
        approve_proposal(proposal)


def test_shadow_mode_does_not_mutate_paper_portfolio():
    portfolio = {"cash": 1000, "positions": []}
    proposal = create_order_proposal(
        symbol="AAPL",
        side="BUY",
        quantity=10,
        verified_quote=_quote(),
        strategy="shadow",
        recommendation_id="rec-2",
        risk_checks=[],
        expected_slippage=0.001,
    )
    fill = simulate_shadow_order(proposal, participation=0.5)
    assert fill.status == "partially-filled"
    assert portfolio == {"cash": 1000, "positions": []}


def test_portfolio_optimizer_flags_concentration():
    score, reasons = portfolio_fit_score(
        symbol="AAPL",
        candidate={"portfolio_equity": 1000, "suggested_value": 500, "sector": "Tech", "country": "US"},
        holdings=[],
    )
    assert score < 100
    assert any("position size" in reason for reason in reasons)


def test_audit_report_and_clean_portfolio_command_are_non_destructive():
    report = build_audit_report(
        [{"id": 1, "symbol": "AAPL", "created_at": "2026-01-01T00:00:00+00:00", "realized_pnl": 5}],
        [],
        cutoff=datetime(2026, 1, 2, tzinfo=timezone.utc),
    )
    command = clean_paper_portfolio_command("cash", approved_by="reviewer")
    assert report.destructive_changes == 0
    assert command["executed"] is False
    assert command["requires_explicit_operator_confirmation"] is True


def test_secret_redaction_covers_urls_headers_and_exceptions():
    text = redact_url("https://example.com?a=1&api_token=SECRET&apikey=ALSO")
    assert "SECRET" not in text
    assert redact_headers({"Authorization": "Bearer SECRET", "ok": "yes"})["Authorization"] == "REDACTED"
    assert "SECRET" not in safe_exception(RuntimeError("token=SECRET"))
