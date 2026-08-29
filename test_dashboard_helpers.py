from datetime import datetime, timezone

from dashboard_helpers import (
    action_class,
    balanced_data_status,
    balanced_money_bar,
    balanced_opportunity_rows,
    balanced_portfolio_rows,
    capital_allocation_rows,
    capital_deployment_status,
    compact_money_text,
    clean_market,
    data_age_label,
    live_data_status,
    normalized_confidence,
    parse_json,
    readable_trade_rows,
    short_reason,
    simple_mode_visible_text,
    simple_opportunity_summary,
    simple_portfolio_builder_plan,
    simple_portfolio_scores,
    star_rating,
    trade_summary,
    trade_value_matches_quantity_price,
)


def test_normalized_confidence_accepts_fraction_and_percent():
    assert normalized_confidence(0.82) == 82.0
    assert normalized_confidence(82) == 82.0


def test_star_rating_is_bounded():
    assert star_rating(100) == "★★★★★"
    assert len(star_rating(0)) == 5


def test_market_and_action_labels():
    assert clean_market("cash") == "stock"
    assert action_class("BUY") == "buy"
    assert action_class("unknown") == "hold"


def test_parse_json_is_safe():
    assert parse_json('{"reason":"test"}') == {"reason": "test"}
    assert parse_json("bad-json") == {}


def test_short_reason_accepts_plain_text_and_length():
    text = "A long council explanation that should be shortened for the dashboard card."
    result = short_reason(text, 32)
    assert len(result) <= 32
    assert result.endswith("…")


def test_short_reason_accepts_record_without_length():
    assert short_reason({"reason": "Momentum confirmed"}) == "Momentum confirmed"


def test_cash_heavy_portfolio_is_not_automatically_high_risk():
    scores = simple_portfolio_scores(
        {"cash": 800_000, "invested": 200_000, "equity": 1_000_000, "starting_balance": 1_000_000, "margin_debt": 0, "leverage_used": 0.0},
        [{"symbol": "AAPL", "quantity": 10, "current_price": 100}],
    )

    assert scores["safety"] == "LOW RISK"
    assert scores["capital_use"] == "MOSTLY CASH"
    assert scores["status"] == "NEEDS MORE INVESTMENTS"


def test_under_investment_is_separate_from_safety_risk():
    scores = simple_portfolio_scores(
        {"cash": 950_000, "invested": 50_000, "equity": 1_000_000, "starting_balance": 1_000_000, "margin_debt": 0},
        [],
    )

    assert scores["safety"] == "LOW RISK"
    assert scores["diversification"] == "POOR"
    assert scores["capital_use"] == "MOSTLY CASH"


def test_simple_mode_text_hides_technical_terms():
    visible = simple_mode_visible_text(
        [
            "TODAY'S ORACLE SUMMARY",
            "MY MONEY",
            "How am I doing?",
            "Money invested",
            "Cash waiting",
            "ORACLE SAYS: BUY",
        ]
    ).lower()

    for forbidden in ("leverage", "margin", "institutional", "model_version", "provider diagnostics"):
        assert forbidden not in visible


def test_advanced_details_can_retain_technical_information():
    scores = simple_portfolio_scores(
        {"cash": 500_000, "invested": 500_000, "equity": 1_000_000, "starting_balance": 1_000_000, "margin_debt": 10_000, "leverage_used": 0.1},
        [{"symbol": "MSFT", "quantity": 5, "current_price": 100}],
    )

    assert "overall_score" in scores
    assert scores["data_quality_score"] == 100.0


def test_portfolio_allocation_respects_concentration_limits():
    plan = simple_portfolio_builder_plan(
        cash=500_000,
        equity=1_000_000,
        opportunities=[{"symbol": "AAPL", "action": "BUY", "score": 95, "confidence": 95, "expected_return": 6, "risk": "LOW", "trade_eligible": True, "quote_verified": True, "quote_age_seconds": 12}],
        positions=[],
        max_position_pct=0.10,
        reserve_pct=0.10,
    )

    aapl = next(item for item in plan if item["symbol"] == "AAPL")
    assert aapl["amount"] <= 100_000


