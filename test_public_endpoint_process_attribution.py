"""Regression: the elected reporter must see other public-data clients.

All provider responses and budget reservations are synthetic. No live HTTP,
database changes, broker access, or trading configuration changes.
"""
import gc
from types import SimpleNamespace
from unittest.mock import Mock

import kraken_research_data as data


def test_process_attribution_includes_paper_pricing_and_ephemeral_clients(monkeypatch):
    monkeypatch.setattr(data, "provider_cooldown_active_live", lambda _: {"active": False})
    reserve = Mock(return_value={"reserved": True})
    monkeypatch.setattr(data, "reserve_provider_budget_live", reserve)
    response = Mock(return_value=SimpleNamespace(
        status_code=200,
        json=lambda: {"error": [], "result": {"BTC/USD": {"status": "online"}}},
    ))
    # Clear any prior in-process diagnostic samples before this synthetic test.
    data.PublicResearchData.drain_process_diagnostics()
    try:
        elected_research_client = data.PublicResearchData(response)
        paper_pricing_client = data.PublicResearchData(response)
        paper_pricing_client.instruments()
        paper_pricing_client.instruments()  # cached, no second reservation
        # A transient feed discovery client must not disappear from accounting.
        data.PublicResearchData(response).instruments()
        gc.collect()

        assert elected_research_client.drain_request_diagnostics()["rows"] == []
        snapshot = data.PublicResearchData.drain_process_diagnostics()
        assert snapshot["scope"] == "PROCESS_LOCAL_ALL_CLIENTS"
        assert snapshot["rows"] == [{
            "provider": "KRAKEN", "endpoint": "ASSET_PAIRS",
            "cache_hits": 1, "cache_misses": 2,
            "reservation_attempts": 2, "reservations_granted": 2,
            "reservations_denied": 0,
        }]
        assert reserve.call_count == response.call_count == 2
        assert data.PublicResearchData.drain_process_diagnostics()["rows"] == []
    finally:
        data.PublicResearchData.drain_process_diagnostics()
