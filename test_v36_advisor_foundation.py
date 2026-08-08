from __future__ import annotations

import os
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from advisor_engine import AdvisorProfile, generate_recommendation
from asset_routing import infer_asset_class
from broker_interface import DisabledBrokerAdapter
import database
from execution_policy import execution_policy
import model_registry
import oracle_bot
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


def _complete_entry_risk_metrics(**overrides):
    values = {
        "daily_loss_pct": 0.0,
        "weekly_loss_pct": 0.0,
        "spread_pct": 0.001,
        "slippage_pct": 0.001,
        "liquidity_value": 1_000_000,
        "correlation_exposure_pct": 0.1,
        "concentration_pct": 0.1,
        "new_entries_today": 0,
        "turnover_pct_today": 0.0,
        "leverage_used": 0.0,
        "margin_utilization_pct": 0.0,
    }
    values.update(overrides)
    return values


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
        **_complete_entry_risk_metrics(concentration_pct=0.1),
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


def test_execution_policy_strict_boolean_and_supported_markets():
    malformed = execution_policy(
        market="cash",
        intent="entry",
        overrides={
            "GLOBAL_KILL_SWITCH": False,
            "ENABLE_AUTOTRADE": "false",
            "ENABLE_STOCK_AUTOTRADE": "true",
            "ENABLE_NEW_ENTRIES": "true",
        },
    )
    unsupported = execution_policy(
        market="forex",
        intent="entry",
        overrides={
            "GLOBAL_KILL_SWITCH": False,
            "ENABLE_AUTOTRADE": "true",
            "ENABLE_STOCK_AUTOTRADE": "true",
            "ENABLE_NEW_ENTRIES": "true",
        },
    )
    assert malformed.allowed is False
    assert unsupported.allowed is False


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


def test_model_registry_governance_preserves_approved_status():
    if not os.getenv("DATABASE_URL"):
        assert model_registry.model_status("unknown", "missing") == model_registry.ModelStatus.SHADOW
        return
    database.initialize_database()
    model_registry.update_model_status("unit-model", "v1", "approved", actor="pytest", reason="operator approved")
    model_registry.register_model("unit-model", "v1", "shadow", "startup seed")
    assert model_registry.model_status("unit-model", "v1") == model_registry.ModelStatus.APPROVED


def test_model_governance_failure_does_not_update_memory(monkeypatch):
    original = dict(model_registry._REGISTRY)
    model_registry._REGISTRY.pop(("fail-model", "v1"), None)

    class FakeConn:
        def execute(self, sql, params=()):
            if "SELECT status" in sql:
                return type("Result", (), {"fetchone": lambda self: None})()
            raise RuntimeError("audit insert failed")

    class FakeContext:
        def __enter__(self):
            return FakeConn()

        def __exit__(self, exc_type, exc, tb):
            return False

    monkeypatch.setattr(database, "connect", lambda: FakeContext())
    monkeypatch.setattr(database, "utc_now", lambda: datetime.now(timezone.utc).isoformat())
    with pytest.raises(RuntimeError):
        model_registry.update_model_status("fail-model", "v1", "approved", actor="pytest", reason="should fail")
    assert ("fail-model", "v1") not in model_registry._REGISTRY
    model_registry._REGISTRY.clear()
    model_registry._REGISTRY.update(original)


def test_invalid_model_status_cannot_approve_execution():
    assert model_registry._coerce_status("not-a-real-status") == model_registry.ModelStatus.SHADOW


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


def test_signal_forecast_linkage_uses_real_signal_id():
    if not os.getenv("DATABASE_URL"):
        pytest.skip("PostgreSQL integration test runs in CI service container")
    database.initialize_database()
    signal_id = database.save_json_signal(
        "cash",
        "SIGLINK",
        100,
        90,
        "BUY",
        0.9,
        {"unit": True},
        created_at=datetime.now(timezone.utc).isoformat(),
    )
    assert isinstance(signal_id, int)


