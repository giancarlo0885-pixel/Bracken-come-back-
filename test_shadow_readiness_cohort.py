import shadow_broker


def test_shadow_readiness_uses_passive_execution_evidence_only(monkeypatch):
    captured = {}

    def fake_row(query, params=()):
        captured["query"] = query
        return {
            "evaluated": 120,
            "open_count": 3,
            "avg_outcome": 0.1,
            "avg_paper_error": 0.2,
            "p95_paper_error": 0.8,
        }

    monkeypatch.setattr(shadow_broker, "row", fake_row)
    result = shadow_broker.shadow_readiness_summary(minimum_samples=100, maximum_paper_error_pct=1.0)

    assert "payload->>'evidence_kind'='passive_paper_execution_model'" in captured["query"]
    assert result["ok"] is True
    assert result["status"] == "PASS"
    assert result["evaluated_samples"] == 120
    assert result["p95_paper_vs_broker_error_pct"] == 0.8
