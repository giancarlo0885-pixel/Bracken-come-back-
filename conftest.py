"""Ensure the repository root is importable in every pytest environment."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


@pytest.fixture(autouse=True)
def isolate_global_scanner_live_network(request, monkeypatch):
    """Keep historical scanner unit tests deterministic.

    Production now has a paper-only fresh intraday market fallback. Historical
    scanner tests pass an explicit evaluation timestamp, so an unrelated live
    network quote from today must not override their fixture data. Tests that
    intentionally exercise a live snapshot can still monkeypatch the function
    inside the test, which takes precedence over this baseline stub.
    """
    if request.node.path.name != "test_global_market_scanner.py":
        return
    import global_market_scanner as scanner

    monkeypatch.setattr(scanner, "get_live_snapshot", lambda symbol: None)
