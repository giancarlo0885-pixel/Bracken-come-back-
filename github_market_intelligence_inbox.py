from __future__ import annotations

"""Read one owner-controlled GitHub issue into Oracle's intelligence bridge.

The scheduled Monday brief is written to a fixed issue as a structured JSON
packet.  The stock worker polls that issue during its existing intelligence
cycle, validates the repository owner and schema, deduplicates the payload in
durable Brain state, and hands the facts to ``ingest_market_brief``.

This module is research transport only.  It cannot create orders, change
position size, approve Council decisions, or arm live trading.
"""

from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
import hashlib
import json
import logging
import math
import os
import re
from typing import Any, Callable
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen

from database import connect
from market_intelligence_bridge import ingest_market_brief


SCHEMA_VERSION = "oracle-market-intelligence-v1"
BODY_MARKER = f"<!-- {SCHEMA_VERSION} -->"
PIPELINE_KEY = "github_market_brief_inbox"
DEFAULT_REPOSITORY = "giancarlo0885-pixel/Bracken-come-back-"
DEFAULT_OWNER = "giancarlo0885-pixel"
DEFAULT_ISSUE_TITLE = "Oracle Brain — Monday Market Intelligence Inbox"
MAX_DEVELOPMENTS = 32
MAX_RESPONSE_BYTES = 256_000
MAX_ERROR_BYTES = 4096
_REPOSITORY_RE = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
_JSON_FENCE_RE = re.compile(r"```json\s*(\{.*?\})\s*```", re.IGNORECASE | re.DOTALL)
log = logging.getLogger("github-market-intelligence-inbox")


class GitHubInboxHTTPError(RuntimeError):
    """A GitHub HTTP failure with an allowlist of safe diagnostic fields."""

    def __init__(self, diagnostics: dict[str, Any]) -> None:
        self.diagnostics = dict(diagnostics)
        self.http_status = diagnostics["http_status"]
        super().__init__(
            f"GitHub inbox HTTP {self.http_status}: {diagnostics['failure_class']}"
        )


def _numeric_header(headers: Any, name: str) -> int | None:
    raw = str(headers.get(name, "") or "").strip() if headers else ""
    if not re.fullmatch(r"[0-9]{1,19}", raw):
        return None
    value = int(raw)
    return value if value <= 2**63 - 1 else None


def _retry_after_seconds(headers: Any) -> float | None:
    raw = str(headers.get("Retry-After", "") or "").strip() if headers else ""
    if re.fullmatch(r"[0-9]{1,10}(?:\.[0-9]{1,3})?", raw):
        return float(raw)
    try:
        retry_at = parsedate_to_datetime(raw)
        if retry_at.tzinfo is None:
            return None
        seconds = (retry_at - datetime.now(timezone.utc)).total_seconds()
        return max(0.0, seconds) if math.isfinite(seconds) else None
    except (TypeError, ValueError, OverflowError):
        return None


def _github_response_diagnostics(status: int | None, headers: Any) -> dict[str, Any]:
    status = status if type(status) is int and 100 <= status <= 599 else None
    request_id = str(headers.get("X-GitHub-Request-Id", "") or "") if headers else ""
    if not re.fullmatch(r"[A-Za-z0-9:-]{1,100}", request_id):
        request_id = None
    return {
        "http_status": status,
        "rate_limit_limit": _numeric_header(headers, "X-RateLimit-Limit"),
        "rate_limit_remaining": _numeric_header(headers, "X-RateLimit-Remaining"),
        "rate_limit_reset_epoch": _numeric_header(headers, "X-RateLimit-Reset"),
        "retry_after_seconds": _retry_after_seconds(headers),
        "github_request_id": request_id,
    }


def _github_http_error(error: HTTPError) -> GitHubInboxHTTPError:
    """Classify without retaining response bodies, request URLs, or raw headers."""
    status = error.code
    diagnostics = _github_response_diagnostics(status, error.headers)
    remaining = diagnostics["rate_limit_remaining"]
    retry_after = diagnostics["retry_after_seconds"]
    rate_limited = status == 429 or (
        status == 403 and (remaining == 0 or retry_after is not None)
    )
    if status == 403 and not rate_limited:
        # GitHub also reports secondary limits as 403 with remaining quota.
        # Inspect only its bounded JSON message; never log or retain the body.
        try:
            payload = error.read(MAX_ERROR_BYTES + 1)
            decoded = json.loads(payload.decode("utf-8")) if len(payload) <= MAX_ERROR_BYTES else {}
            message = decoded.get("message", "") if isinstance(decoded, dict) else ""
            message = message.casefold() if isinstance(message, str) else ""
            rate_limited = message.startswith((
                "api rate limit exceeded",
                "you have exceeded a secondary rate limit",
                "you have triggered an abuse detection mechanism",
            ))
        except (OSError, ValueError, UnicodeError):
            pass
    failure_class = (
        "rate_limited" if rate_limited else
        "authentication_failed" if status == 401 else
        "access_forbidden" if status == 403 else
        "not_found_or_access_denied" if status == 404 else
        "upstream_error" if 500 <= status <= 599 else
        "http_error"
    )
    diagnostics["failure_class"] = failure_class
    return GitHubInboxHTTPError(diagnostics)


