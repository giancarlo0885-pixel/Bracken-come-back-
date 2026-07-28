from research_lab import build_research_report


def signal(**extra):
    base={"symbol":"TEST","trend_score":82,"momentum_score":78,"volume_score":75,"sentiment_score":70,"confidence":0.82}
    base.update(extra); return base


def common():
    return dict(
        market="cash",
        quant={"alpha_score":84,"relative_value_score":72,"risk_score":80},
        memory={"analog_win_rate_pct":68,"analog_count":8},
        global_intelligence={"global_score":76},
        radar={"setup_score":85,"primary_setup":"trend_continuation"},
        scenario={"probability_of_profit":72,"expected_return_pct":5.5},
        capital={"portfolio_fit_score":81},
        explainability={"invalidation_conditions":["Break of support"]},
    )


def test_research_report_is_structured_and_truthful_about_missing_data():
    report=build_research_report(signal(), **common())
    assert report.research_score >= 65
    assert report.bull_case and report.bear_case
    assert report.invalidation_conditions == ["Break of support"]
    assert report.data_gaps


def test_fundamentals_change_research_score_when_available():
    weak=build_research_report(signal(), **common())
    strong=build_research_report(signal(revenue_growth=.25, earnings_growth=.30, profit_margin=.22, debt_to_equity=.4, free_cash_flow_growth=.2), **common())
    assert strong.fundamental_score > weak.fundamental_score
    assert strong.research_score > weak.research_score
