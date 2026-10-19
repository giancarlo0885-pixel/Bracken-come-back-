from types import SimpleNamespace
from unittest.mock import Mock
import pytest
from crypto_execution_source import install_unavailable_crypto_execution_source


@pytest.mark.parametrize('provider',['Robinhood Crypto','Kraken WebSocket v2 reference','Coinbase Exchange','Yahoo Finance'])
@pytest.mark.parametrize('symbol,market',[('BTC-USD','crypto'),('ETH-USD','cash'),('AAVE-USD','CRYPTO')])
def test_no_cached_or_research_quote_can_replace_removed_broker(provider,symbol,market):
    payload={'provider':provider,'price':100,'quote_verified':True,'stale':False}
    original=Mock(return_value=payload);oracle=SimpleNamespace(_verified_quote_for=original, _execution_quote_guard=Mock(return_value=(True,"")))
    assert install_unavailable_crypto_execution_source(oracle)
    assert not install_unavailable_crypto_execution_source(oracle)
    assert oracle._verified_quote_for(symbol,{symbol:payload},market) is None
    original.assert_not_called()
    assert payload['quote_verified'] is True  # Never rewrite original evidence.


def test_stock_quote_path_unchanged_and_removed_provider_rejected():
    quote={'provider':'Alpaca IEX','price':100,'quote_verified':True}
    original=Mock(return_value=quote);oracle=SimpleNamespace(_verified_quote_for=original, _execution_quote_guard=Mock(return_value=(True,"")))
    install_unavailable_crypto_execution_source(oracle)
    assert oracle._verified_quote_for('MSFT',{'MSFT':quote},'cash') is quote
    original.assert_called_once_with('MSFT',{'MSFT':quote},'cash')
    original.return_value={'provider':'Robinhood Crypto','price':100}
    assert oracle._verified_quote_for('MSFT',{},'cash') is None


def test_configuration_ignores_legacy_enabled_and_credentials():
    import os,subprocess,sys
    env=dict(os.environ,ROBINHOOD_CRYPTO_ENABLED='true',ROBINHOOD_CRYPTO_API_KEY='not-a-real-key',
        ROBINHOOD_CRYPTO_PRIVATE_KEY_BASE64='not-a-real-key',ROBINHOOD_CRYPTO_API_MODE='live')
    subprocess.run([sys.executable,'-c',"import config; assert config.ROBINHOOD_CRYPTO_ENABLED is False; assert config.ROBINHOOD_CRYPTO_API_MODE=='removed'; assert config.ROBINHOOD_CRYPTO_API_KEY==config.ROBINHOOD_CRYPTO_PRIVATE_KEY_BASE64==''"],env=env,check=True)


@pytest.mark.parametrize('provider',['Robinhood Crypto','Kraken WebSocket v2 reference','Coinbase Exchange','Yahoo Finance'])
def test_final_fill_guard_rejects_even_prevalidated_crypto_payloads(provider):
    original=Mock(return_value=(True,''))
    oracle=SimpleNamespace(_verified_quote_for=Mock(),_execution_quote_guard=original)
    install_unavailable_crypto_execution_source(oracle)
    quote={'provider':provider,'quote_verified':True,'price':100}
    assert oracle._execution_quote_guard('crypto','BTC-USD',100,{'symbol':'BTC-USD'},quote_metadata=quote)==(
        False,'CRYPTO_EXECUTION_SOURCE_NOT_CONFIGURED')
    original.assert_not_called()
    assert oracle._execution_quote_guard('cash','MSFT',100,{'symbol':'MSFT'},quote_metadata={'provider':'Alpaca IEX'})==(True,'')


def test_buy_and_sell_paths_stop_before_database_mutations(monkeypatch):
    import oracle_bot as oracle
    monkeypatch.setattr(oracle,'_verified_quote_for',oracle._verified_quote_for)
    monkeypatch.setattr(oracle,'_execution_quote_guard',oracle._execution_quote_guard)
    monkeypatch.setattr(oracle,'_execution_disabled',lambda *a,**k:False)
    execute_close=Mock(side_effect=AssertionError('must not write a close'))
    monkeypatch.setattr(oracle,'_execute_close_position',execute_close)
    install_unavailable_crypto_execution_source(oracle)
    quote={'provider':'Robinhood Crypto','quote_verified':True,'price':100,'symbol':'BTC-USD'}
    assert oracle._buy('crypto','BTC-USD',100,{'symbol':'BTC-USD'},verified_quote=quote)==(
        False,'CRYPTO_EXECUTION_SOURCE_NOT_CONFIGURED',None)
    assert oracle._close_position('crypto',{'symbol':'BTC-USD','quantity':1},100,'EXIT',quote_metadata=quote) is False
    execute_close.assert_not_called()


def test_unconfigured_source_rejects_capital_candidate_despite_legacy_verified_flag(monkeypatch):
    import paper_core_rebalance_qualification as qualification
    import runtime_integrity_patch as patch
    for key, value in {'EXECUTION_MODE':'paper', 'PAPER_AUTONOMOUS_LEARNING':'true',
                       'PAPER_UNBOUNDED_LEARNING':'false', 'ENABLE_BROKER_SUBMISSION':'false',
                       'LIVE_TRADING_ARMED':'false'}.items():
        monkeypatch.setenv(key, value)
    log = Mock()
    worker = SimpleNamespace(log=log, _execution_quote_eligible=Mock(return_value=True),
        _v39_signal_opportunity=lambda *a:dict(qualified_for_capital=False, tradeable=True,
            avg_dollar_volume=1208940.8, spread_pct=.049402, risk_score=15.54))
    qualification.install_paper_core_rebalance_qualification(worker)
    oracle = SimpleNamespace(_verified_quote_for=Mock(), _execution_quote_guard=Mock())
    install_unavailable_crypto_execution_source(oracle, worker=worker)
    signal = SimpleNamespace(symbol='BNB-USD', action='HOLD', signal_id=1, forecast_id=None,
        portfolio_intent=patch.CORE_REBALANCE_STRATEGIC_CANDIDATE_INTENT)
    quote = dict(quote_verified=True, requested_symbol='BNB-USD', provider_symbol='BNB-USD')
    result = worker._v39_signal_opportunity('crypto', signal, {'BNB-USD':quote}, {}, 'fast')
    assert result['qualified_for_capital'] is False
    assert result['execution_source_eligible'] is False
    assert result['execution_source_rejection'] == 'CRYPTO_EXECUTION_SOURCE_NOT_CONFIGURED'
    assert not log.info.called  # No misleading PAPER QUALIFICATION success message.
    assert quote['quote_verified'] is True  # Historical/original payload remains intact.
