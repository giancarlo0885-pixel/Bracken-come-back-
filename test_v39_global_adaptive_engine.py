from datetime import datetime, timedelta, timezone
import os

import pytest

import global_adaptive_engine as v39


def fresh_quote(symbol="AAPL", asset_class="stock", **overrides):
    data = {
        "symbol": symbol,
        "requested_symbol": symbol,
        "provider_symbol": symbol,
        "native_symbol": symbol,
        "asset_class": asset_class,
        "exchange": "NASDAQ" if asset_class != "crypto" else "CRYPTO",
        "currency": "USD",
        "sector": "Technology" if asset_class != "crypto" else "Crypto",
        "quote_verified": True,
        "quote_timestamp": (datetime.now(timezone.utc) - timedelta(seconds=20)).isoformat(),
        "interval": "5m",
        "source_interval": "5m",
        "tradeable": True,
        "qualified_for_capital": True,
        "avg_dollar_volume": 50_000_000,
        "liquidity": 50_000_000,
        "spread_pct": 0.02,
        "opportunity_score": 82,
        "expected_move_pct": 3,
        "probability_up": 0.64,
        "confidence": 0.7,
        "data_quality_score": 90,
        "risk_score": 25,
    }
    data.update(overrides)
    return data


def test_global_identity_prevents_symbol_collisions():
    us = v39.canonical_identity({"symbol": "ABC", "native_symbol": "ABC", "asset_class": "stock", "exchange": "NYSE", "currency": "USD"})
    london = v39.canonical_identity({"symbol": "ABC", "native_symbol": "ABC", "asset_class": "stock", "exchange": "LSE", "currency": "GBP"})
    assert us["canonical_id"] != london["canonical_id"]
    merged = v39.merge_global_asset_records([
        {"symbol": "ABC", "native_symbol": "ABC", "asset_class": "stock", "exchange": "NYSE", "currency": "USD", "provider": "polygon", "provider_symbol": "ABC"},
        {"symbol": "ABC", "native_symbol": "ABC", "asset_class": "stock", "exchange": "LSE", "currency": "GBP", "provider": "eodhd", "provider_symbol": "ABC.L"},
    ])
    assert len(merged) == 2


def test_canonical_identity_keeps_provider_symbol_as_alias_not_native():
    identity = v39.canonical_identity(
        {
            "symbol": "AAPL",
            "provider_symbol": "AAPL.US",
            "provider": "EODHD",
            "asset_class": "stock",
            "exchange": "NASDAQ",
            "currency": "USD",
        }
    )

    assert identity["native_symbol"] == "AAPL"
    assert identity["provider_aliases"]["EODHD"] == "AAPL.US"


def test_stock_and_crypto_capital_cannot_mix():
    opportunities = [fresh_quote("AAPL"), fresh_quote("BTC-USD", asset_class="crypto")]
    engines = v39.split_capital_engines(
        {"equity": 100_000, "cash": 80_000, "buying_power": 200_000},
        {"equity": 50_000, "cash": 40_000, "buying_power": 80_000},
        [],
        [],
        opportunities,
    )
    assert engines["stock"]["cash"] == 80_000
    assert engines["crypto"]["cash"] == 40_000
    assert engines["stock"]["qualified_opportunities"] == 1
    assert engines["crypto"]["qualified_opportunities"] == 1


def test_unsupported_assets_remain_intelligence_only_but_can_influence_soft_scores():
    executable = [fresh_quote("XOM", sector="Energy", opportunity_score=70)]
    intelligence = [{"symbol": "OIL", "asset_class": "commodity", "strength_score": 80}]
    adjusted = v39.apply_cross_market_influence(executable, intelligence)
    assert adjusted[0]["soft_score"] > executable[0]["opportunity_score"]
    assert v39.classify_capital_engine({"asset_class": "commodity"}) == "intelligence_only"


def test_forecast_outcomes_reject_future_leakage():
    decision = {"generated_at": "2026-01-02T10:00:00+00:00", "price": 100, "expected_move_pct": 2}
    observed = {"observed_at": "2026-01-02T09:59:00+00:00", "price": 103}
    result = v39.evaluate_forecast_outcome(decision, observed)
    assert result["evaluated"] is False
    assert "future leakage" in result["reason"]


