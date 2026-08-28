from datetime import datetime, timezone, timedelta

import global_pit_engine as pit


def fresh_asset(symbol="AAPL", **overrides):
    data = {
        "symbol": symbol,
        "requested_symbol": symbol,
        "provider_symbol": symbol,
        "asset_class": "stock",
        "country": "United States",
        "exchange": "NASDAQ",
        "sector": "Technology",
        "expected_move_pct": 4.0,
        "probability_up": 0.64,
        "confidence": 0.74,
        "data_quality_score": 88,
        "liquidity": 25_000_000,
        "spread_pct": 0.001,
        "risk_score": 35,
        "portfolio_fit": 75,
        "action": "BUY",
        "quote_verified": True,
        "quote_timestamp": (datetime.now(timezone.utc) - timedelta(seconds=30)).isoformat(),
        "interval": "5m",
        "tradeable": True,
    }
    data.update(overrides)
    return data


def test_global_discovery_is_not_restricted_to_static_watchlists():
    universe = pit.build_global_universe(
        {"AAPL": "Apple"},
        [
            {"symbol": "7203.T", "name": "Toyota", "asset_class": "stock", "country": "Japan", "discovery_source": "provider_symbol_search"},
            {"symbol": "BTC-USD", "asset_class": "crypto", "discovery_source": "crypto_mover"},
        ],
    )
    symbols = {item["symbol"] for item in universe}
    assert {"AAPL", "7203.T", "BTC-USD"}.issubset(symbols)
    toyota = next(item for item in universe if item["symbol"] == "7203.T")
    assert "provider_symbol_search" in toyota["discovery_sources"]


def test_scanners_continue_while_positions_exist():
    assets = [fresh_asset("AAPL"), fresh_asset("MSFT", market_value=100_000)]
    lanes = pit.schedule_scanning_lanes(assets, provider_budget={"shared": 10})
    planned_symbols = {asset["symbol"] for lane in lanes for asset in lane["assets"]}
    assert {"AAPL", "MSFT"}.issubset(planned_symbols)


def test_priority_shifts_toward_hot_open_markets_and_closed_reduces_quote_polling():
    now = datetime(2026, 7, 6, 18, 0, tzinfo=timezone.utc)
    open_asset = fresh_asset("AAPL", change_1d_pct=8, relative_volume=4, news_intensity=4)
    closed_asset = fresh_asset("SHEL.L", exchange="LSE", country="United Kingdom", change_1d_pct=8, relative_volume=4, news_intensity=4)
    open_attention = pit.attention_for_asset(open_asset, now)
    closed_attention = pit.attention_for_asset(closed_asset, now)
    assert open_attention["attention_score"] > closed_attention["attention_score"]
    assert closed_attention["quote_poll_seconds"] > open_attention["quote_poll_seconds"]


def test_open_exchanges_receive_increased_attention():
    now = datetime(2026, 7, 6, 14, 0, tzinfo=timezone.utc)
    active = pit.attention_for_asset(fresh_asset("AAPL", change_1d_pct=5, relative_volume=3), now)
    quiet = pit.attention_for_asset(fresh_asset("MSFT", change_1d_pct=0.1, relative_volume=1), now)
    assert active["attention_score"] > quiet["attention_score"]
    assert active["attention_level"] in {"ACTIVE", "HOT", "CRITICAL"}


def test_crypto_remains_24_7():
    saturday = datetime(2026, 8, 1, 12, 0, tzinfo=timezone.utc)
    attention = pit.attention_for_asset(fresh_asset("BTC-USD", asset_class="crypto"), saturday)
    assert attention["market_session"] == "regular"
    assert attention["quote_poll_seconds"] <= pit.GLOBAL_PIT_MARKET_LOOP_SECONDS


def test_rate_limits_are_respected_and_duplicate_requests_eliminated():
    assets = [fresh_asset("AAPL", provider="alpha"), fresh_asset("MSFT", provider="alpha"), fresh_asset("NVDA", provider="alpha")]
    lanes = pit.schedule_scanning_lanes(assets, provider_budget={"alpha": 1})
    deferred = [asset for lane in lanes for asset in lane["assets"] if asset.get("deferred_reason")]
    assert deferred
    requests = [
        {"provider": "Yahoo", "symbol": "AAPL", "data_type": "quote", "interval": "1m"},
        {"provider": "Yahoo", "symbol": "aapl", "data_type": "quote", "interval": "1m"},
        {"provider": "Yahoo", "symbol": "MSFT", "data_type": "quote", "interval": "1m"},
    ]
    deduped = pit.deduplicate_provider_requests(requests)
    assert len(deduped) == 2


def test_unsupported_assets_cannot_execute_but_remain_intelligence():
    ranked = pit.rank_global_opportunities([fresh_asset("EURUSD", asset_class="forex", quote_verified=True)])
    assert ranked[0]["paper_execution_supported"] is False
    assert ranked[0]["execution_mode"] == "intelligence_only"
    gate = pit.hard_risk_gate(ranked[0])
    assert gate["allowed"] is False
    assert "unsupported asset class cannot execute" in gate["reasons"]


