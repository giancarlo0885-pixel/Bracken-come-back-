"""Regression contract: strategy economics may run only with explicit paper controls.

This adapter influences paper entry sizing and fee/edge decisions. An absent
or unrecognized broker safety setting must never be treated as disabled.
"""
import pytest

import paper_strategy_economics as economics


def _configured_paper(monkeypatch):
    monkeypatch.setenv("EXECUTION_MODE", "paper")
    monkeypatch.setenv("ENABLE_BROKER_SUBMISSION", "false")
    monkeypatch.setenv("LIVE_TRADING_ARMED", "false")
    monkeypatch.setenv("PAPER_AUTONOMOUS_LEARNING", "true")


@pytest.mark.parametrize(
    "missing",
    ("EXECUTION_MODE", "ENABLE_BROKER_SUBMISSION", "LIVE_TRADING_ARMED"),
)
def test_strategy_economics_rejects_missing_execution_control(monkeypatch, missing):
    _configured_paper(monkeypatch)
    assert economics.active() is True
    monkeypatch.delenv(missing)
    assert economics.active() is False


@pytest.mark.parametrize("name", ("ENABLE_BROKER_SUBMISSION", "LIVE_TRADING_ARMED"))
def test_strategy_economics_rejects_unknown_disarm_value(monkeypatch, name):
    _configured_paper(monkeypatch)
    monkeypatch.setenv(name, "unrecognized")
    assert economics.active() is False


@pytest.mark.parametrize("false_value", ("0", "no", "off", "FALSE"))
def test_strategy_economics_preserves_explicit_safe_false_values(monkeypatch, false_value):
    _configured_paper(monkeypatch)
    monkeypatch.setenv("ENABLE_BROKER_SUBMISSION", false_value)
    monkeypatch.setenv("LIVE_TRADING_ARMED", false_value)
    assert economics.active() is True


def test_strategy_economics_still_requires_learning_opt_in(monkeypatch):
    _configured_paper(monkeypatch)
    monkeypatch.delenv("PAPER_AUTONOMOUS_LEARNING")
    assert economics.active() is False
