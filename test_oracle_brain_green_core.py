from oracle_brain_feedback import green_core_profile


def _row(pnl, momentum, volume, rsi):
    return {
        "net_pnl": pnl,
        "feature_snapshot": {
            "momentum_5d": momentum,
            "volume_ratio": volume,
            "rsi_14": rsi,
        },
    }


def test_green_core_rewards_current_features_that_resemble_winners():
    rows = []
    for i in range(20):
        rows.append(_row(1.0, 0.030 + i * 0.0002, 1.55 + i * 0.01, 58 + i * 0.1))
    for i in range(20):
        rows.append(_row(-1.0, -0.020 - i * 0.0002, 0.75 - i * 0.005, 39 - i * 0.1))
    result = green_core_profile(
        rows,
        {"momentum_5d": 0.04, "volume_ratio": 1.8, "rsi_14": 61},
        min_samples=30,
    )
    assert result["status"] == "ok"
    assert result["adjustment"] > 0
    assert result["green_samples"] == 20
    assert all(item["matches_green"] for item in result["features"])


def test_green_core_penalizes_features_that_resemble_losers():
    rows = [_row(1.0, 0.03, 1.6, 60) for _ in range(20)]
    rows += [_row(-1.0, -0.03, 0.7, 38) for _ in range(20)]
    result = green_core_profile(
        rows,
        {"momentum_5d": -0.04, "volume_ratio": 0.6, "rsi_14": 35},
        min_samples=30,
    )
    assert result["status"] == "ok"
    assert result["adjustment"] < 0


def test_green_core_refuses_small_or_one_sided_samples():
    rows = [_row(1.0, 0.03, 1.5, 60) for _ in range(25)]
    rows += [_row(-1.0, -0.02, 0.8, 40) for _ in range(4)]
    result = green_core_profile(
        rows,
        {"momentum_5d": 0.03, "volume_ratio": 1.5, "rsi_14": 60},
        min_samples=30,
    )
    assert result["status"] == "insufficient"
    assert result["adjustment"] == 0.0


def test_green_core_ignores_missing_entry_features():
    rows = [{"net_pnl": 1.0, "feature_snapshot": {}} for _ in range(20)]
    rows += [{"net_pnl": -1.0, "feature_snapshot": {}} for _ in range(20)]
    result = green_core_profile(rows, {"momentum_5d": 0.03}, min_samples=30)
    assert result["status"] == "no_separation"
    assert result["adjustment"] == 0.0
