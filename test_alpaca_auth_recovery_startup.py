"""Credential recovery cannot acquire trading authority or interrupt a worker."""
import sys
from types import SimpleNamespace

import pytest

import database
import market_worker as worker


def _worker(monkeypatch, recover, *, ready=True):
    calls = []
    monkeypatch.setitem(sys.modules, "alpaca_auth_recovery", SimpleNamespace(
        recover_alpaca_authentication=lambda: (calls.append("recovery"), recover())[1]))
    monkeypatch.setattr(worker, "WATCHLISTS", {"cash": {}, "crypto": {}})
    monkeypatch.setattr(worker, "_bootstrap_worker_database", lambda label: calls.append("bootstrap") or ready)
    monkeypatch.setattr(worker, "stop_event", SimpleNamespace(is_set=lambda: True))
    monkeypatch.setattr(worker, "cadence_for", lambda market: SimpleNamespace(
        pulse_seconds=15, fast_scan_seconds=30, deep_scan_seconds=120, session_label="closed"))
    monkeypatch.setattr(worker, "set_market_status", lambda *args, **kwargs: calls.append(args[1]))
    monkeypatch.setattr(worker, "ThreadPoolExecutor", lambda **kwargs: (
        calls.append("executor") or SimpleNamespace(shutdown=lambda **kwargs: None)))
    return calls


@pytest.mark.parametrize("market", ["cash", "stock"])
def test_stock_authentication_recovery_follows_bootstrap_before_scan_executors(monkeypatch, market):
    calls = _worker(monkeypatch, lambda: {"status": "VERIFIED"})
    worker.run_worker(market)
    assert calls[:2] == ["bootstrap", "recovery"]
    assert calls.count("recovery") == 1
    assert calls.index("recovery") < calls.index("executor")
    assert calls[-1] == "stopped"


def test_crypto_worker_does_not_probe_alpaca_credentials(monkeypatch):
    calls = _worker(monkeypatch, lambda: pytest.fail("crypto must not request stock authentication"))
    worker.run_worker("crypto")
    assert "recovery" not in calls
    assert "executor" in calls


def test_failed_database_bootstrap_cannot_probe_credentials(monkeypatch):
    calls = _worker(monkeypatch, lambda: pytest.fail("database guards must precede HTTP"), ready=False)
    worker.run_worker("cash")
    assert calls == ["bootstrap"]


def test_recovery_failure_does_not_stop_worker_or_log_exception_contents(monkeypatch, caplog):
    def failed():
        raise RuntimeError("SECRET_KEY_WITH_REQUEST_HEADERS")
    calls = _worker(monkeypatch, failed)
    worker.run_worker("cash")
    assert "executor" in calls
    assert "status=UNAVAILABLE" in caplog.text
    assert "reason=RuntimeError" in caplog.text
    assert "SECRET" not in caplog.text


def test_authentication_receipts_are_protected_from_retention():
    assert "alpaca_auth_recovery_receipts" in database.CANONICAL_PROTECTED_TABLES
    assert database.DATABASE_TABLE_GROWTH_AUDIT["alpaca_auth_recovery_receipts"]["retention"] == "never auto-delete"
    with pytest.raises(ValueError, match="protected table"):
        database._apply_retention_policy(None, "alpaca_auth_recovery_receipts", {"keep_rows": 1})
