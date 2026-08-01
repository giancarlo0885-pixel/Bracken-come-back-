import logging
import os

from market_worker import run_worker
from structured_logging import configure_structured_logging

configure_structured_logging(os.getenv("LOG_LEVEL", "INFO"))

if __name__ == "__main__":
    run_worker("cash")