def test_duplicate_buys_cannot_create_runaway_exposure():
    plan = simple_portfolio_builder_plan(
        cash=500_000,
        equity=1_000_000,
        opportunities=[
            {"symbol": "AAPL", "action": "BUY", "score": 95, "confidence": 95, "expected_return": 6, "risk": "LOW", "trade_eligible": True, "quote_verified": True, "quote_age_seconds": 10},
            {"symbol": "AAPL", "action": "BUY", "score": 90, "confidence": 90, "expected_return": 5, "risk": "LOW", "trade_eligible": True, "quote_verified": True, "quote_age_seconds": 10},
        ],
        positions=[{"symbol": "AAPL", "quantity": 900, "current_price": 100}],
        max_position_pct=0.10,
        reserve_pct=0.10,
    )

    aapl_items = [item for item in plan if item["symbol"] == "AAPL"]
    assert len(aapl_items) == 1
    assert aapl_items[0]["amount"] <= 10_000


def test_fresh_data_status_displays_clearly():
    status = live_data_status({"trade_eligible": True, "quote_verified": True, "quote_age_seconds": 6})

    assert status["label"] == "LIVE DATA"
    assert "6 seconds" in status["detail"]
    assert status["blocks_execution"] is False


def test_stale_data_blocks_simulated_execution_language():
    status = live_data_status({"trade_eligible": False, "data_status": "stale quote"})

    assert status["label"] == "OLD DATA"
    assert status["blocks_execution"] is True


def test_unknown_freshness_never_displays_live():
    status = live_data_status({"trade_eligible": True, "quote_verified": True})

    assert status["label"] == "FRESHNESS UNKNOWN"
    assert status["blocks_execution"] is True

def test_trade_eligible_never_substitutes_for_quote_verification():
    status = live_data_status({"trade_eligible": True, "quote_age_seconds": 5})

    assert status["label"] == "OLD DATA"
    assert status["blocks_execution"] is True


def test_delayed_data_matches_execution_freshness_gate():
    status = live_data_status(
        {
            "symbol": "AAPL",
            "market": "cash",
            "quote_verified": True,
            "quote_age_seconds": 180,
            "interval": "5m",
        }
    )

    assert status["label"] == "DELAYED DATA"
    assert status["blocks_execution"] is False


def test_expired_intraday_data_blocks_execution():
    status = live_data_status(
        {
            "symbol": "BTC-USD",
            "market": "crypto",
            "quote_verified": True,
            "quote_age_seconds": 60 * 60,
            "interval": "5m",
        }
    )

    assert status["label"] == "OLD DATA"
    assert status["blocks_execution"] is True



def test_balanced_data_age_never_calls_unknown_live():
    assert data_age_label({"trade_eligible": True, "quote_verified": True}) == "Unknown"
    assert data_age_label({"trade_eligible": True, "quote_verified": True, "quote_age_seconds": 8}) == "8 sec"


def test_planner_rejects_unverified_or_stale_opportunities():
    plan = simple_portfolio_builder_plan(
        cash=500_000,
        equity=1_000_000,
        opportunities=[
            {"symbol": "OLD", "action": "BUY", "score": 99, "confidence": 99, "expected_return": 12, "risk": "LOW", "trade_eligible": False, "data_status": "stale quote"},
            {"symbol": "UNKNOWN", "action": "BUY", "score": 99, "confidence": 99, "expected_return": 12, "risk": "LOW", "trade_eligible": True, "quote_verified": True},
        ],
        positions=[],
    )

    assert [item["symbol"] for item in plan] == ["CASH"]


def test_compact_money_text_abbreviates_large_dashboard_values():
    assert compact_money_text(1_953_837_204) == "$2.0B"
    assert compact_money_text(65_389_901) == "$65.4M"


