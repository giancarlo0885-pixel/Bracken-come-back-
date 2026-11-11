"""Pure notification formatting and bounded retry policy.

The durable queue is owned by a separate least-privilege service; this module
does not connect to trading databases or invoke trading functions.
"""
from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import urlsplit


@dataclass(frozen=True)
class Destination:
    platform: str
    url: str

    def validate(self) -> None:
        parsed = urlsplit(self.url)
        if parsed.scheme != "https" or parsed.username or parsed.password or parsed.fragment:
            raise ValueError("Webhook must use a clean HTTPS URL")
        allowed = {
            "discord": {"discord.com", "discordapp.com"},
            "slack": {"hooks.slack.com"},
        }
        if self.platform not in allowed or parsed.hostname not in allowed[self.platform]:
            raise ValueError("Webhook destination is not allowlisted")


def payload(destination: Destination, severity: str, incident_id: str, description: str) -> dict:
    destination.validate()
    severity = str(severity).upper()[:16]
    incident_id = str(incident_id)[:80]
    description = str(description)[:1200]
    headline = f"[{severity}] Oracle inventory audit: {incident_id}"
    if destination.platform == "discord":
        return {"content": f"**{headline}**\n{description}", "allowed_mentions": {"parse": []}}
    return {"text": headline, "blocks": [
        {"type": "header", "text": {"type": "plain_text", "text": headline[:150]}},
        {"type": "section", "text": {"type": "plain_text", "text": description}},
    ]}


def accepted_status(code: int) -> bool:
    """Discord can return 204; Slack commonly returns 200."""
    return code in (200, 204)


def retry_delay(attempt: int, retry_after: float | None = None) -> float:
    if retry_after is not None:
        return max(0.0, min(float(retry_after), 300.0))
    return min(300.0, float(2 ** max(0, min(attempt, 8))))