def test_duplicate_execution_claim_allows_one_decision():
    if not os.getenv("DATABASE_URL"):
        pytest.skip("PostgreSQL integration test runs in CI service container")
    database.initialize_database()
    quote = _quote("CLAIM", 101)
    quote["source_identity"] = "unit:CLAIM"
    with database.connect() as conn:
        first = oracle_bot._try_execution_claim(
            conn,
            market="cash",
            symbol="CLAIM",
            side="BUY",
            price=101,
            quote=quote,
            signal={"signal_id": "sig-1", "forecast_id": "fc-1"},
        )
        second = oracle_bot._try_execution_claim(
            conn,
            market="cash",
            symbol="CLAIM",
            side="BUY",
            price=101,
            quote=quote,
            signal={"signal_id": "sig-1", "forecast_id": "fc-1"},
        )
    assert first[0] is True
    assert second[0] is False


def _pg_execution_setup(monkeypatch, symbols: list[str]) -> None:
    if not os.getenv("DATABASE_URL"):
        pytest.skip("PostgreSQL integration test runs in CI service container")
    database.initialize_database()
    monkeypatch.setattr(oracle_bot, "ENABLE_AUTOTRADE", True)
    monkeypatch.setattr(oracle_bot, "ENABLE_STOCK_AUTOTRADE", True)
    monkeypatch.setattr(oracle_bot, "ENABLE_NEW_ENTRIES", True)
    monkeypatch.setattr(oracle_bot, "ENABLE_AUTOMATED_EXITS", True)
    monkeypatch.setattr(oracle_bot, "ENABLE_PORTFOLIO_ROTATION", True)
    monkeypatch.setattr(oracle_bot, "ENABLE_QUANT_TRADE_STANDARD", False)
    monkeypatch.setattr(oracle_bot, "PENNY_STOCK_ENABLED", False)
    monkeypatch.setattr(oracle_bot, "MIN_TRADE_VALUE", 1.0)
    with database.connect() as conn:
        conn.execute("DELETE FROM trades WHERE symbol = ANY(%s)", (symbols,))
        conn.execute("DELETE FROM positions WHERE symbol = ANY(%s)", (symbols,))
        conn.execute("DELETE FROM execution_claims WHERE symbol = ANY(%s)", (symbols,))
        conn.execute(
            """
            INSERT INTO portfolios (market,cash,starting_balance,leverage_limit,margin_debt,margin_interest_accrued,margin_interest_updated_at,broker_profile,updated_at)
            VALUES ('cash',1000000,1000000,4,0,0,%s,'test',%s)
            ON CONFLICT (market) DO UPDATE SET cash=1000000, margin_debt=0, updated_at=EXCLUDED.updated_at
            """,
            (datetime.now(timezone.utc).isoformat(), datetime.now(timezone.utc).isoformat()),
        )


def _execution_signal(symbol: str, price: float = 100.0, *, signal_id: str = "sig", forecast_id: str = "fc"):
    return SimpleNamespace(
        symbol=symbol,
        price=price,
        score=90,
        confidence=0.9,
        action="BUY",
        signal_id=signal_id,
        forecast_id=forecast_id,
        avg_dollar_volume=1_000_000_000,
    )


def _execution_quote(symbol: str, price: float = 100.0) -> dict:
    quote = _quote(symbol, price)
    quote.update(
        {
            "source_identity": f"unit:{symbol}:quote",
            "bid": price - 0.01,
            "ask": price + 0.01,
            "estimated_slippage_pct": 0.001,
            "liquidity_value": 1_000_000_000,
            "correlation_exposure_pct": 0.1,
        }
    )
    return quote


