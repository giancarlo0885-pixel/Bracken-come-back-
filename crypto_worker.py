import logging
import os

import market_worker
from crypto_execution_guard import install_crypto_execution_quote_guard
from paper_execution_reality import install_paper_execution_reality
from structured_logging import configure_structured_logging


configure_structured_logging(os.getenv("LOG_LEVEL", "INFO"))
install_crypto_execution_quote_guard(market_worker)
install_paper_execution_reality(market_worker)


if __name__ == "__main__":
    market_worker.run_worker("crypto")