def test_learning_weights_adapt_only_after_sufficient_evidence():
    weak = [{"model": "m", "asset_class": "stock", "market_regime": "RISK_ON", "realized_edge_pct": 5} for _ in range(3)]
    strong = [{"model": "m", "asset_class": "stock", "market_regime": "RISK_ON", "realized_edge_pct": 5} for _ in range(12)]
    assert v39.learning_weights_by_context(weak, min_samples=10) == {}
    assert v39.learning_weights_by_context(strong, min_samples=10)["m|stock|RISK_ON"] > 1.0


def test_challengers_cannot_auto_promote_without_evidence():
    shadow = {"status": "SHADOW", "sample_count": 5, "directional_accuracy": 0.8, "mape": 5}
    mature = {"status": "CHALLENGER", "sample_count": 50, "directional_accuracy": 0.62, "mape": 8}
    assert v39.champion_challenger_decision(shadow)["promote"] is False
    decision = v39.champion_challenger_decision(mature, {"directional_accuracy": 0.56, "mape": 12})
    assert decision["promote"] is False
    assert decision["status"] == "ELIGIBLE_FOR_PROMOTION"


def test_liquidity_uses_adv_market_participation():
    ok = v39.liquidity_capacity({"avg_dollar_volume": 10_000_000, "spread_pct": 0.02}, 50_000)
    too_large = v39.liquidity_capacity({"avg_dollar_volume": 10_000_000, "spread_pct": 0.02}, 500_000)
    assert ok["allowed"] is True
    assert too_large["allowed"] is True
    assert too_large["partial_sizing"] is True
    assert too_large["executable_order_value"] == 100_000


def test_optimizer_respects_concentration_and_recalculates_after_each_allocation():
    tech = fresh_quote("MSFT", sector="Technology", opportunity_score=90)
    energy = fresh_quote("XOM", sector="Energy", opportunity_score=80)
    plan = v39.adaptive_portfolio_optimizer(
        [tech, energy],
        {"equity": 1_000_000, "cash": 500_000, "buying_power": 500_000},
        [{"symbol": "AAPL", "sector": "Technology", "market_value": 330_000}],
        engine="stock",
    )
    symbols = {row["symbol"] for row in plan["allocations"]}
    assert "MSFT" not in symbols
    assert "XOM" in symbols
    assert plan["recalculations"] == len(plan["allocations"])


def test_optimizer_fails_closed_when_existing_stock_sector_unknown():
    candidate = fresh_quote("MSFT", sector="Technology", opportunity_score=90)
    plan = v39.adaptive_portfolio_optimizer(
        [candidate],
        {"equity": 1_000_000, "cash": 500_000, "buying_power": 500_000},
        [{"symbol": "MYSTERY", "market_value": 10_000}],
        engine="stock",
    )
    assert plan["allocations"] == []
    assert plan["rejections"][0]["reason"] == "unknown existing position sector prevents concentration calculation"


def test_concurrent_budget_fails_closed_when_shared_ledger_required():
    result = v39.reserve_provider_budget(None, "alpha_vantage", "daily_history", database_url_configured=True)
    assert result["reserved"] is False
    assert "fail closed" in result["reason"]


def test_provider_budget_reservation_is_shared_in_supplied_ledger():
    ledger = {"polygon:quote": {"remaining": 1, "used": 0}}
    assert v39.reserve_provider_budget(ledger, "polygon", "quote")["reserved"] is True
    assert v39.reserve_provider_budget(ledger, "polygon", "quote")["reserved"] is False


def test_provider_budget_has_no_universal_500_default():
    import config

    assert config.PROVIDER_CAPABILITY_DAILY_BUDGETS[("alpha vantage", "us_history")] == config.ALPHA_VANTAGE_DAILY_REQUEST_BUDGET
    assert config.PROVIDER_CAPABILITY_DAILY_BUDGETS[("alpha vantage", "crypto")] == 0
    assert config.PROVIDER_CAPABILITY_DAILY_BUDGETS.get(("unknown", "quote")) is None


def test_decision_funnel_reflects_real_stage_counts_and_reasons():
    funnel = v39.decision_funnel([
        {"symbol": "AAPL", "stages": ["surveillance", "active_hot", "deep_research", "buy_signal", "verified_quote", "forecast_approved", "portfolio_approved"], "quote_verified": True, "qualified_for_capital": True},
        {"symbol": "OLD", "rejection_reasons": ["stale quote", "liquidity"]},
    ])
    assert funnel["counts"]["surveillance"] == 2
    assert funnel["counts"]["verified_quote"] == 1
    assert funnel["counts"]["portfolio_approved"] == 1
    assert funnel["rejection_reasons"]["stale quote"] == 1


