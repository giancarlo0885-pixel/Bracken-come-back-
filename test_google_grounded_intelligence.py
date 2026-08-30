from __future__ import annotations

import api_manager
import news_intelligence as ni


def test_gemini_api_key_accepts_google_api_key_alias(monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.setenv("GOOGLE_API_KEY", "google-key")
    assert api_manager.resolve_api_key("GEMINI_API_KEY") == "google-key"


def test_grounded_web_sources_extracts_attributable_titles_and_urls(monkeypatch):
    monkeypatch.setattr(ni, "GEMINI_MAX_GROUNDED_SOURCES", 8)
    payload = {
        "candidates": [
            {
                "groundingMetadata": {
                    "groundingChunks": [
                        {"web": {"title": "Company files 8-K", "uri": "https://example.com/filing"}},
                        {"web": {"title": "Company files 8-K", "uri": "https://example.com/filing"}},
                        {"web": {"title": "Reuters market update", "uri": "https://example.com/reuters"}},
                    ]
                }
            }
        ]
    }
    headlines, citations = ni._grounded_web_sources(payload)
    assert headlines == ["Company files 8-K", "Reuters market update"]
    assert citations == ["https://example.com/filing", "https://example.com/reuters"]


class _Response:
    def __init__(self, payload, *, status_code=200, text=""):
        self._payload = payload
        self.status_code = status_code
        self.ok = 200 <= status_code < 300
        self.text = text

    def json(self):
        return self._payload


def test_fetch_gemini_grounded_uses_google_search_and_source_titles(monkeypatch):
    captured = {}
    payload = {
        "candidates": [
            {
                "content": {"parts": [{"text": "Generated synthesis is not used as a headline."}]},
                "groundingMetadata": {
                    "groundingChunks": [
                        {"web": {"title": "SEC filing source", "uri": "https://sec.example/filing"}},
                        {"web": {"title": "Earnings release source", "uri": "https://issuer.example/earnings"}},
                    ]
                },
            }
        ]
    }

    def fake_post(url, *, headers, json, timeout):
        captured["url"] = url
        captured["headers"] = headers
        captured["json"] = json
        captured["timeout"] = timeout
        return _Response(payload)

    monkeypatch.setattr(ni.requests, "post", fake_post)
    result = ni._fetch_gemini_grounded("Example Corp EXM", "secret")

    assert captured["headers"]["x-goog-api-key"] == "secret"
    assert captured["json"]["tools"] == [{"google_search": {}}]
    assert "gemini-" in captured["url"]
    assert result.source == "Google Gemini Grounded Search"
    assert result.headlines == ["SEC filing source", "Earnings release source"]
    assert result.citations == ["https://sec.example/filing", "https://issuer.example/earnings"]
    assert "Generated synthesis" not in " ".join(result.headlines)


def test_news_pipeline_falls_back_when_google_grounding_fails(monkeypatch):
    monkeypatch.setattr(ni, "GOOGLE_GROUNDED_INTELLIGENCE_ENABLED", True)
    monkeypatch.setattr(ni, "cache_get", lambda _key: None)
    monkeypatch.setattr(ni, "set_value", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(ni, "_get_gemini_key", lambda: "gemini-key")
    monkeypatch.setattr(ni, "_gemini_budget_allows_request", lambda: True)
    monkeypatch.setattr(ni, "_fetch_gemini_grounded", lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("grounding unavailable")))
    monkeypatch.setattr(ni, "_get_newsapi_key", lambda: "news-key")
    monkeypatch.setattr(ni, "_budget_allows_request", lambda: True)
    expected = ni.NewsResult(0.25, ["Fallback headline"], "NewsAPI", citations=["https://example.com/news"])
    monkeypatch.setattr(ni, "_fetch_newsapi", lambda *_args, **_kwargs: expected)

    result = ni.get_news_sentiment("Example Corp EXM")
    assert result is expected


def test_news_pipeline_skips_grounded_provider_without_key(monkeypatch):
    monkeypatch.setattr(ni, "GOOGLE_GROUNDED_INTELLIGENCE_ENABLED", True)
    monkeypatch.setattr(ni, "cache_get", lambda _key: None)
    monkeypatch.setattr(ni, "set_value", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(ni, "_get_gemini_key", lambda: "")
    monkeypatch.setattr(ni, "_get_newsapi_key", lambda: "")
    expected = ni.NewsResult(0.0, ["RSS headline"], "Google News RSS", citations=["https://example.com/rss"])
    monkeypatch.setattr(ni, "_fetch_google_news", lambda *_args, **_kwargs: expected)

    result = ni.get_news_sentiment("Example Corp EXM")
    assert result is expected
