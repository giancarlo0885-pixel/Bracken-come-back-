from __future__ import annotations

import json

import pytest

import github_market_intelligence_inbox as inbox


def _packet() -> dict:
    return {
        "schema_version": inbox.SCHEMA_VERSION,
        "brief_id": "2026-10-12",
        "generated_at": "2026-10-12T15:00:00+00:00",
        "developments": [
            {
                "category": "macro_policy",
                "title": "Oil and inflation remain the primary market driver",
                "fact": "Brent traded above $100 per barrel.",
                "inference": "Higher energy costs may pressure long-duration assets.",
                "affected_symbols": ["XLE", "SPY"],
                "sources": [
                    {
                        "provider": "Reuters",
                        "url": "https://www.reuters.com/example",
                    }
                ],
            }
        ],
    }


def _issue(packet: dict | None = None, **overrides) -> dict:
    payload = packet or _packet()
    issue = {
        "title": inbox.DEFAULT_ISSUE_TITLE,
        "state": "open",
        "user": {"login": inbox.DEFAULT_OWNER},
        "updated_at": "2026-10-12T15:05:00Z",
        "body": f"{inbox.BODY_MARKER}\n```json\n{json.dumps(payload)}\n```",
    }
    issue.update(overrides)
    return issue


def _enable(monkeypatch) -> None:
    monkeypatch.setenv("MARKET_BRIEF_GITHUB_ISSUE", "123")
    monkeypatch.setenv("MARKET_BRIEF_GITHUB_REPOSITORY", inbox.DEFAULT_REPOSITORY)
    monkeypatch.setenv("MARKET_BRIEF_GITHUB_OWNER", inbox.DEFAULT_OWNER)


def test_extract_market_brief_packet_requires_contract_marker_and_schema():
    packet = _packet()
    body = f"{inbox.BODY_MARKER}\n```json\n{json.dumps(packet)}\n```"
    assert inbox.extract_market_brief_packet(body) == packet

    with pytest.raises(ValueError, match="marker"):
        inbox.extract_market_brief_packet(f"```json\n{json.dumps(packet)}\n```")

    packet["schema_version"] = "unknown"
    with pytest.raises(ValueError, match="schema"):
        inbox.extract_market_brief_packet(
            f"{inbox.BODY_MARKER}\n```json\n{json.dumps(packet)}\n```"
        )


def test_extract_market_brief_packet_rejects_non_https_source():
    packet = _packet()
    packet["developments"][0]["sources"][0]["url"] = "http://example.test/report"
    with pytest.raises(ValueError, match="HTTPS"):
        inbox.extract_market_brief_packet(
            f"{inbox.BODY_MARKER}\n```json\n{json.dumps(packet)}\n```"
        )


def test_inbox_is_disabled_without_issue_number(monkeypatch):
    monkeypatch.delenv("MARKET_BRIEF_GITHUB_ISSUE", raising=False)
    result = inbox.sync_market_brief_inbox(issue_loader=lambda *_: _issue())
    assert result == {
        "status": "disabled",
        "events_persisted": 0,
        "execution_impact": "NONE",
    }


def test_inbox_rejects_non_owner_and_wrong_title(monkeypatch):
    _enable(monkeypatch)
    monkeypatch.setattr(inbox, "_last_receipt", lambda: {})

    with pytest.raises(ValueError, match="owner"):
        inbox.sync_market_brief_inbox(
            issue_loader=lambda *_: _issue(user={"login": "someone-else"})
        )
    with pytest.raises(ValueError, match="title"):
        inbox.sync_market_brief_inbox(
            issue_loader=lambda *_: _issue(title="Untrusted brief")
        )
    with pytest.raises(ValueError, match="not a pull request"):
        inbox.sync_market_brief_inbox(
            issue_loader=lambda *_: _issue(pull_request={"url": "https://example.test"})
        )


def test_inbox_ingests_once_and_records_receipt(monkeypatch):
    _enable(monkeypatch)
    saved_receipts: list[dict] = []
    ingested_packets: list[dict] = []
    monkeypatch.setattr(inbox, "_last_receipt", lambda: {})
    monkeypatch.setattr(inbox, "_record_receipt", saved_receipts.append)

    def fake_ingest(packet, *, provider):
        ingested_packets.append(packet)
        assert provider == "Monday Market Intelligence"
        return {
            "brief_id": packet["brief_id"],
            "events_persisted": 1,
            "event_keys": ["brief:test"],
            "execution_impact": "NONE",
        }

    monkeypatch.setattr(inbox, "ingest_market_brief", fake_ingest)
    result = inbox.sync_market_brief_inbox(issue_loader=lambda *_: _issue())

    assert result["status"] == "ingested"
    assert result["events_persisted"] == 1
    assert result["execution_impact"] == "NONE"
    assert ingested_packets == [_packet()]
    assert saved_receipts[0]["payload_hash"] == result["payload_hash"]


def test_inbox_skips_unchanged_payload(monkeypatch):
    _enable(monkeypatch)
    packet = _packet()
    payload_hash = inbox._payload_hash(packet)
    monkeypatch.setattr(inbox, "_last_receipt", lambda: {"payload_hash": payload_hash})
    monkeypatch.setattr(
        inbox,
        "ingest_market_brief",
        lambda *_args, **_kwargs: pytest.fail("unchanged packet must not be ingested"),
    )

    result = inbox.sync_market_brief_inbox(issue_loader=lambda *_: _issue(packet))
    assert result["status"] == "no_change"
    assert result["events_persisted"] == 0
    assert result["execution_impact"] == "NONE"