def test_stale_data_cannot_execute_through_optimizer():
    stale = fresh_quote("AAPL", quote_timestamp=(datetime.now(timezone.utc) - timedelta(days=3)).isoformat())
    plan = v39.adaptive_portfolio_optimizer([stale], {"equity": 100_000, "cash": 80_000}, [], engine="stock")
    assert plan["allocations"] == []


def test_broker_submission_remains_false():
    assert v39.ENABLE_BROKER_SUBMISSION is False


def test_v39_table_bootstrap_executes_all_statements():
    class Conn:
        def __init__(self):
            self.statements = []
        def execute(self, statement):
            self.statements.append(statement)
    conn = Conn()
    v39.ensure_v39_tables(conn)
    joined = "\n".join(conn.statements)
    assert "global_asset_identities" in joined
    assert "global_decision_ledger" in joined
    assert "provider_budget_ledger" in joined
    assert "idx_global_decision_events_market_symbol_stage" in joined
    assert "idx_global_outcomes_decision_horizon" in joined


def test_postgres_v39_sector_enrichment_and_decision_ledger_persistence():
    if not os.getenv("DATABASE_URL"):
        pytest.skip("PostgreSQL integration test runs in CI service container")
    import database
    import market_worker

    database.initialize_database()
    now = datetime.now(timezone.utc).isoformat()
    with database.connect() as conn:
        v39.ensure_v39_tables(conn)
        conn.execute("DELETE FROM global_decision_events WHERE symbol=%s", ("V39SEC",))
        conn.execute("DELETE FROM global_decision_ledger WHERE symbol=%s", ("V39SEC",))
        conn.execute("DELETE FROM positions WHERE market=%s AND symbol=%s", ("cash", "V39SEC"))
        conn.execute("DELETE FROM global_market_candidates WHERE symbol=%s", ("V39SEC",))
        conn.execute(
            """
            INSERT INTO portfolios (market,cash,starting_balance,leverage_limit,margin_debt,updated_at)
            VALUES ('cash',1000000,1000000,4,0,%s)
            ON CONFLICT (market) DO UPDATE SET cash=1000000, starting_balance=1000000, leverage_limit=4, margin_debt=0, updated_at=EXCLUDED.updated_at
            """,
            (now,),
        )
        conn.execute(
            """
            INSERT INTO positions (
                market,
                symbol,
                quantity,
                entry_price,
                average_price,
                current_price,
                highest_price,
                opened_at,
                updated_at
            )
            VALUES ('cash','V39SEC',10,100,100,100,100,%s,%s)
            """,
            (now, now),
        )
        conn.execute(
            """
            INSERT INTO global_market_candidates (symbol,name,exchange,region,sector,payload,scanned_at)
            VALUES ('V39SEC','V39 Sector Test','NASDAQ','United States','Technology','{}'::jsonb,%s)
            ON CONFLICT (symbol) DO UPDATE SET sector=EXCLUDED.sector, scanned_at=EXCLUDED.scanned_at
            """,
            (now,),
        )

    _, positions = market_worker._v39_position_rows("cash")
    enriched = next(position for position in positions if position["symbol"] == "V39SEC")
    assert enriched["sector"] == "Technology"

    with database.connect() as conn:
        v39.persist_decision_event(
            conn,
            market="cash",
            symbol="V39SEC",
            stage="portfolio_rejected",
            payload={"signal_id": "sig-v39", "forecast_id": "fc-v39", "sector": "Technology"},
            rejection_reason="optimizer_allocation_required",
        )
        record = conn.execute(
            "SELECT decision, rejection_reasons FROM global_decision_ledger WHERE symbol=%s ORDER BY created_at DESC LIMIT 1",
            ("V39SEC",),
        ).fetchone()
    assert record["decision"] == "portfolio_rejected"
    assert "optimizer_allocation_required" in str(record["rejection_reasons"])


def test_postgres_v39_provider_budget_shared_ledger_exhausts_once():
    if not os.getenv("DATABASE_URL"):
        pytest.skip("PostgreSQL integration test runs in CI service container")
    import database

    database.initialize_database()
    with database.connect() as conn:
        v39.ensure_v39_tables(conn)
        conn.execute(
            "DELETE FROM provider_budget_ledger WHERE provider=%s AND capability=%s",
            ("unit_v39", "quote"),
        )
        first = v39.reserve_provider_budget_db(conn, "unit_v39", "quote", daily_budget=1)
        second = v39.reserve_provider_budget_db(conn, "unit_v39", "quote", daily_budget=1)

    assert first["reserved"] is True
    assert second["reserved"] is False