def market_brief_failure_diagnostics(error: Exception) -> dict[str, Any]:
    """Return log-safe metadata, including unknown status for transport failures."""
    result = {
        "reason": error.__class__.__name__,
        "http_status": None,
        "failure_class": "transport_error" if isinstance(error, (URLError, OSError)) else "unclassified",
        "rate_limit_limit": None,
        "rate_limit_remaining": None,
        "rate_limit_reset_epoch": None,
        "retry_after_seconds": None,
        "github_request_id": None,
    }
    if isinstance(error, GitHubInboxHTTPError):
        result.update(error.diagnostics)
    return result


def _clean(value: Any, *, limit: int = 500) -> str:
    return " ".join(str(value or "").split()).strip()[:limit]


def _settings() -> dict[str, Any]:
    repository = _clean(
        os.getenv("MARKET_BRIEF_GITHUB_REPOSITORY", DEFAULT_REPOSITORY),
        limit=200,
    )
    owner = _clean(os.getenv("MARKET_BRIEF_GITHUB_OWNER", DEFAULT_OWNER), limit=100)
    title = _clean(os.getenv("MARKET_BRIEF_GITHUB_TITLE", DEFAULT_ISSUE_TITLE), limit=240)
    issue_text = _clean(os.getenv("MARKET_BRIEF_GITHUB_ISSUE", ""), limit=20)
    if not issue_text:
        return {
            "enabled": False,
            "repository": repository,
            "owner": owner,
            "title": title,
            "issue_number": None,
        }
    if not _REPOSITORY_RE.fullmatch(repository):
        raise ValueError("MARKET_BRIEF_GITHUB_REPOSITORY must use owner/repository form")
    try:
        issue_number = int(issue_text)
    except ValueError as exc:
        raise ValueError("MARKET_BRIEF_GITHUB_ISSUE must be a positive integer") from exc
    if issue_number <= 0:
        raise ValueError("MARKET_BRIEF_GITHUB_ISSUE must be a positive integer")
    if not owner or not title:
        raise ValueError("market brief inbox owner and title must be configured")
    return {
        "enabled": True,
        "repository": repository,
        "owner": owner,
        "title": title,
        "issue_number": issue_number,
    }


def _fetch_issue(repository: str, issue_number: int) -> dict[str, Any]:
    url = f"https://api.github.com/repos/{repository}/issues/{issue_number}"
    request = Request(
        url,
        headers={
            "Accept": "application/vnd.github+json",
            "User-Agent": "garibaldi-market-oracle/market-intelligence-inbox",
            "X-GitHub-Api-Version": "2022-11-28",
        },
    )
    try:
        with urlopen(request, timeout=12) as response:  # noqa: S310 - fixed HTTPS host and validated repository path
            diagnostics = _github_response_diagnostics(
                getattr(response, "status", None), getattr(response, "headers", None),
            )
            # This proves receipt of an HTTP response, before packet validation
            # or ingestion. An unchanged brief must still leave this receipt.
            log.info(
                "GITHUB MARKET INTELLIGENCE HTTP | status=RECEIVED | http_status=%s | "
                "rate_limit_limit=%s | rate_limit_remaining=%s | rate_limit_reset_epoch=%s | "
                "retry_after_seconds=%s | github_request_id=%s | execution_impact=NONE",
                diagnostics["http_status"],
                diagnostics["rate_limit_limit"],
                diagnostics["rate_limit_remaining"],
                diagnostics["rate_limit_reset_epoch"],
                diagnostics["retry_after_seconds"],
                diagnostics["github_request_id"],
            )
            payload = response.read(MAX_RESPONSE_BYTES + 1)
    except HTTPError as exc:
        try:
            failure = _github_http_error(exc)
        finally:
            exc.close()
        raise failure from exc
    if len(payload) > MAX_RESPONSE_BYTES:
        raise ValueError("GitHub issue response exceeds the inbox size limit")
    decoded = json.loads(payload.decode("utf-8"))
    if not isinstance(decoded, dict):
        raise ValueError("GitHub issue response must be an object")
    return decoded


