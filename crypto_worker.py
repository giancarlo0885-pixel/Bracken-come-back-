import logging
import os

import market_worker
from crypto_execution_guard import install_crypto_execution_quote_guard
from paper_execution_accounting import install_paper_execution_accounting
from paper_execution_reality import install_paper_execution_reality
from paper_fee_policy import install_fee_aware_fifo_policy
from structured_logging import configure_structured_logging


configure_structured_logging(os.getenv("LOG_LEVEL", "INFO"))
logger = logging.getLogger("crypto-worker")

install_crypto_execution_quote_guard(market_worker)
if os.getenv("EXECUTION_MODE", "paper").strip().lower() == "paper":
    install_paper_execution_reality(market_worker)
    install_paper_execution_accounting(market_worker)
    install_fee_aware_fifo_policy()


def _robinhood_buying_power_state() -> str:
    """Return a non-numeric buying-power state without logging account balances."""
    try:
        from robinhood_crypto_api import RobinhoodCryptoClient

        account = RobinhoodCryptoClient().account_details()
        raw_value = account.get("buying_power")
        if raw_value in (None, ""):
            return "MISSING"
        value = float(raw_value)
        if value > 0:
            return "POSITIVE"
        if value == 0:
            return "ZERO"
        return "INVALID"
    except Exception:
        return "UNAVAILABLE"


def run_robinhood_startup_preflight() -> None:
    """Run a strictly read-only broker connectivity check without exposing secrets."""
    if os.getenv("ROBINHOOD_CRYPTO_ENABLED", "false").strip().lower() != "true":
        logger.info("ROBINHOOD PREFLIGHT | connection=DISABLED | live_trading=DISARMED")
        return

    try:
        from robinhood_crypto_api import preflight

        result = preflight()
    except Exception as exc:
        logger.warning(
            "ROBINHOOD PREFLIGHT | connection=ERROR | auth=FAIL | live_trading=DISARMED | reason=%s",
            exc.__class__.__name__,
        )
        return

    buying_power_state = (
        _robinhood_buying_power_state()
        if result.get("ROBINHOOD AUTH") == "PASS" and result.get("ACCOUNT STATUS") == "PASS"
        else "NOT_CHECKED"
    )

    logger.info(
        "ROBINHOOD PREFLIGHT | connection=%s | auth=%s | account=%s | crypto=%s | "
        "pairs=%s | quote=%s | buying_power=%s | buying_power_state=%s | journal=%s | "
        "live_trading=%s | reason=%s",
        result.get("ROBINHOOD CONNECTION", "UNKNOWN"),
        result.get("ROBINHOOD AUTH", "UNKNOWN"),
        result.get("ACCOUNT STATUS", "UNKNOWN"),
        result.get("CRYPTO STATUS", "UNKNOWN"),
        result.get("TRADABLE PAIR COUNT", 0),
        result.get("QUOTE CHECK", "UNKNOWN"),
        result.get("BUYING POWER CHECK", "UNKNOWN"),
        buying_power_state,
        result.get("ORDER JOURNAL", "UNKNOWN"),
        result.get("LIVE TRADING ARMED/DISARMED", "DISARMED"),
        str(result.get("reason") or "")[:240],
    )


if __name__ == "__main__":
    run_robinhood_startup_preflight()
    market_worker.run_worker("crypto")
