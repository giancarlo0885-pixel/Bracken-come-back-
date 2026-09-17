from __future__ import annotations

import os
import runpy

from paper_entry_edge_challenger_shadow import install_paper_entry_edge_challenger_shadow
from paper_aeve_generation_controller import install_aeve_generation_controller


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
    # Forward-only measurement challengers. These persist their own durable state
    # and cannot alter Council decisions, sizing, execution, broker submission, or live state.
    install_paper_entry_edge_challenger_shadow()
    install_aeve_generation_controller()
    runpy.run_module("crypto_worker", run_name="__main__")


if __name__ == "__main__":
    main()
