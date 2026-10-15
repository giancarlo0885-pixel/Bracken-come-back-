import paper_readiness_scorecard as scorecard
from forecasting import active_crypto_model_identity


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


def test_default_scorecard_uses_current_model_identity_without_borrowing_legacy_approval(monkeypatch):
    current = active_crypto_model_identity()
    checked = []
    monkeypatch.setattr(scorecard, "paper_lifecycle_health", lambda market: {"round_trip_proven": True})
    monkeypatch.setattr(scorecard, "shadow_readiness_summary", lambda **kwargs: {"ok": True})

    class Status:
        def __init__(self, value):
            self.value = value

    def status(model, version):
        checked.append((model, version))
        return Status("approved" if version == "v43-selective-transition" else "shadow")

    monkeypatch.setattr(scorecard, "model_status", status)
    result = scorecard.build_paper_readiness_scorecard()
    assert checked == [current]
    assert current[1] != "v43-selective-transition"
    assert result["active_model"]["model_version"] == current[1]
    assert result["checks"]["model_governance_approved"] is False
    assert result["paper_evidence_complete"] is False
    assert result["automatic_live_activation_allowed"] is False


def test_explicit_historical_scorecard_keeps_requested_model_identity(monkeypatch):
    checked = []
    monkeypatch.setattr(scorecard, "paper_lifecycle_health", lambda market: {})
    monkeypatch.setattr(scorecard, "shadow_readiness_summary", lambda **kwargs: {})

    class Shadow:
        value = "shadow"

    def status(model, version):
        checked.append((model, version))
        return Shadow()

    monkeypatch.setattr(scorecard, "model_status", status)
    result = scorecard.build_paper_readiness_scorecard(
        active_model="historical-model", active_model_version="retained-v1",
    )
    assert checked == [("historical-model", "retained-v1")]
    assert result["active_model"]["model_version"] == "retained-v1"
