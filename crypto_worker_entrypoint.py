from __future__ import annotations

import os
import runpy


def _normalize_base64_env(name: str) -> None:
    value = os.getenv(name)
    if not value:
        return
    normalized = "".join(value.split()).strip('"').strip("'")
    os.environ[name] = normalized


def main() -> None:
    # PowerShell / clipboard pastes can carry CRLF or surrounding whitespace.
    # Normalize only formatting; never transform the actual base64 payload.
    _normalize_base64_env("ROBINHOOD_CRYPTO_PRIVATE_KEY_BASE64")
    _normalize_base64_env("ROBINHOOD_CRYPTO_PUBLIC_KEY_BASE64")
    os.environ["ROBINHOOD_CRYPTO_API_KEY"] = os.getenv("ROBINHOOD_CRYPTO_API_KEY", "").strip()
    runpy.run_module("crypto_worker", run_name="__main__")


if __name__ == "__main__":
    main()
