from __future__ import annotations

import crypto_execution_guard as guard


def test_yahoo_provider_identity_triggers_consensus_even_when_internal_flags_are_absent():
    assert guard._paper_yahoo_reference({"provider": "Yahoo Finance", "quote_verified": True}) is True


def test_primary_provider_identity_does_not_trigger_yahoo_consensus():
    assert guard._paper_yahoo_reference({"provider": "Polygon", "quote_verified": True}) is False
