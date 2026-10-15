"""Data-corrected forecasts start shadow-only; historical failures stay separate."""
from copy import deepcopy
from datetime import datetime, timezone

import capital_model_governance as governance
import forecasting
import model_registry


OLD="v43-selective-transition"
NEW="v43-selective-transition-data-v2"
MODEL="crypto selective sign transition"


def test_corrected_identity_starts_shadow_and_retains_legacy_registration(monkeypatch):
    import database
    monkeypatch.setattr(database,"row",lambda *args,**kwargs: None)
    assert forecasting.active_crypto_model_identity() == (MODEL,NEW)
    assert (MODEL,NEW) in model_registry._REGISTRY
    assert (MODEL,OLD) in model_registry._REGISTRY
    assert model_registry.model_status(MODEL,NEW) is model_registry.ModelStatus.SHADOW
    assert not model_registry.model_can_approve_execution(MODEL,NEW)


def test_legacy_negative_evidence_is_retained_but_never_relabelled_as_corrected_validation():
    now=datetime.now(timezone.utc)
    rows=[dict(model=MODEL,model_version=OLD,run_id="legacy:"+symbol,symbol=symbol,
        created_at=now.isoformat(),status="INSUFFICIENT_OR_FAILED_EVIDENCE",
        metrics=dict(brier_skill_score=-.1,expected_calibration_error=.1,directional_accuracy=.48,
                     beats_all_baselines=False),
        leakage_checks=dict(strict_ordering=True,future_mutation_probe=dict(ok=True)))
        for symbol in ("BTC-USD","ETH-USD","SOL-USD")]
    retained=deepcopy(rows)
    old=governance.assess_model_evidence(MODEL,OLD,rows,current_status="degraded",now=now)
    new=governance.assess_model_evidence(MODEL,NEW,rows,current_status="shadow",now=now)
    assert rows == retained
    assert old.recent_run_count == 3
    assert old.evidence["failed_run_count"] == 3
    assert old.eligible_for_approval is False
    assert old.recommended_status == "degraded"
    assert new.recent_run_count == new.passing_run_count == 0
    assert new.eligible_for_approval is False
    assert new.recommended_status == "shadow"
