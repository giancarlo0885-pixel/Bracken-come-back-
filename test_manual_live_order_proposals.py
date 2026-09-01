from __future__ import annotations

import robinhood_agentic_mcp as agentic


class Client:
    def __init__(self):
        self.calls = []

    def list_tools(self):
        return [{"name": name} for name in agentic.REQUIRED_CRYPTO_TOOLS]

    def call_tool(self, tool, payload):
        self.calls.append((tool, dict(payload)))
        if tool == "preview_crypto_order":
            return {"estimated_price": "65000.00", "warnings": []}
        if tool == "place_crypto_order":
            raise AssertionError("proposal path must never submit")
        return {}


def test_proposal_previews_but_never_places_order():
    client = Client()
    proposal = agentic.propose_crypto_order(
        client,
        {"symbol": "BTC-USD", "side": "buy", "amount": "25.00"},
        context={"signal_id": "sig-1"},
        proposal_id="proposal-1",
    )

    assert proposal["ok"] is True
    assert proposal["status"] == "AWAITING_HUMAN_APPROVAL"
    assert proposal["proposal_id"] == "proposal-1"
    assert proposal["human_approval_required"] is True
    assert proposal["submission_allowed"] is False
    assert [tool for tool, _ in client.calls] == ["preview_crypto_order"]


def test_autonomous_submission_is_fail_closed():
    client = Client()
    result = agentic.autonomous_crypto_order_submission(
        client,
        {"symbol": "ETH-USD", "side": "sell", "amount": "20.00"},
    )

    assert result["ok"] is False
    assert result["status"] == "AUTONOMOUS_LIVE_SUBMISSION_BLOCKED"
    assert result["human_approval_required"] is True
    assert result["submission_allowed"] is False
    assert [tool for tool, _ in client.calls] == ["preview_crypto_order"]


def test_agentic_preflight_reports_autonomous_submission_blocked():
    result = agentic.agentic_preflight(Client())
    assert result["AUTONOMOUS LIVE SUBMISSION"] == "BLOCKED"
    assert result["LIVE TRADING ARMED/DISARMED"] == "DISARMED"
