"""Fail-closed regression for the Kraken shadow research installer.

This module tests configuration presence, not market edge or live execution.
"""
import pytest

import kraken_primary_research as research


CONTROLS = {
    "EXECUTION_MODE": "paper",
    "ENABLE_BROKER_SUBMISSION": "false",
    "LIVE_TRADING_ARMED": "false",
}
RESEARCH = {
    "KRAKEN_REFERENCE_WS_ENABLED": "true",
    "KRAKEN_PRIMARY_RESEARCH_ENABLED": "true",
}


def configure(monkeypatch, *, omit=(), override=None):
    for name, value in {**RESEARCH, **CONTROLS}.items():
        monkeypatch.setenv(name, value)
    for name in omit:
        monkeypatch.delenv(name, raising=False)
    for name, value in (override or {}).items():
        monkeypatch.setenv(name, value)


@pytest.mark.parametrize("missing", [
    ("EXECUTION_MODE",),
    ("ENABLE_BROKER_SUBMISSION",),
    ("LIVE_TRADING_ARMED",),
    tuple(CONTROLS),
])
def test_primary_research_requires_explicit_paper_controls(monkeypatch, missing):
    configure(monkeypatch, omit=missing)
    assert research.enabled() is False


def test_primary_research_explicit_paper_controls_remain_supported(monkeypatch):
    configure(monkeypatch)
    assert research.enabled() is True


@pytest.mark.parametrize("name,value", [
    ("EXECUTION_MODE", "live"),
    ("ENABLE_BROKER_SUBMISSION", "true"),
    ("LIVE_TRADING_ARMED", "true"),
])
def test_primary_research_rejects_contradictory_controls(monkeypatch, name, value):
    configure(monkeypatch, override={name: value})
    assert research.enabled() is False


def test_primary_research_requires_both_research_opt_ins(monkeypatch):
    configure(monkeypatch)
    monkeypatch.delenv("KRAKEN_PRIMARY_RESEARCH_ENABLED")
    assert research.enabled() is False
    configure(monkeypatch)
    monkeypatch.delenv("KRAKEN_REFERENCE_WS_ENABLED")
    assert research.enabled() is False