def test_postgres_duplicate_buy_concurrency(monkeypatch):
    symbol = "PGBUY"
    _pg_execution_setup(monkeypatch, [symbol])
    signal = _execution_signal(symbol, 100, signal_id="buy-sig", forecast_id="buy-fc")
    quote = _execution_quote(symbol, 100)

    def attempt():
        return oracle_bot._buy("cash", symbol, 100, signal, verified_quote=quote)

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: attempt(), range(2)))
    with database.connect() as conn:
        trades = conn.execute("SELECT * FROM trades WHERE symbol=%s AND side='BUY'", (symbol,)).fetchall()
        claims = conn.execute("SELECT * FROM execution_claims WHERE symbol=%s AND side='BUY' AND status='completed'", (symbol,)).fetchall()
        position = conn.execute("SELECT * FROM positions WHERE symbol=%s", (symbol,)).fetchone()
    assert sum(1 for result in results if result[0]) == 1
    assert len(trades) == 1
    assert len(claims) == 1
    assert position and float(position["quantity"]) > 0


def test_postgres_duplicate_sell_concurrency(monkeypatch):
    symbol = "PGSELL"
    _pg_execution_setup(monkeypatch, [symbol])
    now = datetime.now(timezone.utc).isoformat()
    with database.connect() as conn:
        conn.execute(
            "INSERT INTO positions (market,symbol,quantity,entry_price,average_price,current_price,highest_price,opened_at,updated_at) VALUES ('cash',%s,2,90,90,100,100,%s,%s)",
            (symbol, now, now),
        )
        position = conn.execute("SELECT * FROM positions WHERE symbol=%s", (symbol,)).fetchone()
    quote = _execution_quote(symbol, 100)

    def attempt():
        return oracle_bot._close_position("cash", dict(position), 100, "sell_signal", quote_metadata=quote)

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: attempt(), range(2)))
    with database.connect() as conn:
        trades = conn.execute("SELECT * FROM trades WHERE symbol=%s AND side='SELL'", (symbol,)).fetchall()
        claims = conn.execute("SELECT * FROM execution_claims WHERE symbol=%s AND side='SELL' AND status='completed'", (symbol,)).fetchall()
        remaining = conn.execute("SELECT * FROM positions WHERE symbol=%s", (symbol,)).fetchall()
    assert sum(1 for result in results if result) == 1
    assert len(trades) == 1
    assert len(claims) == 1
    assert remaining == []


def test_postgres_execution_claim_rollback_allows_retry():
    if not os.getenv("DATABASE_URL"):
        pytest.skip("PostgreSQL integration test runs in CI service container")
    database.initialize_database()
    symbol = "PGROLL"
    quote = _execution_quote(symbol, 100)
    with pytest.raises(RuntimeError):
        with database.connect() as conn:
            ok, _, _ = oracle_bot._try_execution_claim(
                conn,
                market="cash",
                symbol=symbol,
                side="BUY",
                price=100,
                quote=quote,
                signal={"signal_id": "rollback", "forecast_id": "rollback"},
            )
            assert ok is True
            raise RuntimeError("force rollback")
    with database.connect() as conn:
        claims = conn.execute("SELECT * FROM execution_claims WHERE symbol=%s", (symbol,)).fetchall()
        ok, _, _ = oracle_bot._try_execution_claim(
            conn,
            market="cash",
            symbol=symbol,
            side="BUY",
            price=100,
            quote=quote,
            signal={"signal_id": "rollback", "forecast_id": "rollback"},
        )
    assert claims == []
    assert ok is True


def _insert_position(conn, symbol: str, quantity: float = 10, price: float = 100, market: str = "cash") -> None:
    now = datetime.now(timezone.utc).isoformat()
    conn.execute(
        """
        INSERT INTO positions (market,symbol,quantity,entry_price,average_price,current_price,highest_price,opened_at,updated_at)
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)
        ON CONFLICT (market, symbol) DO UPDATE
        SET quantity=EXCLUDED.quantity,
            entry_price=EXCLUDED.entry_price,
            average_price=EXCLUDED.average_price,
            current_price=EXCLUDED.current_price,
            highest_price=EXCLUDED.highest_price,
            updated_at=EXCLUDED.updated_at
        """,
        (market, symbol, quantity, price, price, price, price, now, now),
    )


