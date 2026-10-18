"""Removed broker startup must not be revived by legacy environment variables."""
import ast
from pathlib import Path
from unittest.mock import Mock
import pytest
import robinhood_crypto_api as api


def test_worker_has_no_active_broker_install_or_preflight():
    tree=ast.parse(Path('crypto_worker.py').read_text())
    modules={node.module for node in ast.walk(tree) if isinstance(node,ast.ImportFrom)}
    assert not any(m and m.startswith('robinhood_') for m in modules)
    assert not modules.intersection({'live_broker_capital','live_v39_broker_bridge',
        'crypto_dynamic_universe_runtime','crypto_provider_health_runtime','paper_broker_reference_runtime',
        'crypto_quote_readiness_sampler'})
    assert not any(isinstance(n,ast.FunctionDef) and n.name=='run_robinhood_startup_preflight' for n in ast.walk(tree))


@pytest.mark.parametrize('method,path',[('GET','/api/v2/crypto/marketdata/best_bid_ask/?symbol=BTC-USD'),
    ('GET','/api/v2/crypto/trading/accounts/'),('POST','/api/v2/crypto/trading/orders/'),
    ('DELETE','/api/v2/crypto/trading/orders/old/')])
def test_legacy_enabled_environment_cannot_send_requests_or_sign(monkeypatch,method,path):
    monkeypatch.setenv('ROBINHOOD_CRYPTO_ENABLED','true')
    monkeypatch.setenv('ROBINHOOD_CRYPTO_API_KEY','not-a-real-key')
    monkeypatch.setenv('ROBINHOOD_CRYPTO_PRIVATE_KEY_BASE64','not-a-real-key')
    session,signer=Mock(),Mock()
    client=api.RobinhoodCryptoClient(api_key='not-a-real-key',private_key_base64='not-a-real-key',session=session,signer=signer)
    assert client.configured()=={'ok':False,'reason':'ROBINHOOD_INTEGRATION_REMOVED'}
    with pytest.raises(RuntimeError,match='ROBINHOOD_INTEGRATION_REMOVED'):client.request(method,path)
    session.request.assert_not_called();signer.assert_not_called()