def test_provider_wide_cooldown_db_helpers_share_provider_state():
    now = datetime.now(timezone.utc)
    conn = FakeConn({"cooldown_until": (now + timedelta(minutes=5)).isoformat(), "last_failure": "429 rate limit"})
    active = v39.provider_cooldown_active_db(conn, "Polygon", now=now)
    assert active["active"] is True
    assert "429" in active["reason"]



def test_forecast_outcome_parses_timezone_aware_timestamps_not_strings():
    decision = {"generated_at": "2026-01-02T10:30:00+01:00", "price": 100, "expected_move_pct": 2}
    same_in_utc = {"observed_at": "2026-01-02T09:30:00Z", "price": 103}
    later = {"observed_at": "2026-01-02T09:35:00Z", "price": 103}
    assert v39.evaluate_forecast_outcome(decision, same_in_utc)["evaluated"] is False
    assert v39.evaluate_forecast_outcome(decision, later)["evaluated"] is True


def test_cross_market_influence_preserves_direction():
    energy = [fresh_quote("XOM", sector="Energy", opportunity_score=70)]
    rising = v39.apply_cross_market_influence(energy, [{"symbol": "OIL", "asset_class": "commodity", "strength_score": 60, "confidence": 1.0}])[0]
    falling = v39.apply_cross_market_influence(energy, [{"symbol": "OIL", "asset_class": "commodity", "strength_score": -60, "confidence": 1.0}])[0]
    assert rising["soft_score"] > 70
    assert falling["soft_score"] < 70


def test_optimizer_uses_partial_liquidity_capacity_instead_of_full_reject():
    candidate = fresh_quote("LIQ", avg_dollar_volume=75_000_000, liquidity=75_000_000, sector="Energy")
    plan = v39.adaptive_portfolio_optimizer(
        [candidate],
        {"equity": 100_000_000, "cash": 50_000_000, "buying_power": 50_000_000},
        [],
        engine="stock",
    )
    assert plan["allocations"]
    assert plan["allocations"][0]["amount"] == 750_000
    assert plan["allocations"][0]["liquidity"]["partial_sizing"] is True


def test_decision_funnel_is_sequential():
    funnel = v39.decision_funnel([
        {"stages": ["surveillance", "verified_quote"]},
        {"stages": ["surveillance", "active_hot", "deep_research", "buy_signal", "verified_quote"]},
    ])
    assert funnel["counts"]["surveillance"] == 2
    assert funnel["counts"]["active_hot"] == 1
    assert funnel["counts"]["verified_quote"] == 1


class FakeConn:
    def __init__(self, row=None):
        self.row = row
        self.statements = []
    def execute(self, statement, params=None):
        self.statements.append((statement, params))
        return self
    def fetchone(self):
        return self.row


def test_provider_db_budget_reservation_is_atomic_shape():
    conn = FakeConn({"requests_used": 0, "remaining_budget": 1, "cooldown_until": None})
    result = v39.reserve_provider_budget_db(conn, "polygon", "us_history", daily_budget=20)
    assert result["reserved"] is True
    assert result["remaining"] == 0
    assert any("FOR UPDATE" in statement for statement, _ in conn.statements)
    assert any("remaining_budget > 0" in statement for statement, _ in conn.statements)


def test_provider_db_budget_exhaustion_blocks_request():
    conn = FakeConn({"requests_used": 20, "remaining_budget": 0, "cooldown_until": None})
    result = v39.reserve_provider_budget_db(conn, "alpha", "daily", daily_budget=20)
    assert result["reserved"] is False
    assert "exhausted" in result["reason"]


def test_invalid_symbol_quarantine_records_retry_window():
    conn = FakeConn()
    record = v39.record_invalid_symbol_failure(conn, symbol="bad", provider="Yahoo", failure_type="empty_history", retry_after_seconds=120)
    assert record["symbol"] == "BAD"
    assert record["provider"] == "Yahoo"
    assert any("invalid_symbol_quarantine" in statement for statement, _ in conn.statements)


def test_v39_live_worker_has_actual_optimizer_and_funnel_callers():
    import inspect
    import market_worker
    source = inspect.getsource(market_worker)
    assert "adaptive_portfolio_optimizer" in source
    assert "persist_decision_event" in source
    assert "record_invalid_symbol_failure" in source


def test_local_provider_budget_does_not_block_without_database_url(monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    result = v39.reserve_provider_budget_live("polygon", "us_history", daily_budget=1)
    assert result["reserved"] is True
