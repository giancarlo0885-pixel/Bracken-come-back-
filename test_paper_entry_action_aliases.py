"""Executable BUY aliases must not bypass paper confirmation or loss cooldowns."""
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

import paper_crypto_churn_guard as churn
import paper_strategy_execution_guard as economics_guard


RESEARCH = SimpleNamespace(model_tier='RESEARCH', sample_count=100,
                           expectancy=-.21, profit_factor=.19)


@pytest.mark.parametrize('action', ['STRONG_BUY', 'LONG'])
def test_entry_alias_requires_two_distinct_observations(action, monkeypatch):
    monkeypatch.setattr(economics_guard, '_RESEARCH_ENTRY_CONFIRMATIONS', {})
    signal={'symbol':'BTC-USD','action':action}
    def observe(stamp):
        return economics_guard._research_entry_confirmation_allows(
            'BTC-USD',signal,{'quote_timestamp':stamp},.9,.55,RESEARCH)
    assert observe('2026-11-04T14:00:00Z') == (False,'research_entry_confirmation_pending:1/2')
    assert observe('2026-11-04T14:00:00Z') == (False,'research_entry_confirmation_pending:1/2')
    assert observe('2026-11-04T14:01:00Z') == (True,'research_entry_confirmation_passed:2/2')


@pytest.mark.parametrize('action', ['STRONG_BUY', 'LONG'])
def test_entry_alias_cannot_bypass_recent_post_cost_loss(action, monkeypatch):
    monkeypatch.setenv('PAPER_CRYPTO_REENTRY_COOLDOWN_MINUTES','10')
    monkeypatch.setenv('PAPER_CRYPTO_LOSS_REENTRY_COOLDOWN_MULTIPLIER','3')
    monkeypatch.setattr(churn,'_recycling_quarantine',lambda _: (True,'not_quarantined'))
    monkeypatch.setattr(churn,'_recent_realized_loss_streak',lambda _:1)
    now=datetime.now(timezone.utc)
    monkeypatch.setattr(churn,'_last_trade',lambda symbol,side:
        {'created_at':now-timedelta(minutes=5),'price':101,'realized_pnl':-.5}
        if side=='SELL' else {'created_at':now-timedelta(minutes=20),'price':100})
    allowed,reason=churn._allow_generic_buy({'symbol':'BTC-USD','action':action})
    assert not allowed and reason.startswith('loss_reentry_cooldown:')
    assert reason.endswith('/30.00m')


@pytest.mark.parametrize('action', ['STRONG_BUY', 'LONG'])
def test_installed_churn_wrapper_rejects_alias_before_execution(action,monkeypatch):
    for key,value in {'EXECUTION_MODE':'paper','ENABLE_BROKER_SUBMISSION':'false',
                      'LIVE_TRADING_ARMED':'false','PAPER_AUTONOMOUS_LEARNING':'true',
                      'PAPER_UNBOUNDED_LEARNING':'true'}.items():monkeypatch.setenv(key,value)
    monkeypatch.setattr(churn,'_INSTALLED',False)
    monkeypatch.setattr(churn,'_allow_generic_buy',Mock(return_value=(False,'loss_reentry_cooldown:test')))
    process=Mock(return_value=['must not execute'])
    worker=SimpleNamespace(process_signals=process)
    assert churn.install_paper_crypto_churn_guard(worker)
    signal={'symbol':'BTC-USD','action':action}
    assert worker.process_signals('crypto',[signal],{}) == []
    churn._allow_generic_buy.assert_called_once_with(signal)
    process.assert_not_called()


@pytest.mark.parametrize('action',['SELL','EXIT','CLOSE','HOLD','STRONG BUY',''])
def test_non_executable_entries_do_not_gain_buy_semantics(action,monkeypatch):
    monkeypatch.setattr(economics_guard,'_RESEARCH_ENTRY_CONFIRMATIONS',{})
    monkeypatch.setattr(churn,'_recycling_quarantine',Mock(side_effect=AssertionError('not an entry')))
    signal={'symbol':'BTC-USD','action':action}
    assert churn._allow_generic_buy(signal) == (True,'not_generic_buy')
    if action:
        assert economics_guard._research_entry_confirmation_allows(
            'BTC-USD',signal,{},None,.55,RESEARCH) == (True,'research_entry_confirmation_non_entry_action')
    churn._recycling_quarantine.assert_not_called()