def extract_market_brief_packet(body: Any) -> dict[str, Any]:
    text = str(body or "")
    if BODY_MARKER not in text:
        raise ValueError("market intelligence inbox marker is missing")
    match = _JSON_FENCE_RE.search(text)
    if not match:
        raise ValueError("market intelligence inbox JSON packet is missing")
    packet = json.loads(match.group(1))
    if not isinstance(packet, dict):
        raise ValueError("market intelligence packet must be an object")
    if packet.get("schema_version") != SCHEMA_VERSION:
        raise ValueError("market intelligence packet schema is unsupported")
    brief_id = _clean(packet.get("brief_id"), limit=120)
    generated_at = _clean(packet.get("generated_at"), limit=80)
    developments = packet.get("developments")
    if not brief_id or not generated_at:
        raise ValueError("market intelligence packet requires brief_id and generated_at")
    try:
        parsed_generated_at = datetime.fromisoformat(generated_at.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("market intelligence generated_at must be ISO-8601") from exc
    if parsed_generated_at.tzinfo is None:
        raise ValueError("market intelligence generated_at must include a timezone")
    if not isinstance(developments, list) or not developments:
        raise ValueError("market intelligence packet requires developments")
    if len(developments) > MAX_DEVELOPMENTS:
        raise ValueError("market intelligence packet has too many developments")
    for index, item in enumerate(developments):
        if not isinstance(item, dict):
            raise ValueError(f"development {index} must be an object")
        if not _clean(item.get("title"), limit=500):
            raise ValueError(f"development {index} requires a title")
        sources = item.get("sources")
        if sources is not None and not isinstance(sources, list):
            raise ValueError(f"development {index} sources must be a list")
        for source_index, source in enumerate(sources or []):
            if not isinstance(source, dict):
                raise ValueError(
                    f"development {index} source {source_index} must be an object"
                )
            source_url = _clean(source.get("url"), limit=2000)
            if source_url:
                parsed_url = urlparse(source_url)
                if parsed_url.scheme != "https" or not parsed_url.netloc:
                    raise ValueError(
                        f"development {index} source {source_index} must use an HTTPS URL"
                    )
    return packet


def _payload_hash(packet: dict[str, Any]) -> str:
    canonical = json.dumps(packet, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _last_receipt() -> dict[str, Any]:
    with connect() as conn:
        row = conn.execute(
            """
            SELECT last_result
            FROM oracle_brain_learning_state
            WHERE pipeline_key=%s AND market='global'
            """,
            (PIPELINE_KEY,),
        ).fetchone()
    if not row:
        return {}
    result = row.get("last_result") if isinstance(row, dict) else None
    if isinstance(result, dict):
        return dict(result)
    if isinstance(result, str) and result.strip():
        try:
            decoded = json.loads(result)
        except ValueError:
            return {}
        return dict(decoded) if isinstance(decoded, dict) else {}
    return {}


def _record_receipt(receipt: dict[str, Any]) -> None:
    with connect() as conn:
        conn.execute(
            """
            INSERT INTO oracle_brain_learning_state(pipeline_key,market,last_sync_at,last_result)
            VALUES (%s,'global',NOW(),%s::jsonb)
            ON CONFLICT (pipeline_key,market) DO UPDATE SET
                last_sync_at=EXCLUDED.last_sync_at,
                last_result=EXCLUDED.last_result
            """,
            (PIPELINE_KEY, json.dumps(receipt, default=str)),
        )


def sync_market_brief_inbox(
    *,
    issue_loader: Callable[[str, int], dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Ingest the current owner-authored issue body exactly once per payload."""
    settings = _settings()
    if not settings["enabled"]:
        return {
            "status": "disabled",
            "events_persisted": 0,
            "execution_impact": "NONE",
        }

    loader = issue_loader or _fetch_issue
    issue = loader(settings["repository"], int(settings["issue_number"]))
    if issue.get("pull_request") is not None:
        raise ValueError("market intelligence inbox must be a GitHub issue, not a pull request")
    author = issue.get("user") if isinstance(issue.get("user"), dict) else {}
    if _clean(author.get("login"), limit=100).lower() != settings["owner"].lower():
        raise ValueError("market intelligence inbox author is not the configured owner")
    if _clean(issue.get("title"), limit=240) != settings["title"]:
        raise ValueError("market intelligence inbox title does not match the configured contract")
    if _clean(issue.get("state"), limit=20).lower() != "open":
        raise ValueError("market intelligence inbox issue must remain open")

    packet = extract_market_brief_packet(issue.get("body"))
    packet_hash = _payload_hash(packet)
    previous = _last_receipt()
    if previous.get("payload_hash") == packet_hash:
        return {
            "status": "no_change",
            "brief_id": packet.get("brief_id"),
            "payload_hash": packet_hash,
            "events_persisted": 0,
            "execution_impact": "NONE",
        }

    result = ingest_market_brief(packet, provider="Monday Market Intelligence")
    receipt = {
        "status": "ingested",
        "schema_version": SCHEMA_VERSION,
        "brief_id": result.get("brief_id"),
        "payload_hash": packet_hash,
        "repository": settings["repository"],
        "issue_number": settings["issue_number"],
        "issue_updated_at": issue.get("updated_at"),
        "events_persisted": result.get("events_persisted", 0),
        "event_keys": list(result.get("event_keys") or []),
        "received_at": datetime.now(timezone.utc).isoformat(),
        "execution_impact": "NONE",
    }
    _record_receipt(receipt)
    return receipt


__all__ = [
    "BODY_MARKER",
    "SCHEMA_VERSION",
    "GitHubInboxHTTPError",
    "extract_market_brief_packet",
    "market_brief_failure_diagnostics",
    "sync_market_brief_inbox",
]