def test_capital_allocation_rows_explain_position_size():
    rows = capital_allocation_rows(
        [
            {
                "symbol": "NVDA",
                "price": 100,
                "stop_loss": 95,
                "tier": "A",
                "confidence": 88,
                "reward_risk_ratio": 2.0,
                "market_regime": "risk_on",
                "avg_dollar_volume": 1_000_000_000,
                "spread_pct": 0.002,
            }
        ],
        {"equity": 10_000, "cash": 6_000, "invested": 2_000},
        [],
        market="cash",
    )

    assert rows[0]["Symbol"] == "NVDA"
    assert rows[0]["Base Risk $"] == "$100.00"
    assert rows[0]["Position Size $"] != "$0.00"
    assert "final risk budget" in rows[0]["Why This Size"]
    assert compact_money_text(357_000) == "$357K"


def test_stale_buy_is_not_displayed_as_green_buy():
    rows = balanced_opportunity_rows(
        [
            {
                "symbol": "COPX",
                "action": "BUY",
                "price": 33.08,
                "target": 34.12,
                "expected_return": 3.1,
                "confidence": 87,
                "risk": "LOW",
                "trade_eligible": True,
                "quote_verified": True,
                "quote_age_seconds": 60 * 60 * 24,
                "interval": "5m",
            }
        ]
    )

    assert rows[0]["Action"] == "YELLOW WATCH"
    assert rows[0]["Data Age"] == "Old"


def test_stale_buy_summary_explains_waiting_for_fresh_quotes():
    summary = simple_opportunity_summary(
        {
            "symbol": "COPX",
            "action": "BUY",
            "price": 33.08,
            "target": 34.12,
            "risk": "LOW",
            "trade_eligible": True,
            "quote_verified": True,
            "quote_age_seconds": 60 * 60 * 24,
            "interval": "5m",
        }
    )

    assert summary["action"] == "WATCH"
    assert "waiting for verified fresh price data" in summary["why"]
    assert "look strong" not in summary["why"]


def test_capital_deployment_waits_when_no_qualified_opportunities():
    status = capital_deployment_status(
        {"equity": 1_000_000, "cash": 900_000},
        [
            {
                "market": "cash",
                "symbol": "COPX",
                "action": "BUY",
                "trade_eligible": False,
                "data_status": "stale quote",
            }
        ],
        market="cash",
    )

    assert status["status"] == "blocked_quote"
    assert "Too much capital" not in status["message"]


def test_simple_opportunity_card_output_is_child_readable():
    summary = simple_opportunity_summary(
        {"symbol": "SEA", "action": "BUY", "price": 19.79, "target": 19.98, "risk": "low", "trade_eligible": True, "quote_verified": True, "quote_age_seconds": 6}
    )

    assert summary["symbol"] == "SEA"
    assert summary["action"] == "BUY"
    assert summary["why"] == "The price and market signals look strong."
    assert summary["possible_gain"] == "+$0.19 per share"


def test_readable_trade_history_hides_giant_decimal_quantities_from_main_rows():
    rows = readable_trade_rows(
        [
            {
                "created_at": "2026-08-20T12:00:00+00:00",
                "side": "BUY",
                "symbol": "ADA-USD",
                "quantity": 10472445.293979,
                "price": 0.190974,
                "value": 2_000_000.0,
                "realized_pnl": 0,
                "reason": "Institutional paper buy; very long technical reason",
            }
        ]
    )

    assert rows[0]["Bought / Sold"] == "Bought"
    assert rows[0]["Asset"] == "ADA-USD"
    assert rows[0]["Quantity"] == "10,472,445"
    assert rows[0]["Money Used"] == "$2,000,000.00"


def test_balanced_money_bar_has_professional_top_cards():
    items = balanced_money_bar(
        {"starting_balance": 1_000_000, "equity": 1_000_030, "cash": 750_000, "invested": 250_030},
        [{"created_at": "2026-08-20T12:00:00+00:00", "realized_pnl": 30}],
        now=datetime(2026, 8, 20, tzinfo=timezone.utc),
    )

    assert [item["label"] for item in items] == ["PORTFOLIO VALUE", "CASH", "INVESTED", "TOTAL PROFIT / LOSS", "TODAY"]
    assert items[-1]["value"] == "+$30"


