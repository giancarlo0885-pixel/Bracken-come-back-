from dashboard_helpers import (
    action_class,
    clean_market,
    live_data_status,
    normalized_confidence,
    parse_json,
    short_reason,
    simple_mode_visible_text,
    simple_opportunity_summary,
    simple_portfolio_builder_plan,
    simple_portfolio_scores,
    star_rating,
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
        opportunities=[{"symbol": "AAPL", "action": "BUY", "score": 95, "confidence": 95, "trade_eligible": True}],
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
            {"symbol": "AAPL", "action": "BUY", "score": 95, "confidence": 95, "trade_eligible": True},
            {"symbol": "AAPL", "action": "BUY", "score": 90, "confidence": 90, "trade_eligible": True},
        ],
        positions=[{"symbol": "AAPL", "quantity": 900, "current_price": 100}],
        max_position_pct=0.10,
        reserve_pct=0.10,
    )

    aapl_items = [item for item in plan if item["symbol"] == "AAPL"]
    assert len(aapl_items) == 1
    assert aapl_items[0]["amount"] <= 10_000


def test_fresh_data_status_displays_clearly():
    status = live_data_status({"trade_eligible": True, "quote_age_seconds": 6})

    assert status["label"] == "LIVE DATA"
    assert "6 seconds" in status["detail"]
    assert status["blocks_execution"] is False


def test_stale_data_blocks_simulated_execution_language():
    status = live_data_status({"trade_eligible": False, "data_status": "stale quote"})

    assert status["label"] == "OLD DATA"
    assert status["blocks_execution"] is True


def test_simple_opportunity_card_output_is_child_readable():
    summary = simple_opportunity_summary(
        {"symbol": "SEA", "action": "BUY", "price": 19.79, "target": 19.98, "risk": "low", "trade_eligible": True, "quote_age_seconds": 6}
    )

    assert summary["symbol"] == "SEA"
    assert summary["action"] == "BUY"
    assert summary["why"] == "The price and market signals look strong."
    assert summary["possible_gain"] == "+$0.19 per share"
