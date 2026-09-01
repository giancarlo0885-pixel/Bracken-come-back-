from __future__ import annotations


def test_crypto_worker_installs_v39_quote_readiness_sampler() -> None:
    source = open("crypto_worker.py", encoding="utf-8").read()
    assert "from crypto_quote_readiness_sampler import install_v39_quote_verification_sampler" in source
    assert "install_v39_quote_verification_sampler(market_worker)" in source
