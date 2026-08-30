"""Compatibility entry point for Railway worker services.

Prefer stock_worker.py and crypto_worker.py as separate services. This file remains
available for older Railway configurations that set WORKER_MARKET.
"""
from __future__ import annotations

import logging
import os

import market_worker
from paper_execution_reality import install_paper_execution_reality
from portfolio_valuation import install_closed_market_valuation_pulse


logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO").upper(),
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)


if __name__ == "__main__":
    requested = os.getenv("WORKER_MARKET", "cash").strip().lower()
    market = "cash" if requested in {"cash", "stock", "stocks"} else "crypto"
    if market == "cash":
        install_closed_market_valuation_pulse(market_worker)
    install_paper_execution_reality(market_worker)
    market_worker.run_worker(market)