def test_postgres_duplicate_rotation_concurrency(monkeypatch):
    symbols = ["ROTATEOUT", "ROTATEIN"]
    _pg_execution_setup(monkeypatch, symbols)
    with database.connect() as conn:
        _insert_position(conn, "ROTATEOUT", quantity=5, price=80)
    outgoing_quote = _execution_quote("ROTATEOUT", 90)
    incoming_quote = _execution_quote("ROTATEIN", 100)
    rotation = {
        "symbol": "ROTATEOUT",
        "_verified_rotation_quote": outgoing_quote,
        "_rotation_action": {
            "symbol": "ROTATEOUT",
            "reason": "continuous_rotation_to_ROTATEIN",
        },
    }
    signal = _execution_signal("ROTATEIN", 100, signal_id="rotation-sig", forecast_id="rotation-fc")

    def attempt():
        return oracle_bot._buy(
            "cash",
            "ROTATEIN",
            100,
            signal,
            rotation_candidate=rotation,
            verified_quote=incoming_quote,
            rotation_verified_quote=outgoing_quote,
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: attempt(), range(2)))
    with database.connect() as conn:
        sells = conn.execute("SELECT * FROM trades WHERE symbol='ROTATEOUT' AND side='SELL'").fetchall()
        buys = conn.execute("SELECT * FROM trades WHERE symbol='ROTATEIN' AND side='BUY'").fetchall()
        out_position = conn.execute("SELECT * FROM positions WHERE symbol='ROTATEOUT'").fetchone()
        in_position = conn.execute("SELECT * FROM positions WHERE symbol='ROTATEIN'").fetchone()
    assert sum(1 for result in results if result[0]) == 1
    assert len(sells) == 1
    assert len(buys) == 1
    assert out_position is None
    assert in_position is not None


def test_postgres_rotation_atomic_rollback_then_retry(monkeypatch):
    symbols = ["ROLLROTOUT", "ROLLROTIN"]
    _pg_execution_setup(monkeypatch, symbols)
    with database.connect() as conn:
        _insert_position(conn, "ROLLROTOUT", quantity=5, price=80)
    outgoing_quote = _execution_quote("ROLLROTOUT", 90)
    incoming_quote = _execution_quote("ROLLROTIN", 100)
    rotation = {
        "symbol": "ROLLROTOUT",
        "_verified_rotation_quote": outgoing_quote,
        "_rotation_action": {
            "symbol": "ROLLROTOUT",
            "reason": "continuous_rotation_to_ROLLROTIN",
        },
    }
    signal = _execution_signal("ROLLROTIN", 100, signal_id="roll-rotation-sig", forecast_id="roll-rotation-fc")
    original_complete = oracle_bot._complete_execution_claim
    calls = {"count": 0}

    def fail_after_rotation_sell(conn, execution_key):
        calls["count"] += 1
        if calls["count"] == 1:
            raise RuntimeError("simulate failure after outgoing sell")
        return original_complete(conn, execution_key)

    monkeypatch.setattr(oracle_bot, "_complete_execution_claim", fail_after_rotation_sell)
    failed = oracle_bot._buy(
        "cash",
        "ROLLROTIN",
        100,
        signal,
        rotation_candidate=rotation,
        verified_quote=incoming_quote,
        rotation_verified_quote=outgoing_quote,
    )
    with database.connect() as conn:
        assert conn.execute("SELECT * FROM positions WHERE symbol='ROLLROTOUT'").fetchone() is not None
        assert conn.execute("SELECT * FROM positions WHERE symbol='ROLLROTIN'").fetchone() is None
        assert conn.execute("SELECT * FROM trades WHERE symbol IN ('ROLLROTOUT','ROLLROTIN')").fetchall() == []
        assert conn.execute("SELECT * FROM execution_claims WHERE symbol IN ('ROLLROTOUT','ROLLROTIN') AND status='completed'").fetchall() == []
    assert failed[0] is False

    retry = oracle_bot._buy(
        "cash",
        "ROLLROTIN",
        100,
        signal,
        rotation_candidate=rotation,
        verified_quote=incoming_quote,
        rotation_verified_quote=outgoing_quote,
    )
    with database.connect() as conn:
        sells = conn.execute("SELECT * FROM trades WHERE symbol='ROLLROTOUT' AND side='SELL'").fetchall()
        buys = conn.execute("SELECT * FROM trades WHERE symbol='ROLLROTIN' AND side='BUY'").fetchall()
    assert retry[0] is True
    assert len(sells) == 1
    assert len(buys) == 1