def test_delayed_or_eod_data_cannot_masquerade_as_live():
    eod = fresh_asset("AAPL", interval="1d", provider_mode="EOD")
    unknown = fresh_asset("MSFT", quote_timestamp="", quote_verified=True)
    assert pit.quote_freshness_label(eod)["label"] == "DELAYED DATA"
    assert pit.quote_freshness_label(unknown)["label"] == "FRESHNESS UNKNOWN"

def test_missing_quote_identity_never_qualifies_for_capital():
    asset = fresh_asset("AAPL", provider_symbol="MSFT")
    ranked = pit.rank_global_opportunities([asset])
    assert ranked[0]["qualified_for_capital"] is False
    gate = pit.hard_risk_gate(ranked[0])
    assert gate["allowed"] is False
    assert "quote symbol identity must match" in gate["reasons"]


def test_missing_tradeable_flag_does_not_default_to_executable():
    asset = fresh_asset("AAPL")
    asset.pop("tradeable")
    ranked = pit.rank_global_opportunities([asset])
    assert ranked[0]["paper_execution_supported"] is False
    assert ranked[0]["qualified_for_capital"] is False


def test_missing_liquidity_never_qualifies_for_capital():
    ranked = pit.rank_global_opportunities([fresh_asset("AAPL", liquidity=0)])
    assert ranked[0]["qualified_for_capital"] is False
    gate = pit.hard_risk_gate(ranked[0])
    assert gate["allowed"] is False
    assert "verified liquidity required" in gate["reasons"]


def core_asset(symbol="CORE", **overrides):
    data = fresh_asset(
        symbol,
        trend_score=82,
        volume_liquidity_score=78,
        catalyst_score=75,
        regime_score=72,
        risk_reward_score=80,
        reward_risk_ratio=2.1,
        confidence=86,
        price=100,
        stop=94,
        target=114,
    )
    data.update(overrides)
    return data


def test_five_strong_core_signals_with_unverified_quote_cannot_trade():
    ranked = pit.rank_global_opportunities([core_asset("NOQUOTE", quote_verified=False)])
    assert ranked[0]["core_signals_supporting"] == 5
    assert ranked[0]["qualified_for_capital"] is False
    gate = pit.hard_risk_gate(ranked[0])
    assert gate["allowed"] is False
    assert "verified quote required" in gate["reasons"]


def test_two_core_signals_with_high_additive_noise_cannot_trade():
    asset = core_asset(
        "TWO",
        trend_score=90,
        volume_liquidity_score=90,
        catalyst_score=20,
        regime_score=20,
        risk_reward_score=20,
        reward_risk_ratio=0.8,
        flow_score=100,
        on_chain_score=100,
        options_flow_score=100,
        confidence=95,
        opportunity_score=99,
    )
    ranked = pit.rank_global_opportunities([asset])
    assert ranked[0]["core_signals_supporting"] == 2
    assert ranked[0]["qualified_for_capital"] is False
    assert ranked[0]["secondary_confirmation_adjustment"] <= pit.MAX_SECONDARY_SCORE_ADJUSTMENT


def test_three_core_signals_verified_confident_and_good_rr_is_eligible():
    ranked = pit.rank_global_opportunities([
        core_asset("THREE", catalyst_score=10, regime_score=45, risk_reward_score=78, reward_risk_ratio=1.8)
    ])
    assert ranked[0]["core_signals_supporting"] == 3
    assert ranked[0]["confidence_score"] >= pit.MIN_CONFIDENCE_TO_TRADE
    assert ranked[0]["qualified_for_capital"] is True


def test_missing_secondary_feeds_do_not_block_core_trade():
    asset = core_asset("NOSECONDARY")
    for key in ("flow_score", "on_chain_score", "options_flow_score", "social_sentiment_score", "news_sentiment_score"):
        asset.pop(key, None)
    ranked = pit.rank_global_opportunities([asset])
    assert ranked[0]["qualified_for_capital"] is True
    assert ranked[0]["secondary_confirmation_adjustment"] == 0


def test_missing_core_data_lowers_confidence():
    complete = pit.strategy_opportunity_score(core_asset("COMPLETE"))
    missing = pit.strategy_opportunity_score(core_asset("MISSING", catalyst_score=0, regime_score=0))
    assert missing["confidence_score"] < complete["confidence_score"]
    assert missing["core_signals_supporting"] < complete["core_signals_supporting"]


def test_stale_catalyst_expires_before_scoring():
    expired = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
    score = pit.strategy_opportunity_score(core_asset("STALECAT", catalyst_score=None, news_score=95, catalyst_expiry=expired))
    assert score["catalyst_score"] == 0


def test_duplicate_technical_indicators_do_not_multiply_momentum_score():
    base = pit.strategy_opportunity_score(core_asset("BASE", trend_score=70))
    duplicate = pit.strategy_opportunity_score(core_asset("DUP", trend_score=70, momentum_score=95, sma_score=100, ema_score=100, macd_score=100))
    assert duplicate["trend_score"] == base["trend_score"]
    assert duplicate["opportunity_score"] <= 100