def test_balanced_top_opportunity_table_output_is_compact():
    rows = balanced_opportunity_rows(
        [
            {"symbol": "SEA", "action": "BUY", "price": 19.79, "target": 20.72, "expected_return": 4.7, "confidence": 82, "risk": "medium", "trade_eligible": True, "quote_verified": True, "quote_age_seconds": 8},
            {"symbol": "WAIT", "action": "HOLD", "price": 10, "target": 10, "confidence": 55, "risk": "low", "trade_eligible": False, "data_status": "stale"},
        ],
        limit=2,
        hide_rejected=False,
    )

    assert rows[0] == {
        "Action": "GREEN BUY",
        "Symbol": "SEA",
        "Price": "$19.79",
        "Target": "$20.72",
        "Possible Gain %": "+4.7%",
        "Confidence": "82%",
        "Risk": "Medium",
        "Data Age": "8 sec",
    }
    assert rows[1]["Action"] == "YELLOW HOLD"
    assert rows[1]["Data Age"] == "Old"


def test_balanced_opportunity_rows_hides_rejected_by_default():
    rows = balanced_opportunity_rows(
        [
            {"symbol": "SEA", "action": "BUY", "price": 19.79, "target": 20.72, "expected_return": 4.7, "confidence": 82, "risk": "medium", "trade_eligible": True, "quote_verified": True, "quote_age_seconds": 8},
            {"symbol": "WAIT", "action": "HOLD", "price": 10, "target": 10, "confidence": 55, "risk": "low", "trade_eligible": False, "data_status": "stale"},
        ],
        limit=10,
    )

    assert len(rows) == 1
    assert rows[0]["Symbol"] == "SEA"
    assert all(row["Symbol"] != "WAIT" for row in rows)


def test_balanced_opportunity_rows_returns_hidden_items_when_requested():
    rows, hidden = balanced_opportunity_rows(
        [
            {"symbol": "SEA", "action": "BUY", "price": 19.79, "target": 20.72, "expected_return": 4.7, "confidence": 82, "risk": "medium", "trade_eligible": True, "quote_verified": True, "quote_age_seconds": 8},
            {"symbol": "WAIT", "action": "HOLD", "price": 10, "target": 10, "confidence": 55, "risk": "low", "trade_eligible": False, "data_status": "stale"},
        ],
        limit=10,
        hide_rejected=True,
        return_hidden=True,
    )

    assert len(rows) == 1
    assert rows[0]["Symbol"] == "SEA"
    assert len(hidden) == 1
    assert hidden[0]["Symbol"] == "WAIT"


def test_balanced_portfolio_table_rows_show_status_without_raw_quantity():
    rows = balanced_portfolio_rows(
        [
            {"symbol": "AAPL", "quantity": 10, "average_price": 100, "current_price": 110},
            {"symbol": "RISKY", "quantity": 1_000, "average_price": 100, "current_price": 80},
        ],
        equity=100_000,
    )

    assert set(rows[0]) == {"Symbol", "Value", "Allocation %", "Avg Price", "Current Price", "Profit/Loss", "Status"}
    assert rows[0]["Status"] == "GOOD"
    assert rows[1]["Status"] == "HIGH RISK"


def test_balanced_data_status_is_compact_provider_bar():
    rows = balanced_data_status(True, True, [{"provider": "EODHD", "status": "cooldown"}])

    assert rows == [
        {"Area": "STOCKS", "Status": "GREEN"},
        {"Area": "CRYPTO", "Status": "GREEN"},
        {"Area": "NEWS", "Status": "YELLOW"},
        {"Area": "GLOBAL", "Status": "YELLOW"},
    ]


def test_trade_summary_counts_and_formats_business_values():
    trades = [
        {"side": "BUY", "value": 80, "realized_pnl": 0},
        {"side": "SELL", "value": 110, "realized_pnl": 30},
    ]
    summary = trade_summary(trades)

    assert summary["Total Trades"] == 2
    assert summary["Buys"] == 1
    assert summary["Sells"] == 1
    assert summary["Realized P/L"] == 30
    assert summary["Trade Volume"] == 190


def test_trade_value_arithmetic_detects_huge_trade_consistency():
    assert trade_value_matches_quantity_price({"quantity": 10_000_000, "price": 0.20, "value": 2_000_000})
    assert not trade_value_matches_quantity_price({"quantity": 10_000_000, "price": 0.20, "value": 200_000})
