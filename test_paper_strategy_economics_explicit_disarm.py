"""Fail-first tests for explicit paper-only economics activation controls."""
from __future__ import annotations

import pytest

import paper_strategy_economics as economics


@pytest.fixture
def disarmed_paper(monkeypatch):
    monkeypatch.setenv("EXECUTION_MODE", "paper")
    monkeypatch.setenv("PAPER_AUTONOMOUS_LEARNING", "true")
    monkeypatch.setenv("ENABLE_BROKER_SUBMISSION", "false")
    monkeypatch.setenv("LIVE_TRADING_ARMED", "false")
    assert economics.active() is True


@pytest.mark.parametrize(
    "variable",
    ("EXECUTION_MODE", "ENABLE_BROKER_SUBMISSION", "LIVE_TRADING_ARMED"),
)
def test_missing_execution_control_cannot_activate_economics(disarmed_paper, monkeypatch, variable):
    monkeypatch.delenv(variable)
    assert economics.active() is False


@pytest.mark.parametrize(
    "variable,value",
    (
        ("EXECUTION_MODE", ""),
        ("ENABLE_BROKER_SUBMISSION", ""),
        ("LIVE_TRADING_ARMED", ""),
        ("EXECUTION_MODE", "unknown"),
        ("ENABLE_BROKER_SUBMISSION", "unknown"),
        ("LIVE_TRADING_ARMED", "unknown"),
    ),
)
def test_ambiguous_execution_control_cannot_activate_economics(
    disarmed_paper, monkeypatch, variable, value
):
    monkeypatch.setenv(variable, value)
    assert economics.active() is False


@pytest.mark.parametrize("disabled", ("false", "0", "no", "off"))
def test_explicitly_disarmed_controls_keep_paper_economics_available(
    disarmed_paper, monkeypatch, disabled
):
    monkeypatch.setenv("ENABLE_BROKER_SUBMISSION", disabled)
    monkeypatch.setenv("LIVE_TRADING_ARMED", disabled)
    assert economics.active() is True


def test_armed_controls_never_activate_economics(disarmed_paper, monkeypatch):
    monkeypatch.setenv("ENABLE_BROKER_SUBMISSION", "true")
    assert economics.active() is False
    monkeypatch.setenv("ENABLE_BROKER_SUBMISSION", "false")
    monkeypatch.setenv("LIVE_TRADING_ARMED", "true")
    assert economics.active() is False
