from datetime import datetime, timedelta, timezone

import pytest

import crypto_opportunity_engine as crypto


def candidate(symbol="SOL-USD", **overrides):
    row = {
        "symbol": symbol,
        "market": "crypto",
        "asset_class": "crypto",
        "price": 150,
        "quote_verified": True,
        "quote_timestamp": (datetime.now(timezone.utc) - timedelta(seconds=20)).isoformat(),
        "interval": "5m",
        "dollar_volume_24h": 200_000_000,
        "liquidity": 200_000_000,
        "spread_pct": 0.005,
        "change_5m_pct": 0.4,
        "change_15m_pct": 1.2,
        "change_1h_pct": 2.0,
        "change_4h_pct": 4.0,
        "change_24h_pct": 6.0,
        "relative_volume": 2.0,
        "breakout_quality": 82,
        "crypto_regime": "risk_on",
        "reward_risk_ratio": 1.8,
        "confidence": 0.78,
        "signals": {
            "trend_momentum": True,
            "volume_liquidity": True,
            "catalyst": True,
            "market_regime": True,
            "risk_reward": True,
        },
    }
    row.update(overrides)
    return row


def test_dynamic_crypto_universe_excludes_unsupported_symbols():
    assets = [
        {"symbol": "SOL-USD", "dollar_volume_24h": 100_000_000, "spread_pct": 0.005},
        {"symbol": "OBSCURE-USD", "dollar_volume_24h": 100_000_000, "spread_pct": 0.005},
    ]

    universe = crypto.dynamic_crypto_universe(assets, lambda symbol, capability: symbol != "OBSCURE-USD")

    assert "SOL-USD" in universe["symbols"]
    assert "OBSCURE-USD" not in universe["symbols"]
    assert universe["provider_blocked"] == ["OBSCURE-USD"]


def test_illiquid_tokens_are_rejected():
    ok, reason = crypto.crypto_candidate_eligible(candidate("PEPE-USD", dollar_volume_24h=1_000_000))

    assert ok is False
    assert "24h dollar volume" in reason


def test_unverified_quotes_cannot_buy():
    ok, reason = crypto.crypto_candidate_eligible(candidate(quote_verified=False))

    assert ok is False
    assert "unverified" in reason


def test_top_crypto_ranking_is_not_raw_percentage_gain_only():
    thin = candidate("THIN-USD", change_24h_pct=45, dollar_volume_24h=25_000_000, relative_volume=1.0, breakout_quality=50, confidence=0.63, reward_risk_ratio=1.25)
    liquid = candidate("ETH-USD", change_24h_pct=5, dollar_volume_24h=1_000_000_000, relative_volume=2.5, breakout_quality=88, confidence=0.84, reward_risk_ratio=2.0)

    page = crypto.crypto_page_sections([thin, liquid], [], [], {"equity": 1_000_000, "cash": 500_000})

    assert page["best_trades"][0]["Asset"] == "ETH-USD"


def test_core_quantity_cannot_be_liquidated_by_tactical_rotation():
    positions = [
        {"symbol": "XRP-USD", "quantity": 1000, "bucket": "Core"},
        {"symbol": "XRP-USD", "quantity": 250, "bucket": "Tactical"},
    ]

    result = crypto.protected_crypto_sell_quantity("XRP-USD", 800, positions)

    assert result["core_quantity"] == 1000
    assert result["tactical_quantity"] == 250
    assert result["sellable_quantity"] == 250
    assert result["allowed"] is False


def test_abc_crypto_tier_sizing_multipliers_order_position_sizes():
    a = crypto.tactical_position_size(candidate(confidence=0.90, reward_risk_ratio=2.2), {"equity": 100_000, "cash": 80_000})
    b = crypto.tactical_position_size(candidate(confidence=0.74, reward_risk_ratio=1.6), {"equity": 100_000, "cash": 80_000})
    c = crypto.tactical_position_size(candidate(confidence=0.64, reward_risk_ratio=1.3), {"equity": 100_000, "cash": 80_000})

    assert a["tier"] == "A"
    assert b["tier"] == "B"
    assert c["tier"] == "C"
    assert a["amount"] > b["amount"] > c["amount"] > 0


def test_crypto_cash_reserve_remains_protected():
    sized = crypto.tactical_position_size(candidate(), {"equity": 100_000, "cash": 10_000})

    assert sized["allowed"] is False
    assert "reserve" in sized["reason"]


