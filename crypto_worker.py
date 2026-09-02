import logging
import os

import market_worker
from binance_us_adapter import install_binance_us_reference_fallback
from capital_readiness_runtime import prepare_capital_readiness_runtime
from core_rebalance_observability import install_core_rebalance_observability
from core_rebalance_optimizer_trace import install_core_rebalance_optimizer_trace
from core_rebalance_score_compat import install_core_rebalance_score_compat
from crypto_execution_guard import install_crypto_execution_quote_guard
from crypto_forecast_runtime import install_crypto_short_horizon_forecast
from crypto_quote_readiness_sampler import install_v39_quote_verification_sampler
from crypto_v39_risk_bridge import install_crypto_v39_risk_bridge
from crypto_v39_spread_bridge import install_crypto_v39_spread_bridge
from live_broker_capital import install_live_broker_capital_sizing
from live_v39_broker_bridge import install_live_v39_broker_capital_bridge
from paper_execution_accounting import install_paper_execution_accounting
from paper_execution_reality import install_paper_execution_reality
from paper_fee_policy import install_fee_aware_fifo_policy
from readiness_observability import emit_capital_readiness_report
from robinhood_quote_compat import install_robinhood_quote_compat
from runtime_integrity_patch import install_runtime_integrity_patch
from runtime_provider_reliability import install_yahoo_runtime_reliability
from strategic_core_rebalance_runtime import install_strategic_core_rebalance_producer
from structured_logging import configure_structured_logging


configure_structured_logging(os.getenv("LOG_LEVEL", "INFO"))
logger = logging.getLogger("crypto-worker")

install_yahoo_runtime_reliability()
install_runtime_integrity_patch(market_worker)
install_core_rebalance_score_compat()
install_strategic_core_rebalance_producer(market_worker)
install_binance_us_reference_fallback()
install_robinhood_quote_compat()
install_crypto_v39_spread_bridge(market_worker)
install_crypto_execution_quote_guard(market_worker)
install_v39_quote_verification_sampler(market_worker)
install_live_broker_capital_sizing()
install_live_v39_broker_capital_bridge(market_worker)
install_crypto_short_horizon_forecast(market_worker)
install_crypto_v39_risk_bridge(market_worker)
install_core_rebalance_optimizer_trace(market_worker)
install_core_rebalance_observability(market_worker)
if os.getenv("EXECUTION_MODE", "paper").strip().lower() == "paper":
    install_paper_execution_reality(market_worker)
    install_paper_execution_accounting(market_worker)
    install_fee_aware_fifo_policy()


def run_robinhood_startup_preflight() -> None:
    """Run read-only broker connectivity plus durable restart reconciliation."""
    if os.getenv("ROBINHOOD_CRYPTO_ENABLED", "false").strip().lower() != "true":
        logger.info("ROBINHOOD PREFLIGHT | connection=DISABLED | live_trading=DISARMED")
        return

    reconciliation_status = "NOT_RUN"
    try:
        from broker_order_journal import PersistentOrderJournal
        from broker_reconciliation import reconcile_persistent_journal
        from robinhood_crypto_api import RobinhoodCryptoClient, preflight

        client = RobinhoodCryptoClient()
        journal = PersistentOrderJournal()
        result = preflight(client, journal)

        if result.get("ROBINHOOD AUTH") == "PASS" and result.get("ACCOUNT STATUS") == "PASS":
            account = client.account_details()
            account_number = str(account.get("account_number") or "").strip()
            if account_number:
                remote_orders = client.orders(account_number)
                reconciliation = reconcile_persistent_journal(
                    journal,
                    remote_orders,
                    account_number_present=True,
                )
                reconciliation_status = str(reconciliation.get("status") or "UNKNOWN")
                if reconciliation_status != "PASS":
                    result["ORDER JOURNAL"] = "FAIL"
            else:
                reconciliation_status = "FAIL_CLOSED"
                result["ORDER JOURNAL"] = "FAIL"
    except Exception as exc:
        logger.warning(
            "ROBINHOOD PREFLIGHT | connection=ERROR | auth=FAIL | reconciliation=FAIL_CLOSED | "
            "live_trading=DISARMED | reason=%s",
            exc.__class__.__name__,
        )
        return

    buying_power_state = str(result.get("BUYING POWER STATE") or "NOT_CHECKED")

    logger.info(
        "ROBINHOOD PREFLIGHT | connection=%s | auth=%s | account=%s | crypto=%s | "
        "pairs=%s | quote=%s | quote_reason=%s | buying_power=%s | buying_power_state=%s | holdings=%s | "
        "orders=%s | journal=%s | reconciliation=%s | live_trading=%s | reason=%s",
        result.get("ROBINHOOD CONNECTION", "UNKNOWN"),
        result.get("ROBINHOOD AUTH", "UNKNOWN"),
        result.get("ACCOUNT STATUS", "UNKNOWN"),
        result.get("CRYPTO STATUS", "UNKNOWN"),
        result.get("TRADABLE PAIR COUNT", 0),
        result.get("QUOTE CHECK", "UNKNOWN"),
        result.get("QUOTE CHECK REASON", "DIRECT_BTC_OR_PRIMARY_PAIR"),
        result.get("BUYING POWER CHECK", "UNKNOWN"),
        buying_power_state,
        result.get("HOLDINGS CHECK", "UNKNOWN"),
        result.get("ORDERS CHECK", "UNKNOWN"),
        result.get("ORDER JOURNAL", "UNKNOWN"),
        reconciliation_status,
        result.get("LIVE TRADING ARMED/DISARMED", "DISARMED"),
        str(result.get("reason") or "")[:240],
    )


if __name__ == "__main__":
    prepare_capital_readiness_runtime(market_worker, "crypto")
    run_robinhood_startup_preflight()
    emit_capital_readiness_report(logger)
    market_worker.run_worker("crypto")
