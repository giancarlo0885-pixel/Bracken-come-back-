import logging
import os

import market_worker
from portfolio_valuation import install_closed_market_valuation_pulse
from stock_execution_repair import install_stock_execution_quote_repair
from structured_logging import configure_structured_logging

configure_structured_logging(os.getenv("LOG_LEVEL", "INFO"))
install_closed_market_valuation_pulse(market_worker)
install_stock_execution_quote_repair(market_worker)

if __name__ == "__main__":
    market_worker.run_worker("cash")
