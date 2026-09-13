from __future__ import annotations

from dataclasses import dataclass, field
from types import SimpleNamespace

import event_opportunity_runtime as runtime


@dataclass
class FakeNews:
    sentiment: float
    headlines: list[str]
    source: str
    message: str = ""
    citations: list[str] = field(default_factory=list)


def _worker():
    worker = SimpleNamespace()
    worker.WATCHLISTS = {"cash": {"AAPL": "Apple"}, "crypto": {}}
    worker.log = SimpleNamespace(info=lambda *args, **kwargs: None)
    worker.scan_market = lambda market: {"market": market, "watchlist": dict(worker.WATCHLISTS.get(market, {}))}
    worker.analyze_market = lambda symbol, history, news_sentiment: SimpleNamespace(symbol=symbol, reason="base")
    worker.get_news_sentiment = lambda query, priority=True: FakeNews(
        sentiment=0.1,
        headlines=["ordinary symbol news"],
        source="Provider",
        message="provider result",
        citations=["https://provider.test/item"],
    )
    return worker


def test_runtime_adds_only_verified_event_watchlist(monkeypatch):
    worker = _worker()
    monkeypatch.setattr(runtime, "_INSTALLED", False)
    monkeypatch.setattr(runtime, "active_event_watchlist", lambda: {"EXM": "Example Energy"})
    monkeypatch.setattr(runtime, "event_context_for_symbol", lambda symbol: {"score": 0.0, "events": [], "headlines": []})

    runtime.install_event_opportunity_runtime(worker)
    result = worker.scan_market("cash")

    assert worker.EVENT_OPPORTUNITY_RUNTIME_ACTIVE is True
    assert result["watchlist"]["EXM"] == "Example Energy"


def test_runtime_attaches_event_evidence_to_signal_and_news(monkeypatch):
    worker = _worker()
    monkeypatch.setattr(runtime, "_INSTALLED", False)
    monkeypatch.setattr(runtime, "active_event_watchlist", lambda: {})
    monkeypatch.setattr(
        runtime,
        "event_context_for_symbol",
        lambda symbol: {
            "score": 88.0 if symbol == "EXM" else 0.0,
            "headlines": ["Example Energy wins major contract"],
            "events": [{"url": "https://event.test/exm", "title": "Example Energy wins major contract"}],
        },
    )

    runtime.install_event_opportunity_runtime(worker)
    signal = worker.analyze_market("EXM", object(), 0.1)
    news = worker.get_news_sentiment("Example Energy EXM", priority=True)

    assert signal.external_catalyst_score == 88.0
    assert signal.event_catalyst_score == 88.0
    assert "Event radar catalyst 88/100" in signal.reason
    assert news.headlines[0] == "Example Energy wins major contract"
    assert "https://event.test/exm" in news.citations
    assert news.source.endswith("+ Event Radar")
