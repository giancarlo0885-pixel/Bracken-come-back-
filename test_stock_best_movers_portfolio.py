from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

from capital_allocator import adaptive_capital_allocation
from stock_best_movers import (
    broker_capacity_valid,
    core_tactical_quantities,
    holding_view_rows,
    is_allowed_us_stock,
    rank_best_movers,
    sector_capacity_ok,
    should_rotate,
    stock_position_plan,
    tactical_sell_quantity,
    validate_mover_for_entry,
)


def valid_mover(symbol: str = "NVDA", **overrides):
    row = {
        "symbol": symbol,
        "market": "cash",
        "asset_class": "stock",
        "exchange": "NASDAQ",
        "region": "US",
        "price": 125.0,
        "requested_symbol": symbol,
        "provider_symbol": symbol,
        "quote_verified": True,
        "quote_timestamp": (datetime.now(timezone.utc) - timedelta(seconds=30)).isoformat(),
        "quote_age_seconds": 30,
        "avg_volume": 2_000_000,
        "avg_dollar_volume": 250_000_000,
        "spread_pct": 0.004,
        "distance_from_vwap_pct": 0.02,
        "single_bar_spike_pct": 0.03,
        "change_15m_pct": 1.8,
        "change_1h_pct": 3.2,
        "change_1d_pct": 4.0,
        "relative_volume": 2.4,
        "breakout_score": 78,
        "catalyst_score": 65,
        "regime_alignment": 70,
    }
    row.update(overrides)
    return row


def test_foreign_equity_rejected():
    assert is_allowed_us_stock("BHP.AX") is False
    assert is_allowed_us_stock({"symbol": "VOD.L", "exchange": "LSE", "region": "GB"}) is False


def test_us_equity_allowed():
    assert is_allowed_us_stock("NVDA") is True
    assert is_allowed_us_stock({"symbol": "SPY", "market": "cash", "exchange": "NYSEARCA", "region": "US"}) is True


def test_rotation_requires_material_improvement():
    assert should_rotate(incoming_score=80, held_score=74, minimum_improvement=8) is False
    assert should_rotate(incoming_score=85, held_score=74, minimum_improvement=8) is True


def test_core_spy_cannot_be_sold_by_tactical_spy_exit():
    positions = [
        {"symbol": "SPY", "bucket": "core", "quantity": 100},
        {"symbol": "SPY", "bucket": "tactical", "quantity": 7},
    ]

    assert core_tactical_quantities(positions, "SPY") == {"core": 100.0, "tactical": 7.0}
    assert tactical_sell_quantity(positions, "SPY", 50) == 7.0


def test_unverified_stale_illiquid_and_wide_spread_movers_are_not_purchased():
    assert validate_mover_for_entry(valid_mover(quote_verified=False))[0] == "REJECT"
    assert validate_mover_for_entry(valid_mover(quote_age_seconds=9_999))[0] == "REJECT"
    assert validate_mover_for_entry(valid_mover(avg_dollar_volume=5_000_000))[0] == "REJECT"
    assert validate_mover_for_entry(valid_mover(spread_pct=0.03))[0] == "REJECT"


def test_extended_mover_waits_for_pullback():
    action, reason = validate_mover_for_entry(valid_mover(distance_from_vwap_pct=0.075))

    assert action == "WAIT_FOR_PULLBACK"
    assert "VWAP" in reason


def test_a_b_c_position_sizes_are_respected():
    common = dict(
        symbol="NVDA",
        market="cash",
        equity=100_000,
        cash=80_000,
        current_exposure=20_000,
        price=100,
        stop_price=94,
        confidence=82,
        reward_risk=2.0,
        market_regime="risk_on",
        dollar_volume=250_000_000,
        spread_pct=0.004,
        existing_position_value=0,
        buying_power=80_000,
        buying_power_validated=True,
    )

    a = adaptive_capital_allocation(**common, tier="A")
    b = adaptive_capital_allocation(**common, tier="B")
    c = adaptive_capital_allocation(**common, tier="C")

    assert a.calculated_notional > b.calculated_notional > c.calculated_notional


def test_sector_exposure_limit_works():
    assert sector_capacity_ok(current_sector_exposure_pct=0.20, proposed_position_pct=0.05, max_sector_pct=0.30)
    assert not sector_capacity_ok(current_sector_exposure_pct=0.28, proposed_position_pct=0.05, max_sector_pct=0.30)


def test_best_mover_ranking_does_not_rank_by_raw_percentage_gain_alone():
    ranked = rank_best_movers(
        [
            valid_mover("THIN", price=8, change_1d_pct=30, avg_volume=20_000, avg_dollar_volume=160_000),
            valid_mover("NVDA", change_1d_pct=4, relative_volume=2.8, avg_dollar_volume=400_000_000),
        ]
    )

    assert [row["symbol"] for row in ranked["best_movers"]] == ["NVDA"]
    assert any(row["symbol"] == "THIN" and "liquidity" in row["reason"] for row in ranked["rejected"])


def test_portfolio_table_matches_stored_paper_positions():
    rows = holding_view_rows(
        [
            {
                "symbol": "AAPL",
                "name": "Apple Inc.",
                "bucket": "tactical",
                "quantity": 10,
                "average_price": 100,
                "current_price": 112,
                "sector": "Technology",
                "tier": "A",
                "strategy": "Momentum",
                "quote_provider": "polygon",
                "quote_verified": True,
                "quote_age_seconds": 15,
            }
        ],
        equity=2_000,
    )

    assert rows[0]["market_value"] == 1120
    assert rows[0]["unrealized_pnl"] == 120
    assert rows[0]["portfolio_weight_pct"] == 56
    assert rows[0]["bucket"] == "TACTICAL"


def test_broker_capacity_bug_blocks_order_sizing():
    ok, reason = broker_capacity_valid(
        {
            "cash": 1_000,
            "invested": 2_000,
            "equity": 3_000_000_000,
            "buying_power": 12_000_000_000,
            "leverage_limit": 4,
        }
    )

    assert ok is False
    assert reason == "BROKER_CAPACITY_INVALID"


def test_stock_position_plan_uses_volatility_for_stop_and_target():
    plan = stock_position_plan(100, 0.03, "B", symbol="NVDA", score=82)

    assert plan.symbol == "NVDA"
    assert plan.stop_loss == 96.25
    assert plan.take_profit == 106.0
    assert plan.tier == "B"


def test_canonical_attribution_schema_accepts_text_decision_ids():
    source = Path("database.py").read_text(encoding="utf-8")

    assert "decision_id TEXT" in source
    assert "decision_id BIGINT" not in source
