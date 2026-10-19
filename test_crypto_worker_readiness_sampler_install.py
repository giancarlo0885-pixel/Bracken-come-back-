from pathlib import Path


def test_worker_removes_broker_sampler_and_installs_no_execution_source_guard():
    source=Path('crypto_worker.py').read_text()
    assert 'install_v39_quote_verification_sampler' not in source
    assert source.index('    if not install_paper_pricing(market_worker):') < source.index('    prepare_capital_readiness_runtime(')
    assert source.index('        install_unavailable_crypto_execution_source(worker=market_worker)') < source.index('    prepare_capital_readiness_runtime(')
    assert 'run_robinhood_startup_preflight' not in source
