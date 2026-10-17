from __future__ import annotations

import json
from datetime import datetime, timezone
from email.message import Message
from io import BytesIO
from types import SimpleNamespace
from urllib.error import HTTPError, URLError

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


def _http_error(status, *, headers=None, body=None):
    safe_headers = Message()
    for key, value in (headers or {}).items():
        safe_headers[key] = value
    return HTTPError(
        "https://api.github.com/repos/example/private/issues/1?secret=hidden-token",
        status,
        "hidden-token must never appear in diagnostics",
        safe_headers,
        BytesIO(json.dumps(body or {"message": "Forbidden"}).encode()),
    )


@pytest.mark.parametrize(
    ("status", "headers", "body", "expected"),
    [
        (429, {}, {}, "rate_limited"),
        (403, {"X-RateLimit-Remaining": "0"}, {}, "rate_limited"),
        (403, {"Retry-After": "60"}, {}, "rate_limited"),
        (403, {"X-RateLimit-Remaining": "40"}, {"message": "You have exceeded a secondary rate limit. Please wait."}, "rate_limited"),
        (403, {}, {"message": "API rate limit exceeded for an address."}, "rate_limited"),
        (403, {}, {"message": "You have triggered an abuse detection mechanism."}, "rate_limited"),
        (403, {"X-RateLimit-Remaining": "40"}, {}, "access_forbidden"),
        (401, {"X-RateLimit-Remaining": "0"}, {}, "authentication_failed"),
        (404, {}, {}, "not_found_or_access_denied"),
        (503, {}, {}, "upstream_error"),
        (422, {}, {}, "http_error"),
    ],
)
def test_fetch_issue_preserves_http_status_and_classification(monkeypatch, status, headers, body, expected):
    error = _http_error(status, headers=headers, body=body)
    calls = []

    def unavailable(request, *, timeout):
        calls.append((request.full_url, timeout))
        raise error

    monkeypatch.setattr(inbox, "urlopen", unavailable)
    with pytest.raises(inbox.GitHubInboxHTTPError) as captured:
        inbox._fetch_issue(inbox.DEFAULT_REPOSITORY, 338)
    diagnostics = inbox.market_brief_failure_diagnostics(captured.value)
    assert diagnostics["http_status"] == status
    assert diagnostics["failure_class"] == expected
    assert captured.value.__cause__ is error
    assert error.fp.closed
    assert len(calls) == 1  # Diagnostic capture must not add a retry.
    assert "hidden-token" not in str(captured.value)
    assert "hidden-token" not in json.dumps(diagnostics)


def test_http_diagnostics_allow_only_safe_headers(monkeypatch):
    error = _http_error(429, headers={
        "X-RateLimit-Limit": "60",
        "X-RateLimit-Remaining": "0",
        "X-RateLimit-Reset": "1792032692",
        "Retry-After": "12.5",
        "X-GitHub-Request-Id": "A123:BC45:6789:D012",
        "Authorization": "Bearer hidden-token",
        "Set-Cookie": "hidden-token",
    }, body={"message": "hidden-token"})
    monkeypatch.setattr(inbox, "urlopen", lambda *_args, **_kwargs: (_ for _ in ()).throw(error))
    with pytest.raises(inbox.GitHubInboxHTTPError) as captured:
        inbox._fetch_issue(inbox.DEFAULT_REPOSITORY, 338)
    diagnostics = inbox.market_brief_failure_diagnostics(captured.value)
    assert diagnostics == {
        "reason": "GitHubInboxHTTPError", "http_status": 429, "failure_class": "rate_limited",
        "rate_limit_limit": 60, "rate_limit_remaining": 0,
        "rate_limit_reset_epoch": 1792032692, "retry_after_seconds": 12.5,
        "github_request_id": "A123:BC45:6789:D012",
    }
    assert "hidden-token" not in json.dumps(diagnostics)


def test_malformed_headers_do_not_claim_rate_limit_or_leak_text():
    error = _http_error(403, headers={
        "X-RateLimit-Limit": "9999999999999999999999999999999",
        "X-RateLimit-Remaining": "0 hidden-token",
        "X-RateLimit-Reset": "NaN",
        "Retry-After": "Infinity",
        "X-GitHub-Request-Id": "hidden-token?secret=123",
    })
    diagnostics = inbox.market_brief_failure_diagnostics(inbox._github_http_error(error))
    assert diagnostics["failure_class"] == "access_forbidden"
    assert all(diagnostics[key] is None for key in (
        "rate_limit_limit", "rate_limit_remaining", "rate_limit_reset_epoch",
        "retry_after_seconds", "github_request_id",
    ))
    assert "hidden-token" not in json.dumps(diagnostics)


