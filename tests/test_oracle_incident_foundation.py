from decimal import Decimal

import pytest

from oracle_incident_audit import classify_inventory
from oracle_incident_outbox import Destination, accepted_status, payload, retry_delay


def test_inventory_mismatch_and_missing_evidence():
    rows = [
        {"market": "crypto", "symbol": "BTC-USD", "position_quantity": Decimal("2"), "lot_quantity": Decimal("1")},
        {"market": "crypto", "symbol": "ETH-USD", "position_quantity": 1, "lot_quantity": None},
        {"market": "crypto", "symbol": "SOL-USD", "position_quantity": 1, "lot_quantity": 1},
    ]
    findings = classify_inventory(rows)
    assert [f.severity for f in findings] == ["CRITICAL", "WARNING"]
    assert findings[0].key == "inventory:crypto:BTC-USD"


def test_negative_inventory_critical():
    assert classify_inventory([{"market": "crypto", "symbol": "X", "position_quantity": -1, "lot_quantity": 0}])[0].severity == "CRITICAL"


def test_discord_mentions_disabled():
    dest = Destination("discord", "https://discord.com/api/webhooks/example/token")
    assert payload(dest, "critical", "test", "@everyone")["allowed_mentions"] == {"parse": []}


def test_slack_payload():
    dest = Destination("slack", "https://hooks.slack.com/services/example")
    assert "blocks" in payload(dest, "warning", "test", "missing fee")


@pytest.mark.parametrize("url", ["http://discord.com/api/webhooks/x", "https://discord.com.evil.example/x", "https://user:pass@discord.com/x"])
def test_reject_bad_destination(url):
    with pytest.raises(ValueError):
        Destination("discord", url).validate()


def test_receipt_and_bounded_retry():
    assert accepted_status(200) and accepted_status(204) and not accepted_status(429)
    assert retry_delay(50) <= 300
    assert retry_delay(1, 99999) == 300


def test_audit_adapter_fails_closed_until_schema_is_verified():
    from oracle_incident_audit import audit_inventory

    with pytest.raises(NotImplementedError, match="schema"):
        audit_inventory(None)


def test_invalid_nonfinite_quantities_are_unknown_not_clean():
    findings = classify_inventory([
        {"market": "crypto", "symbol": "BTC-USD",
         "position_quantity": float("nan"), "lot_quantity": 0}
    ])
    assert len(findings) == 1
    assert findings[0].severity == "WARNING"
