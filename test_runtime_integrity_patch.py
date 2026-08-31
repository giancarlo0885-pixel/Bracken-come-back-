from types import SimpleNamespace

import runtime_integrity_patch as patch


def test_small_account_position_cap_replaces_aggressive_extra_positions(monkeypatch):
    monkeypatch.setenv("EXECUTION_MODE", "paper")
    monkeypatch.setenv("PAPER_BROKER_PROFILE", "small-account-paper")
    monkeypatch.setenv("SMALL_ACCOUNT_MAX_OPEN_POSITIONS", "10")
    module = SimpleNamespace(DEFAULT_MAX_OPEN_POSITIONS=40, EXTRA_OPEN_POSITIONS=6)

    effective = patch._install_small_account_position_cap(module)

    assert effective == 10
    assert module.DEFAULT_MAX_OPEN_POSITIONS == 10
    assert module.EXTRA_OPEN_POSITIONS == 0


def test_small_account_position_cap_preserves_stricter_config(monkeypatch):
    monkeypatch.setenv("EXECUTION_MODE", "paper")
    monkeypatch.setenv("PAPER_BROKER_PROFILE", "small-account-paper")
    monkeypatch.setenv("SMALL_ACCOUNT_MAX_OPEN_POSITIONS", "10")
    module = SimpleNamespace(DEFAULT_MAX_OPEN_POSITIONS=5, EXTRA_OPEN_POSITIONS=6)

    effective = patch._install_small_account_position_cap(module)

    assert effective == 5
    assert module.DEFAULT_MAX_OPEN_POSITIONS == 5
    assert module.EXTRA_OPEN_POSITIONS == 0


def test_position_cap_does_not_touch_nonpaper_runtime(monkeypatch):
    monkeypatch.setenv("EXECUTION_MODE", "live")
    monkeypatch.setenv("PAPER_BROKER_PROFILE", "small-account-paper")
    module = SimpleNamespace(DEFAULT_MAX_OPEN_POSITIONS=40, EXTRA_OPEN_POSITIONS=6)

    assert patch._install_small_account_position_cap(module) is None
    assert module.DEFAULT_MAX_OPEN_POSITIONS == 40
    assert module.EXTRA_OPEN_POSITIONS == 6


def test_yahoo_paper_reference_is_not_mislabeled_provider_verified():
    route = {
        "provider_quote_verified": False,
        "paper_reference_verified": True,
        "verification_basis": "paper:fresh_identity_matched_yahoo",
    }
    payload = {"quote_verified": True}

    patch._verification_metadata(route, payload)

    assert payload["quote_verified"] is True
    assert payload["execution_quote_eligible"] is True
    assert payload["provider_quote_verified"] is False
    assert payload["paper_reference_verified"] is True
    assert payload["verified"] is False
    assert payload["verification_kind"] == "paper_reference"
    assert payload["correlation_id"]
    assert payload["decision_correlation_id"] == payload["correlation_id"]


def test_provider_verified_quote_remains_explicitly_verified():
    route = {
        "provider_quote_verified": True,
        "paper_reference_verified": False,
        "verification_basis": "provider",
        "correlation_id": "corr-123",
    }
    payload = {"quote_verified": True}

    patch._verification_metadata(route, payload)

    assert payload["verified"] is True
    assert payload["provider_quote_verified"] is True
    assert payload["paper_reference_verified"] is False
    assert payload["verification_kind"] == "provider"
    assert payload["correlation_id"] == "corr-123"


def test_unverified_quote_does_not_gain_verification():
    payload = {"quote_verified": False}

    patch._verification_metadata({}, payload)

    assert payload["execution_quote_eligible"] is False
    assert payload["verified"] is False
    assert payload["provider_quote_verified"] is False
    assert payload["paper_reference_verified"] is False
    assert payload["verification_kind"] == "unverified"


def test_workers_install_integrity_patch():
    stock_source = open("stock_worker.py", encoding="utf-8").read()
    crypto_source = open("crypto_worker.py", encoding="utf-8").read()

    assert "install_runtime_integrity_patch(market_worker)" in stock_source
    assert "install_runtime_integrity_patch(market_worker)" in crypto_source