def test_retry_after_http_date_is_converted_to_safe_numeric_seconds(monkeypatch):
    class FrozenDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime(2026, 10, 15, 12, tzinfo=timezone.utc)

    monkeypatch.setattr(inbox, "datetime", FrozenDatetime)
    error = _http_error(403, headers={"Retry-After": "Thu, 15 Oct 2026 12:01:00 GMT"})
    diagnostics = inbox.market_brief_failure_diagnostics(inbox._github_http_error(error))
    assert diagnostics["failure_class"] == "rate_limited"
    assert diagnostics["retry_after_seconds"] == 60.0


def test_invalid_error_json_does_not_mask_original_http_status():
    error = HTTPError(
        "https://api.github.com/", 403, "Forbidden", Message(),
        BytesIO(b"not JSON; hidden-token"),
    )
    result = inbox._github_http_error(error)
    assert result.http_status == 403
    assert result.diagnostics["failure_class"] == "access_forbidden"
    assert "hidden-token" not in str(result)


def test_successful_http_fetch_keeps_request_and_size_contract(monkeypatch):
    payload = _issue()
    response = BytesIO(json.dumps(payload).encode())
    calls = []

    def available(request, *, timeout):
        calls.append((request, timeout))
        return response

    monkeypatch.setattr(inbox, "urlopen", available)
    assert inbox._fetch_issue(inbox.DEFAULT_REPOSITORY, 338) == payload
    assert len(calls) == 1
    request, timeout = calls[0]
    assert request.full_url == f"https://api.github.com/repos/{inbox.DEFAULT_REPOSITORY}/issues/338"
    assert request.get_method() == "GET"
    assert not request.has_header("Authorization")
    assert timeout == 12
    assert response.closed


def test_unchanged_brief_logs_actual_http_receipt_without_reingestion(monkeypatch, caplog):
    _enable(monkeypatch)
    packet = _packet()
    packet["developments"][0]["fact"] = "hidden-token must not enter transport logs"
    receipt = {"brief_id": packet["brief_id"], "payload_hash": inbox._payload_hash(packet)}
    monkeypatch.setattr(inbox, "_last_receipt", lambda: dict(receipt))
    monkeypatch.setattr(inbox, "_record_receipt", lambda *_: pytest.fail("unchanged evidence must be preserved"))
    monkeypatch.setattr(inbox, "ingest_market_brief", lambda *_args, **_kwargs: pytest.fail("unchanged brief cannot be reingested"))
    response = BytesIO(json.dumps(_issue(packet)).encode())
    response.status = 200
    response.headers = Message()
    for key, value in {
        "X-RateLimit-Limit": "60", "X-RateLimit-Remaining": "44",
        "X-RateLimit-Reset": "1792032692", "X-GitHub-Request-Id": "ABCD:1234:EF56",
        "Authorization": "Bearer hidden-token", "Set-Cookie": "hidden-token",
    }.items():
        response.headers[key] = value
    requests = []

    def available(request, *, timeout):
        requests.append(request)
        return response

    monkeypatch.setattr(inbox, "urlopen", available)
    with caplog.at_level("INFO", logger="github-market-intelligence-inbox"):
        result = inbox.sync_market_brief_inbox()
    assert result["status"] == "no_change"
    assert result["payload_hash"] == receipt["payload_hash"]
    assert result["events_persisted"] == 0
    assert len(requests) == 1
    assert "status=RECEIVED | http_status=200" in caplog.text
    assert "rate_limit_limit=60 | rate_limit_remaining=44 | rate_limit_reset_epoch=1792032692" in caplog.text
    assert "github_request_id=ABCD:1234:EF56" in caplog.text
    assert "execution_impact=NONE" in caplog.text
    assert "hidden-token" not in caplog.text
    assert "api.github.com" not in caplog.text
    assert "INGESTED" not in caplog.text
    assert inbox._last_receipt() == receipt


@pytest.mark.parametrize("headers", [None, {
    "X-RateLimit-Limit": "hidden-token", "X-RateLimit-Remaining": "NaN",
    "X-RateLimit-Reset": "Infinity", "X-GitHub-Request-Id": "hidden-token?secret=123",
}])
def test_success_receipt_does_not_invent_missing_or_malformed_quota(monkeypatch, caplog, headers):
    response = BytesIO(json.dumps(_issue()).encode())
    response.status = 200
    response.headers = headers
    monkeypatch.setattr(inbox, "urlopen", lambda *_args, **_kwargs: response)
    with caplog.at_level("INFO", logger="github-market-intelligence-inbox"):
        inbox._fetch_issue(inbox.DEFAULT_REPOSITORY, 338)
    assert "http_status=200" in caplog.text
    assert "rate_limit_limit=None | rate_limit_remaining=None | rate_limit_reset_epoch=None" in caplog.text
    assert "github_request_id=None" in caplog.text
    assert "hidden-token" not in caplog.text