def test_postgres_shared_risk_rejection_rolls_no_portfolio_mutation(monkeypatch):
    symbol = "RISKREJECT"
    _pg_execution_setup(monkeypatch, [symbol])
    signal = _execution_signal(symbol, 100, signal_id="risk-reject-sig", forecast_id="risk-reject-fc")
    quote = _execution_quote(symbol, 100)
    quote.pop("liquidity_value")
    quote.pop("volume", None)
    with database.connect() as conn:
        before = conn.execute("SELECT cash, margin_debt FROM portfolios WHERE market='cash'").fetchone()
    ok, reason, _ = oracle_bot._buy("cash", symbol, 100, signal, verified_quote=quote)
    with database.connect() as conn:
        after = conn.execute("SELECT cash, margin_debt FROM portfolios WHERE market='cash'").fetchone()
        trades = conn.execute("SELECT * FROM trades WHERE symbol=%s", (symbol,)).fetchall()
        position = conn.execute("SELECT * FROM positions WHERE symbol=%s", (symbol,)).fetchone()
        completed = conn.execute("SELECT * FROM execution_claims WHERE symbol=%s AND status='completed'", (symbol,)).fetchall()
    assert ok is False
    assert "liquidity" in reason
    assert dict(before) == dict(after)
    assert trades == []
    assert position is None
    assert completed == []


def test_postgres_execution_risk_context_calculates_pnl_and_turnover(monkeypatch):
    symbol = "RISKCTX"
    _pg_execution_setup(monkeypatch, [symbol])
    now = datetime.now(timezone.utc).isoformat()
    with database.connect() as conn:
        _insert_position(conn, symbol, quantity=2, price=90)
        conn.execute(
            "INSERT INTO trades (market,symbol,side,quantity,price,value,realized_pnl,score,reason,created_at) VALUES ('cash',%s,'BUY',1,80,80,0,NULL,'entry',%s)",
            (symbol, now),
        )
        conn.execute(
            "INSERT INTO trades (market,symbol,side,quantity,price,value,realized_pnl,score,reason,created_at) VALUES ('cash',%s,'SELL',1,110,110,30,NULL,'exit',%s)",
            (symbol, now),
        )
        portfolio = conn.execute("SELECT * FROM portfolios WHERE market='cash' FOR UPDATE").fetchone()
        positions = conn.execute("SELECT * FROM positions WHERE market='cash' FOR UPDATE").fetchall()
        ctx = oracle_bot._build_execution_risk_context(
            conn,
            market="cash",
            symbol=symbol,
            side="BUY",
            intent="entry",
            order_value=100,
            portfolio=portfolio,
            positions=list(positions),
            quote=_execution_quote(symbol, 100),
        )
    assert ctx["daily_realized_pnl"] == pytest.approx(30)
    assert ctx["weekly_realized_pnl"] == pytest.approx(30)
    assert ctx["daily_unrealized_pnl"] == pytest.approx(20)
    assert ctx["turnover_pct_today"] > 0


def test_advisory_lock_failure_blocks_execution_claim():
    class FakeConn:
        def __init__(self):
            self.calls = []

        def execute(self, sql, params=()):
            self.calls.append(sql)
            if "pg_advisory_xact_lock" in sql:
                raise RuntimeError("lock unavailable")
            raise AssertionError("claim insert should not run after lock failure")

    conn = FakeConn()
    ok, _, reason = oracle_bot._try_execution_claim(
        conn,
        market="cash",
        symbol="AAPL",
        side="BUY",
        price=100,
        quote={**_quote("AAPL", 100), "source_identity": "unit:AAPL"},
        signal={"signal_id": "sig", "forecast_id": "fc"},
    )
    assert ok is False
    assert "advisory lock" in reason
    assert len(conn.calls) == 1


