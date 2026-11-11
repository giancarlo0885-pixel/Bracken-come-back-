"""Regression coverage for explicit paper-learning execution safety settings."""

import pytest

import paper_crypto_learning_relaxation as learning


_SAFETY_SETTINGS = {
    "EXECUTION_MODE": "paper",
    "ENABLE_BROKER_SUBMISSION": "false",
    "LIVE_TRADING_ARMED": "false",
}


@pytest.mark.parametrize("missing", [
    "EXECUTION_MODE",
    "ENABLE_BROKER_SUBMISSION",
    "LIVE_TRADING_ARMED",
])
def test_learning_override_fails_closed_if_safety_setting_missing(monkeypatch, missing):
    for name, value in _SAFETY_SETTINGS.items():
        monkeypatch.setenv(name, value)
    monkeypatch.setenv("PAPER_AUTONOMOUS_LEARNING", "true")
    monkeypatch.setenv("PAPER_UNBOUNDED_LEARNING", "true")
    monkeypatch.delenv(missing, raising=False)

    assert learning._active() is False
    assert learning._unbounded_learning() is False


def test_learning_override_fails_closed_if_all_safety_settings_missing(monkeypatch):
    for name in _SAFETY_SETTINGS:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("PAPER_AUTONOMOUS_LEARNING", "true")
    monkeypatch.setenv("PAPER_UNBOUNDED_LEARNING", "true")

    assert learning._active() is False
    assert learning._unbounded_learning() is False


@pytest.mark.parametrize("setting,unsafe_value", [
    ("EXECUTION_MODE", "live"),
    ("ENABLE_BROKER_SUBMISSION", "true"),
    ("LIVE_TRADING_ARMED", "true"),
])
def test_learning_override_fails_closed_on_unsafe_execution_settings(monkeypatch, setting, unsafe_value):
    for name, value in _SAFETY_SETTINGS.items():
        monkeypatch.setenv(name, value)
    monkeypatch.setenv("PAPER_AUTONOMOUS_LEARNING", "true")
    monkeypatch.setenv("PAPER_UNBOUNDED_LEARNING", "true")
    monkeypatch.setenv(setting, unsafe_value)

    assert learning._active() is False
    assert learning._unbounded_learning() is False


def test_learning_override_requires_explicit_paper_only_settings(monkeypatch):
    for name, value in _SAFETY_SETTINGS.items():
        monkeypatch.setenv(name, value)
    monkeypatch.setenv("PAPER_AUTONOMOUS_LEARNING", "true")
    monkeypatch.setenv("PAPER_UNBOUNDED_LEARNING", "true")

    assert learning._active() is True
    assert learning._unbounded_learning() is True


def test_learning_override_requires_autonomous_learning_opt_in(monkeypatch):
    for name, value in _SAFETY_SETTINGS.items():
        monkeypatch.setenv(name, value)
    monkeypatch.delenv("PAPER_AUTONOMOUS_LEARNING", raising=False)
    monkeypatch.setenv("PAPER_UNBOUNDED_LEARNING", "true")

    assert learning._active() is False
    assert learning._unbounded_learning() is False