def test_success_receipt_does_not_claim_packet_acceptance(monkeypatch, caplog):
    response = BytesIO(b"not JSON; hidden-token")
    response.status = 200
    response.headers = None
    monkeypatch.setattr(inbox, "urlopen", lambda *_args, **_kwargs: response)
    with caplog.at_level("INFO", logger="github-market-intelligence-inbox"), pytest.raises(ValueError):
        inbox._fetch_issue(inbox.DEFAULT_REPOSITORY, 338)
    assert "status=RECEIVED | http_status=200" in caplog.text
    assert "hidden-token" not in caplog.text


def test_secondary_limit_body_inspection_is_bounded():
    class ErrorBody(BytesIO):
        sizes = []

        def read(self, size=-1):
            self.sizes.append(size)
            return super().read(size)

    body = ErrorBody(b'{"message":"You have exceeded a secondary rate limit.' + b"x" * inbox.MAX_ERROR_BYTES)
    error = HTTPError("https://api.github.com/", 403, "Forbidden", Message(), body)
    result = inbox._github_http_error(error)
    assert result.diagnostics["failure_class"] == "access_forbidden"
    assert body.sizes == [inbox.MAX_ERROR_BYTES + 1]


@pytest.mark.parametrize("error", [TimeoutError("hidden-token"), URLError("hidden-token")])
def test_transport_failure_status_is_unknown_and_receipt_is_preserved(monkeypatch, error):
    _enable(monkeypatch)
    receipt = {"brief_id": "2026-10-12", "payload_hash": "durable-existing-hash"}
    monkeypatch.setattr(inbox, "_last_receipt", lambda: dict(receipt))
    monkeypatch.setattr(inbox, "_record_receipt", lambda *_: pytest.fail("fetch failure must not overwrite evidence"))
    monkeypatch.setattr(inbox, "ingest_market_brief", lambda *_args, **_kwargs: pytest.fail("fetch failure cannot ingest"))
    monkeypatch.setattr(inbox, "urlopen", lambda *_args, **_kwargs: (_ for _ in ()).throw(error))
    with pytest.raises(type(error)):
        inbox.sync_market_brief_inbox()
    diagnostics = inbox.market_brief_failure_diagnostics(error)
    assert diagnostics["http_status"] is None
    assert diagnostics["failure_class"] == "transport_error"
    assert "hidden-token" not in json.dumps(diagnostics)
    assert inbox._last_receipt() == receipt


@pytest.mark.parametrize("status", [401, 403, 404, 429, 503])
def test_http_failure_preserves_durable_receipt(monkeypatch, status):
    _enable(monkeypatch)
    monkeypatch.setattr(inbox, "_record_receipt", lambda *_: pytest.fail("HTTP failure must not overwrite evidence"))
    monkeypatch.setattr(inbox, "ingest_market_brief", lambda *_args, **_kwargs: pytest.fail("HTTP failure cannot ingest"))
    error = _http_error(status)
    monkeypatch.setattr(inbox, "urlopen", lambda *_args, **_kwargs: (_ for _ in ()).throw(error))
    with pytest.raises(inbox.GitHubInboxHTTPError):
        inbox.sync_market_brief_inbox()


@pytest.mark.parametrize("status", [403, 429, 401, 404])
def test_worker_keeps_collecting_and_logs_http_classification(monkeypatch, caplog, status):
    import market_worker as worker

    error = inbox._github_http_error(_http_error(status, headers={"X-RateLimit-Remaining": "0"}))
    monkeypatch.setattr(worker, "sync_market_brief_inbox", lambda: (_ for _ in ()).throw(error))
    monkeypatch.setattr(worker, "stop_event", SimpleNamespace(is_set=lambda: False))
    records = [{"title": "regular collector remains available"}]
    collected = []
    ingested = []

    def collect():
        collected.append(True)
        return {"macro": SimpleNamespace(available=True, provider="existing-provider", records=records)}

    monkeypatch.setattr(worker, "collect_all", collect)
    monkeypatch.setattr(worker, "ingest_monitor_record", lambda *args: ingested.append(args))
    with caplog.at_level("WARNING", logger="market-worker"):
        worker._collect_stock_intelligence()
    assert collected == [True]
    assert ingested == [("macro", "existing-provider", records[0])]
    assert f"http_status={status}" in caplog.text
    assert f"failure_class={error.diagnostics['failure_class']}" in caplog.text
    assert "execution_impact=NONE" in caplog.text
    assert "hidden-token" not in caplog.text