def test_weak_reward_risk_blocks_execution():
    ranked = pit.rank_global_opportunities([core_asset("WEAKRR", reward_risk_ratio=1.0, risk_reward_score=45)])
    assert ranked[0]["qualified_for_capital"] is False
    gate = pit.hard_risk_gate(ranked[0])
    assert "reward/risk below trade threshold" in gate["reasons"]


def test_opposing_regime_reduces_regime_contribution_and_output_reports_components():
    ranked = pit.rank_global_opportunities([core_asset("REGIME", regime_label="strong_risk_off", regime_score=None)])
    item = ranked[0]
    assert item["regime_score"] < pit.CORE_SIGNAL_SUPPORT_THRESHOLD
    assert item["core_signal_agreement"] == "4/5"
    for key in ("trend_score", "volume_liquidity_score", "catalyst_score", "regime_score", "risk_reward_score"):
        assert key in item



def test_global_pit_liquidity_normalizes_to_percent_scale():
    base = fresh_asset("BASE", liquidity=0, avg_dollar_volume=0)
    liquid = fresh_asset("LIQ", liquidity=1_000_000, avg_dollar_volume=1_000_000)
    assert pit._ranking_score(liquid) > pit._ranking_score(base) + 8


def test_missing_risk_information_is_not_treated_as_low_risk():
    missing = fresh_asset("MISS")
    missing.pop("risk_score")
    missing.pop("risk_level_score", None)
    low_risk = fresh_asset("LOW", risk_score=10)
    assert pit._ranking_score(low_risk) > pit._ranking_score(missing)


def test_portfolio_reaches_deployment_target_only_through_qualified_assets():
    ranked = pit.rank_global_opportunities([
        fresh_asset("AAPL"),
        fresh_asset("OLD", quote_timestamp="", quote_verified=False),
        fresh_asset("GOLD", asset_class="commodity"),
    ])
    plan = pit.capital_deployment_plan(ranked, equity=1_000_000, cash=500_000, positions=[])
    symbols = {item["symbol"] for item in plan["allocations"]}
    assert symbols == {"AAPL"}
    assert plan["qualified_assets_used"] == 1
    assert plan["allocations"][0]["amount"] <= 100_000


def test_opportunity_ranking_spans_multiple_sectors_and_markets():
    assets = [
        fresh_asset("AAPL", sector="Technology", country="United States"),
        fresh_asset("XOM", sector="Energy", country="United States", expected_move_pct=3.5),
        fresh_asset("BTC-USD", asset_class="crypto", sector="Crypto", country="Global", expected_move_pct=5.0),
    ]
    ranked = pit.rank_global_opportunities(assets)
    sectors = {item["sector"] for item in ranked}
    countries = {item.get("country") for item in ranked}
    assert {"Technology", "Energy", "Crypto"}.issubset(sectors)
    assert {"United States", "Global"}.issubset(countries)


def test_rotation_requires_meaningful_advantage():
    weak = {"symbol": "WEAK", "opportunity_score": 70}
    small = {"symbol": "SMALL", "opportunity_score": 71, "qualified_for_capital": True}
    strong = {"symbol": "STRONG", "opportunity_score": 78, "qualified_for_capital": True}
    assert pit.rotation_requires_advantage(weak, small, estimated_cost_pct=1.0)["rotate"] is False
    assert pit.rotation_requires_advantage(weak, strong, estimated_cost_pct=1.0)["rotate"] is True


def test_learning_outcomes_affect_soft_rankings_but_not_hard_gates():
    weights = pit.learning_weights_from_observations([
        {"feature": "edge", "sample_count": 40, "realized_edge_pct": 8},
        {"feature": "risk", "sample_count": 40, "realized_edge_pct": -5},
    ])
    assert weights["edge"] > 0.22
    assert weights["risk"] < 0.10
    stale = fresh_asset("AAPL", quote_verified=False, expected_move_pct=20)
    ranked = pit.rank_global_opportunities([stale], weights)
    gate = pit.hard_risk_gate(ranked[0])
    assert gate["allowed"] is False
    assert "verified quote required" in gate["reasons"]


def test_broker_submission_remains_disabled():
    assert pit.ENABLE_BROKER_SUBMISSION is False


def test_dashboard_activity_labels_match_actual_system_state():
    labels = pit.dashboard_activity_labels({
        "scans_completed_today": 0,
        "research_events_persisted": 0,
        "learning_observations_persisted": 0,
        "qualified_allocations": 0,
        "rotation_candidates": 0,
        "execution_enabled": False,
    })
    assert labels["Scanning"] == "Waiting for scanner activity"
    assert labels["Learning"] == "Waiting for evaluated outcomes"
    assert labels["Trading"] == "Paper execution disabled"
    active = pit.dashboard_activity_labels({
        "scans_completed_today": 4,
        "research_events_persisted": 2,
        "learning_observations_persisted": 1,
        "qualified_allocations": 1,
        "rotation_candidates": 1,
        "execution_enabled": False,
    })
    assert active["Scanning"] == "Active"
    assert active["Researching"] == "Active"
    assert active["Learning"] == "Active"
    assert active["Allocating"] == "Paper planning only"