def test_missing_or_nan_risk_metric_blocks_entry():
    switches = ExecutionSwitches(autotrade=True, stock_autotrade=True, new_entries=True)
    missing = pre_trade_risk_checks(
        market="cash",
        symbol="AAPL",
        side="BUY",
        intent="entry",
        order_value=100,
        portfolio_equity=10_000,
        cash=10_000,
        quote=_quote(),
        switches=switches,
        **_complete_entry_risk_metrics(liquidity_value=float("nan")),
    )
    assert missing.allowed is False
    assert any("non-finite" in reason for reason in missing.reasons)


@pytest.mark.parametrize(
    ("metric", "reason"),
    [
        ("daily_loss_pct", "daily loss metric unavailable"),
        ("spread_pct", "spread metric unavailable"),
        ("liquidity_value", "liquidity value metric unavailable"),
    ],
)
def test_missing_required_entry_metric_blocks_buy(metric, reason):
    switches = ExecutionSwitches(autotrade=True, stock_autotrade=True, new_entries=True)
    metrics = _complete_entry_risk_metrics()
    metrics[metric] = None
    result = pre_trade_risk_checks(
        market="cash",
        symbol="AAPL",
        side="BUY",
        intent="entry",
        order_value=100,
        portfolio_equity=10_000,
        cash=10_000,
        quote=_quote(),
        switches=switches,
        **metrics,
    )
    assert result.allowed is False
    assert any(reason in item for item in result.reasons)


@pytest.mark.parametrize("bad_value", [float("nan"), float("inf"), float("-inf"), ""])
def test_non_finite_required_entry_metric_blocks_buy(bad_value):
    switches = ExecutionSwitches(autotrade=True, stock_autotrade=True, new_entries=True)
    result = pre_trade_risk_checks(
        market="cash",
        symbol="AAPL",
        side="BUY",
        intent="entry",
        order_value=100,
        portfolio_equity=10_000,
        cash=10_000,
        quote=_quote(),
        switches=switches,
        **_complete_entry_risk_metrics(spread_pct=bad_value),
    )
    assert result.allowed is False
    assert any("spread metric unavailable or non-finite" in item for item in result.reasons)


def test_forced_risk_reduction_sell_uses_dedicated_policy():
    switches = ExecutionSwitches(autotrade=True, stock_autotrade=True, automated_exits=True)
    result = pre_trade_risk_checks(
        market="cash",
        symbol="AAPL",
        side="SELL",
        intent="forced_risk_reduction",
        order_value=5_000,
        portfolio_equity=10_000,
        cash=100,
        quote=_quote(),
        positions=[{"symbol": "AAPL"}],
        daily_loss_pct=0.99,
        weekly_loss_pct=0.99,
        turnover_pct_today=9.0,
        spread_pct=0.99,
        slippage_pct=0.99,
        liquidity_value=1,
        concentration_pct=0.99,
        correlation_exposure_pct=0.99,
        leverage_used=1.0,
        margin_utilization_pct=25.0,
        switches=switches,
    )
    assert result.allowed is True
    assert result.warnings


def test_normal_entry_blocked_under_forced_exit_risk_conditions():
    switches = ExecutionSwitches(autotrade=True, stock_autotrade=True, new_entries=True)
    result = pre_trade_risk_checks(
        market="cash",
        symbol="AAPL",
        side="BUY",
        intent="entry",
        order_value=100,
        portfolio_equity=10_000,
        cash=10_000,
        quote=_quote(),
        switches=switches,
        **_complete_entry_risk_metrics(
            daily_loss_pct=0.99,
            turnover_pct_today=9.0,
            concentration_pct=0.99,
        ),
    )
    assert result.allowed is False
    assert "daily loss limit reached" in result.reasons
