from global_market_scanner import supported_common_equity_candidate


def test_discovery_rejects_warrants_and_unsupported_symbols():
    assert supported_common_equity_candidate("BBAI+") is False
    assert supported_common_equity_candidate("WLDSW", name="Wearable Devices Warrant") is False
    assert supported_common_equity_candidate("WLDSW") is False
    assert supported_common_equity_candidate("AAPL", name="Apple Common Stock") is True
    assert supported_common_equity_candidate("BRK.B", name="Berkshire Class B") is True


def test_dashboard_does_not_fall_back_to_unverified_decisions():
    source = open("app.py", encoding="utf-8").read()
    assert 'decision_buckets = actionable_decision_buckets(decisions, all_positions)' in source
    assert 'top = buy_decisions[0] if buy_decisions else (sell_decisions[0] if sell_decisions else None)' in source
    assert 'table_decisions = buy_decisions[:10]' in source
    assert "else ready_decisions[:10]" not in source
