"""Execute the worker entrypoint; never contact providers or a database."""
import builtins
import logging
from pathlib import Path
import runpy
import sys
import time
from types import ModuleType

import pytest


WORKER = Path(__file__).with_name("stock_worker.py")
INSTALLERS = {
    'migration_runtime_safety': 'install_migration_runtime_safety',
    'runtime_provider_reliability': 'install_yahoo_runtime_reliability',
    'runtime_integrity_patch': 'install_runtime_integrity_patch',
    'event_opportunity_runtime': 'install_event_opportunity_runtime',
    'paper_autonomous_learning': 'install_paper_autonomous_learning',
    'portfolio_valuation': 'install_closed_market_valuation_pulse',
    'stock_execution_repair': 'install_stock_execution_quote_repair',
    'stock_transient_quarantine_repair': 'install_stock_transient_quarantine_repair',
    'paper_execution_reality': 'install_paper_execution_reality',
    'paper_execution_accounting': 'install_paper_execution_accounting',
    'paper_fee_policy': 'install_fee_aware_fifo_policy',
    'paper_regime_economics_shadow': 'install_paper_regime_economics_shadow',
    'paper_shadow_exit_challenger': 'install_paper_shadow_exit_challenger',
}


class Paused(BaseException):
    """Stop an actual entrypoint once it reaches its idle sleep."""


@pytest.fixture(autouse=True)
def paper_only(monkeypatch):
    for key, value in {'EXECUTION_MODE': 'paper', 'ENABLE_BROKER_SUBMISSION': 'false',
                       'LIVE_TRADING_ARMED': 'false', 'DATABASE_URL': ''}.items():
        monkeypatch.setenv(key, value)
    module = ModuleType('structured_logging')
    module.configure_structured_logging = lambda *args: None
    monkeypatch.setitem(sys.modules, module.__name__, module)


@pytest.mark.parametrize('value', [None, '', 'false', '0', 'no', 'off', 'invalid', ' FALSE '])
def test_paused_entrypoint_never_imports_worker_or_background_installers(monkeypatch, caplog, value):
    if value is None:
        monkeypatch.delenv('ENABLE_CASH_MARKET_RESEARCH', raising=False)
    else:
        monkeypatch.setenv('ENABLE_CASH_MARKET_RESEARCH', value)
    original_import = builtins.__import__

    def forbid_startup(name, *args, **kwargs):
        if name == 'market_worker' or name in INSTALLERS or name == 'capital_readiness_runtime':
            pytest.fail('Paused worker imported active runtime: '+name)
        return original_import(name, *args, **kwargs)

    sleeps = []

    def reached_idle(seconds):
        sleeps.append(seconds)
        raise Paused

    monkeypatch.setattr(builtins, '__import__', forbid_startup)
    monkeypatch.setattr(time, 'sleep', reached_idle)
    with caplog.at_level(logging.INFO), pytest.raises(Paused):
        runpy.run_path(str(WORKER), run_name='__main__')
    assert sleeps == [3600]
    assert 'cash market research paused' in caplog.text


@pytest.mark.parametrize('value', ['1', 'true', 'yes', 'on', ' TRUE '])
def test_explicit_opt_in_preserves_installers_readiness_audit_and_cash_worker(monkeypatch, value):
    monkeypatch.setenv('ENABLE_CASH_MARKET_RESEARCH', value)
    events = []

    def install_module(module_name, function_name):
        module = ModuleType(module_name)
        def call(*args):
            events.append((function_name, args))
        setattr(module, function_name, call)
        monkeypatch.setitem(sys.modules, module_name, module)
        return module

    worker = install_module('market_worker', 'run_worker')
    for module, function in INSTALLERS.items():
        install_module(module, function)
    install_module('capital_readiness_runtime', 'prepare_capital_readiness_runtime')
    install_module('paper_trade_evidence_audit', 'emit_trade_evidence_audit')
    monkeypatch.setattr(time, 'sleep', lambda *_: pytest.fail('Opted-in worker unexpectedly paused'))
    runpy.run_path(str(WORKER), run_name='__main__')
    assert events[-3:] == [
        ('prepare_capital_readiness_runtime', (worker, 'cash')),
        ('emit_trade_evidence_audit', ('cash',)),
        ('run_worker', ('cash',)),
    ]
    names = [name for name, _ in events]
    assert all(names.count(name) == 1 for name in INSTALLERS.values())
    assert ('install_paper_regime_economics_shadow', ('cash',)) in events
    assert ('install_paper_shadow_exit_challenger', ('cash',)) in events
