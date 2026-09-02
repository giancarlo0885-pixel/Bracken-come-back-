import paper_readiness_scorecard as scorecard


def test_scorecard_never_auto_arms_live(monkeypatch):
    monkeypatch.setenv("EXECUTION_MODE", "paper")
    monkeypatch.setenv("LIVE_TRADING_ARMED", "false")
    monkeypatch.setenv("ENABLE_BROKER_SUBMISSION", "false")
    monkeypatch.setattr(scorecard, "paper_lifecycle_health", lambda market: {"ok": True, "round_trip_proven": True})
    monkeypatch.setattr(scorecard, "shadow_readiness_summary", lambda **kwargs: {"ok": True, "status": "PASS"})

    class Approved:
        value = "approved"

    monkeypatch.setattr(scorecard, "model_status", lambda *args: Approved())
    result = scorecard.build_paper_readiness_scorecard()
    assert result["status"] == "HUMAN_REVIEW_READY"
    assert result["paper_evidence_complete"] is True
    assert result["human_authorization_required"] is True
    assert result["automatic_live_activation_allowed"] is False


def test_scorecard_fails_closed_when_live_is_armed(monkeypatch):
    monkeypatch.setenv("EXECUTION_MODE", "paper")
    monkeypatch.setenv("LIVE_TRADING_ARMED", "true")
    monkeypatch.setenv("ENABLE_BROKER_SUBMISSION", "false")
    monkeypatch.setattr(scorecard, "paper_lifecycle_health", lambda market: {"ok": True, "round_trip_proven": True})
    monkeypatch.setattr(scorecard, "shadow_readiness_summary", lambda **kwargs: {"ok": True, "status": "PASS"})

    class Approved:
        value = "approved"

    monkeypatch.setattr(scorecard, "model_status", lambda *args: Approved())
    result = scorecard.build_paper_readiness_scorecard()
    assert result["status"] == "COLLECTING_EVIDENCE"
    assert result["checks"]["live_trading_disarmed"] is False
