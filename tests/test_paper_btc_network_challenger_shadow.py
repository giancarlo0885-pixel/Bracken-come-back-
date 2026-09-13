from paper_btc_network_challenger_shadow import classify_cohort, mature_negative, mean_ci95


def test_classify_cohort_requires_observed_network_source():
    assert classify_cohort({}) == "network_unavailable"
    assert classify_cohort({"btc_network_security_score": 0.8}) == "network_unavailable"


def test_classify_cohort_combines_network_and_technical_entry_evidence():
    cohort = classify_cohort({
        "btc_network_source": "mempool.space",
        "btc_network_security_score": 0.8,
        "btc_network_activity_score": 0.6,
        "btc_network_fee_pressure": 0.1,
        "btc_network_mempool_pressure": 0.1,
        "ta_consensus": 0.7,
        "schwager_setup_score": 0.5,
        "dip_rebound_score": 0.6,
    })
    assert cohort == "network_favorable__technical_bullish"


def test_mature_negative_requires_depth_and_upper_confidence_bound_below_zero():
    losing = [-0.08, -0.05, -0.09, -0.04, -0.07] * 6
    mean, low, high = mean_ci95(losing)
    assert mean < 0 and high < 0
    assert mature_negative(losing, minimum_samples=25)
    assert not mature_negative(losing[:10], minimum_samples=25)


def test_mixed_outcomes_are_not_declared_mature_negative():
    mixed = [-0.20, 0.19, -0.15, 0.16, -0.10, 0.11] * 5
    assert not mature_negative(mixed, minimum_samples=25)
