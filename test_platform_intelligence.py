from platform_intelligence import classify_regime, build_snapshot


def test_regime_constructive():
    regime, score, breadth = classify_regime([
        {"score": 75, "action": "BUY"},
        {"score": 65, "action": "BUY"},
        {"score": 55, "action": "HOLD"},
    ])
    assert regime in {"Constructive", "Risk-On"}
    assert score > 55
    assert breadth > 0


def test_snapshot_is_safe_with_empty_data():
    snap, reasons = build_snapshot(
        signals=[], opportunities=[], positions=[], portfolio_metrics=[], alerts=[], diagnostics=[], workers=[],
        worker_online_fn=lambda _: False,
    )
    assert snap.regime == "Insufficient Data"
    assert snap.workers_expected == 2
    assert reasons
