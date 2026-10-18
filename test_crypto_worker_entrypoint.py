from unittest.mock import Mock
import os
import crypto_worker_entrypoint as entrypoint


def test_entrypoint_runs_shadow_learning_without_processing_removed_credentials(monkeypatch):
    values = {'ROBINHOOD_CRYPTO_PRIVATE_KEY_BASE64': '  archived-value\r\n',
              'ROBINHOOD_CRYPTO_PUBLIC_KEY_BASE64': '"archived-value"',
              'ROBINHOOD_CRYPTO_API_KEY': '  archived-value  '}
    for k,v in values.items():monkeypatch.setenv(k,v)
    for name in ('install_paper_entry_edge_challenger_shadow', 'install_aeve_generation_controller', 'install_paper_kelly_shadow'):
        monkeypatch.setattr(entrypoint,name,Mock())
    run=Mock();monkeypatch.setattr(entrypoint.runpy,'run_module',run)
    entrypoint.main()
    run.assert_called_once_with('crypto_worker',run_name='__main__')
    for k,v in values.items():assert os.environ[k]==v
