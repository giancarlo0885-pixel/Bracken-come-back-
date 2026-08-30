from __future__ import annotations

from types import SimpleNamespace

import ai_oracle
import gemini_oracle


def test_gemini_disabled_without_configuration(monkeypatch):
    monkeypatch.setattr(gemini_oracle, "ENABLE_GEMINI", False)
    monkeypatch.setattr(gemini_oracle, "GEMINI_API_KEY", "")

    result = gemini_oracle.test_gemini_connection()

    assert result["available"] is False
    assert result["status"] == "disabled"
    assert result["api_key_configured"] is False


def test_gemini_connection_uses_interactions_api(monkeypatch):
    calls = []

    class FakeInteractions:
        def create(self, **kwargs):
            calls.append(kwargs)
            return SimpleNamespace(output_text="GARIBALDI GEMINI ONLINE")

    fake_client = SimpleNamespace(interactions=FakeInteractions())
    monkeypatch.setattr(gemini_oracle, "ENABLE_GEMINI", True)
    monkeypatch.setattr(gemini_oracle, "GEMINI_API_KEY", "test-key")
    monkeypatch.setattr(gemini_oracle, "get_client", lambda: fake_client)

    result = gemini_oracle.test_gemini_connection()

    assert result["available"] is True
    assert result["status"] == "online"
    assert result["message"] == "GARIBALDI GEMINI ONLINE"
    assert calls[0]["model"] == gemini_oracle.GEMINI_MODEL
    assert calls[0]["input"] == "Reply exactly: GARIBALDI GEMINI ONLINE"


def test_ai_auto_mode_falls_back_to_gemini(monkeypatch):
    monkeypatch.setattr(ai_oracle, "AI_PROVIDER_MODE", "auto")
    monkeypatch.setattr(ai_oracle, "openai_available", lambda: False)
    monkeypatch.setattr(ai_oracle, "gemini_available", lambda: True)
    monkeypatch.setattr(
        ai_oracle,
        "call_gemini",
        lambda **kwargs: "Gemini second brain online",
    )

    answer = ai_oracle.answer_market_question(
        "What is the strongest opportunity?",
        {"opportunities": [{"symbol": "AAPL", "price": 100.0}]},
    )

    assert answer == "Gemini second brain online"


def test_gemini_prompt_cannot_replace_verified_market_data(monkeypatch):
    captured = {}

    class FakeInteractions:
        def create(self, **kwargs):
            captured.update(kwargs)
            return SimpleNamespace(output_text="Analysis only")

    fake_client = SimpleNamespace(interactions=FakeInteractions())
    monkeypatch.setattr(gemini_oracle, "ENABLE_GEMINI", True)
    monkeypatch.setattr(gemini_oracle, "GEMINI_API_KEY", "test-key")
    monkeypatch.setattr(gemini_oracle, "get_client", lambda: fake_client)

    result = gemini_oracle.answer_market_question(
        "Should Oracle trust this price?",
        {"symbol": "MSFT", "price": 500.0, "provider_verified": True},
    )

    assert result == "Analysis only"
    prompt = captured["input"]
    assert "verified market-data providers as authoritative" in prompt
    assert '"price":500.0' in prompt
