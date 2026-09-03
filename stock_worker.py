import logging
import os

import market_worker
from capital_readiness_runtime import prepare_capital_readiness_runtime
from migration_runtime_safety import install_migration_runtime_safety
from paper_autonomous_learning import install_paper_autonomous_learning
from paper_execution_accounting import install_paper_execution_accounting
from paper_execution_reality import install_paper_execution_reality
from paper_fee_policy import install_fee_aware_fifo_policy
from portfolio_valuation import install_closed_market_valuation_pulse
from runtime_integrity_patch import install_runtime_integrity_patch
from runtime_provider_reliability import install_yahoo_runtime_reliability
from stock_execution_repair import install_stock_execution_quote_repair
from stock_transient_quarantine_repair import install_stock_transient_quarantine_repair
from structured_logging import configure_structured_logging

configure_structured_logging(os.getenv("LOG_LEVEL", "INFO"))
logger = logging.getLogger("stock-worker")
install_migration_runtime_safety()
install_yahoo_runtime_reliability()
install_runtime_integrity_patch(market_worker)
install_paper_autonomous_learning()
install_closed_market_valuation_pulse(market_worker)
install_stock_execution_quote_repair(market_worker)
install_stock_transient_quarantine_repair(market_worker)
if os.getenv("EXECUTION_MODE", "paper").strip().lower() == "paper":
    install_paper_execution_reality(market_worker)
    install_paper_execution_accounting(market_worker)
    install_fee_aware_fifo_policy()

# Keep this entrypoint in the stock-worker deploy watch set when paper-learning
# runtime policy changes. Production source-sync marker: current main, 2026-09-03.
if __name__ == "__main__":
    logger.info("ORACLE STOCK SOURCE SYNC | current-main deployment active")
    prepare_capital_readiness_runtime(market_worker, "cash")
    market_worker.run_worker("cash")