def test_crypto_core_rebalance_deploys_only_above_protected_reserve():
    quotes = {
        "BTC-USD": candidate("BTC-USD", price=60_000, confidence=0.80, reward_risk_ratio=1.6),
        "ETH-USD": candidate("ETH-USD", price=3_000, confidence=0.80, reward_risk_ratio=1.6),
    }
    plan = crypto.crypto_core_rebalance_plan(quotes, {"equity": 100_000, "cash": 25_000}, [])

    assert plan
    assert sum(row["Amount"] for row in plan) <= 15_000
    assert plan[0]["Bucket"] == "Core"


def test_crypto_core_rebalance_excludes_unverified_core_quotes():
    quotes = {
        "BTC-USD": candidate("BTC-USD", quote_verified=False),
        "ETH-USD": candidate("ETH-USD", price=3_000),
    }

    plan = crypto.crypto_core_rebalance_plan(quotes, {"equity": 100_000, "cash": 25_000}, [])

    assert all(row["Asset"] != "BTC-USD" for row in plan)
    assert any(row["Asset"] == "ETH-USD" for row in plan)


def test_single_tactical_position_stays_under_max_allocation():
    sized = crypto.tactical_position_size(candidate(), {"equity": 100_000, "cash": 80_000}, existing_value=11_500)

    assert sized["allowed"] is True
    assert sized["amount"] <= 500


def test_rotation_requires_material_score_improvement():
    incoming = candidate("AAVE-USD", confidence=0.9, reward_risk_ratio=2.4, breakout_quality=95)
    weak_holding = [{"symbol": "ATOM-USD", "quantity": 10, "bucket": "Tactical", "holding_score": 40}]
    almost_good_holding = [{"symbol": "ATOM-USD", "quantity": 10, "bucket": "Tactical", "holding_score": 90}]

    assert crypto.crypto_rotation_candidate(incoming, weak_holding)["recommended_action"] == "ROTATE INTO AAVE-USD"
    assert crypto.crypto_rotation_candidate(incoming, almost_good_holding) is None


def test_worker_provider_failure_keeps_crypto_scan_alive():
    result = crypto.worker_provider_failure_result(["BTC-USD", "ETH-USD"], "Polygon", RuntimeError("429"))

    assert result["continue_scanning"] is True
    assert result["remaining_symbols"] == ["BTC-USD", "ETH-USD"]


def test_crypto_profit_attribution_reconciles_page_totals():
    page = crypto.crypto_page_sections(
        [],
        [],
        [
            {"symbol": "SOL-USD", "strategy": "tactical", "bucket": "Tactical", "entry_price": 100, "exit_price": 120, "quantity": 2, "gross_pnl": 40, "fees": 1, "net_pnl": 39, "return_pct": 20, "status": "CLOSED"},
            {"symbol": "AAPL", "market": "cash", "net_pnl": 999},
        ],
        {"equity": 100_000, "cash": 50_000},
    )

    assert page["profit_sources"][0]["Asset"] == "SOL-USD"
    assert page["profit_sources"][0]["Net P/L"] == "+$39.00"
    assert "AAPL" not in str(page["profit_sources"])


def test_crypto_page_summary_reports_wider_universe_diagnostics():
    page = crypto.crypto_page_sections(
        [candidate("SOL-USD")],
        [{"symbol": "BTC-USD", "quantity": 1, "average_price": 50_000, "current_price": 60_000, "bucket": "Core"}],
        [],
        {"equity": 100_000, "cash": 40_000},
        provider_assets=[
            {"symbol": "INJ-USD", "dollar_volume_24h": 80_000_000, "spread_pct": 0.004},
            {"symbol": "BAD-USD", "dollar_volume_24h": 80_000_000, "spread_pct": 0.004},
            {"symbol": "THIN-USD", "dollar_volume_24h": 1_000_000, "spread_pct": 0.004},
        ],
        provider_supports_symbol=lambda symbol, capability: symbol != "BAD-USD",
    )

    assert page["summary"]["Configured Core Assets"] == 9
    assert page["summary"]["Dynamic Eligible Symbols"] == 1
    assert page["summary"]["Provider Blocked"] == 1
    assert page["summary"]["Liquidity Rejected"] == 1
    assert page["owned"][0]["Bucket"] == "Core"


def test_paper_accounting_rejects_malformed_capacity():
    sized = crypto.tactical_position_size(candidate(), {"equity": 0, "cash": 80_000})

    assert sized["allowed"] is False
    assert "capacity" in sized["reason"]
