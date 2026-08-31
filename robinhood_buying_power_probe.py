from __future__ import annotations

import os


def _normalize_secret_env(name: str) -> None:
    value = os.getenv(name)
    if not value:
        return
    os.environ[name] = "".join(value.split()).strip('"').strip("'")


def main() -> None:
    _normalize_secret_env("ROBINHOOD_CRYPTO_PRIVATE_KEY_BASE64")
    _normalize_secret_env("ROBINHOOD_CRYPTO_PUBLIC_KEY_BASE64")
    os.environ["ROBINHOOD_CRYPTO_API_KEY"] = os.getenv("ROBINHOOD_CRYPTO_API_KEY", "").strip()

    from robinhood_crypto_api import RobinhoodCryptoClient

    account = RobinhoodCryptoClient().account_details()
    status = str(account.get("status") or "UNKNOWN")
    buying_power = account.get("buying_power", "MISSING")
    currency = str(account.get("buying_power_currency") or "UNKNOWN")
    print(
        "ROBINHOOD BALANCE PROBE | status=%s | buying_power=%s | currency=%s"
        % (status, buying_power, currency)
    )


if __name__ == "__main__":
    main()
