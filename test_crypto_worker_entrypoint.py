import os

import crypto_worker_entrypoint as entrypoint


def test_normalize_base64_env_strips_clipboard_whitespace(monkeypatch):
    monkeypatch.setenv("ROBINHOOD_CRYPTO_PRIVATE_KEY_BASE64", "  abcDEF123==\r\n")
    entrypoint._normalize_base64_env("ROBINHOOD_CRYPTO_PRIVATE_KEY_BASE64")
    assert os.environ["ROBINHOOD_CRYPTO_PRIVATE_KEY_BASE64"] == "abcDEF123=="


def test_normalize_base64_env_strips_surrounding_quotes(monkeypatch):
    monkeypatch.setenv("ROBINHOOD_CRYPTO_PRIVATE_KEY_BASE64", '"abcDEF123=="')
    entrypoint._normalize_base64_env("ROBINHOOD_CRYPTO_PRIVATE_KEY_BASE64")
    assert os.environ["ROBINHOOD_CRYPTO_PRIVATE_KEY_BASE64"] == "abcDEF123=="
