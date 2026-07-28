"""Compatibility entry point for Railway worker services.

Prefer stock_worker.py and crypto_worker.py as separate services. This file remains
available for older Railway configurations that set WORKER_MARKET.
"""
from __future__ import annotations

import logging
import os

from market_worker import run_worker


logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO").upper(),
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)


if __name__ == "__main__":
    requested = os.getenv("WORKER_MARKET", "cash").strip().lower()
    market = "cash" if requested in {"cash", "stock", "stocks"} else "crypto"
    run_worker(market)
