from __future__ import annotations

import capital_data_health as health


def test_rejected_divergence_is_safe_guard_evidence(monkeypatch) -> None:
    monkeypatch.setattr(
        health,
        "row",
        lambda *args, **kwargs: {
            "total": 12,
            "confirmed": 10,
            "rejected": 2,
            "unsafe_confirmed_divergent": 0,
            "safely_rejected_divergent": 2,
        },
    )
    result = health.quote_integrity_summary(maximum_divergence_pct=1.0)
    assert result["ok"] is True
    assert result["status"] == "PASS"
    assert result["divergent"] == 0
    assert result["safely_rejected_divergent"] == 2


def test_confirmed_divergence_remains_fail_closed(monkeypatch) -> None:
    monkeypatch.setattr(
        health,
        "row",
        lambda *args, **kwargs: {
            "total": 12,
            "confirmed": 11,
            "rejected": 1,
            "unsafe_confirmed_divergent": 1,
            "safely_rejected_divergent": 1,
        },
    )
    result = health.quote_integrity_summary(maximum_divergence_pct=1.0)
    assert result["ok"] is False
    assert result["status"] == "FAIL_CLOSED"
    assert result["divergent"] == 1


def test_no_confirmed_evidence_never_passes(monkeypatch) -> None:
    monkeypatch.setattr(
        health,
        "row",
        lambda *args, **kwargs: {
            "total": 4,
            "confirmed": 0,
            "rejected": 4,
            "unsafe_confirmed_divergent": 0,
            "safely_rejected_divergent": 4,
        },
    )
    result = health.quote_integrity_summary()
    assert result["ok"] is False
    assert result["status"] == "INSUFFICIENT_EVIDENCE"
