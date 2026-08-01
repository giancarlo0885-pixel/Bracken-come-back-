from __future__ import annotations

import os

APP_NAME = "GARIBALDI MARKET ORACLE™"
DATABASE_PATH = os.getenv("DATABASE_PATH", "oracle.db")
# Institutional paper-broker capital. These are simulated balances only.
# Existing paper portfolios are upgraded in place while preserving their P/L.
STARTING_BALANCE = float(os.getenv("STARTING_BALANCE", "10000000"))
STOCK_STARTING_BALANCE = float(os.getenv("STOCK_STARTING_BALANCE", "10000000"))
CRYPTO_STARTING_BALANCE = float(os.getenv("CRYPTO_STARTING_BALANCE", "5000000"))
PAPER_BROKER_MODE = os.getenv("PAPER_BROKER_MODE", "true").lower() == "true"
PAPER_CAPITAL_UPGRADE = os.getenv("PAPER_CAPITAL_UPGRADE", "false").lower() == "true"
PAPER_BROKER_PROFILE = os.getenv("PAPER_BROKER_PROFILE", "institutional-paper")
STOCK_PAPER_LEVERAGE = max(1.0, min(6.0, float(os.getenv("STOCK_PAPER_LEVERAGE", "4.0"))))
CRYPTO_PAPER_LEVERAGE = max(1.0, min(3.0, float(os.getenv("CRYPTO_PAPER_LEVERAGE", "2.0"))))
STOCK_MAINTENANCE_MARGIN_PCT = max(0.15, min(0.60, float(os.getenv("STOCK_MAINTENANCE_MARGIN_PCT", "0.25"))))
CRYPTO_MAINTENANCE_MARGIN_PCT = max(0.25, min(0.80, float(os.getenv("CRYPTO_MAINTENANCE_MARGIN_PCT", "0.50"))))
STOCK_MARGIN_INTEREST_APR = max(0.0, float(os.getenv("STOCK_MARGIN_INTEREST_APR", "0.065")))
CRYPTO_MARGIN_INTEREST_APR = max(0.0, float(os.getenv("CRYPTO_MARGIN_INTEREST_APR", "0.12")))
PAPER_MAX_MARGIN_UTILIZATION_PCT = max(0.25, min(0.95, float(os.getenv("PAPER_MAX_MARGIN_UTILIZATION_PCT", "0.82"))))
PAPER_MARGIN_WARNING_PCT = max(0.10, min(0.90, float(os.getenv("PAPER_MARGIN_WARNING_PCT", "0.70"))))
PAPER_MAX_MARKET_PARTICIPATION_PCT = max(0.001, min(0.05, float(os.getenv("PAPER_MAX_MARKET_PARTICIPATION_PCT", "0.01"))))
PAPER_MARGIN_INTEREST_ACCRUAL_SECONDS = max(60, int(os.getenv("PAPER_MARGIN_INTEREST_ACCRUAL_SECONDS", "300")))
API_CACHE_TTL_SECONDS = max(30, int(os.getenv("API_CACHE_TTL_SECONDS", "300")))
ROTATION_ENABLED = os.getenv("ROTATION_ENABLED", "true").lower() == "true"
ROTATION_MIN_SCORE_GAP = float(os.getenv("ROTATION_MIN_SCORE_GAP", "8"))
OPPORTUNITY_LIMIT = max(3, int(os.getenv("OPPORTUNITY_LIMIT", "12")))
WORKER_INTERVAL_SECONDS = max(15, int(os.getenv("WORKER_INTERVAL_SECONDS", "60")))

# V32 Live Pulse runtime. The workers use a fast position-risk pulse plus a
# deeper opportunity scan. This keeps open positions monitored continuously
# without running the expensive global research stack on every heartbeat.
REALTIME_MODE = os.getenv("REALTIME_MODE", "true").lower() == "true"
EXECUTION_MODE = os.getenv("EXECUTION_MODE", "paper").strip().lower()
UI_AUTO_REFRESH = os.getenv("UI_AUTO_REFRESH", "true").lower() == "true"
UI_REFRESH_SECONDS = max(5, int(os.getenv("UI_REFRESH_SECONDS", "15")))
STOCK_PULSE_SECONDS = max(5, int(os.getenv("STOCK_PULSE_SECONDS", "10")))
CRYPTO_PULSE_SECONDS = max(5, int(os.getenv("CRYPTO_PULSE_SECONDS", "5")))
STOCK_DEEP_SCAN_SECONDS = max(30, int(os.getenv("STOCK_DEEP_SCAN_SECONDS", "60")))
STOCK_CLOSED_SCAN_SECONDS = max(60, int(os.getenv("STOCK_CLOSED_SCAN_SECONDS", "120")))
CRYPTO_DEEP_SCAN_SECONDS = max(15, int(os.getenv("CRYPTO_DEEP_SCAN_SECONDS", "30")))
INTELLIGENCE_REFRESH_SECONDS = max(300, int(os.getenv("INTELLIGENCE_REFRESH_SECONDS", "900")))
REALTIME_CACHE_TTL_SECONDS = max(5, int(os.getenv("REALTIME_CACHE_TTL_SECONDS", "10")))
LIVE_SCAN_WORKERS = max(1, min(12, int(os.getenv("LIVE_SCAN_WORKERS", "5"))))
DEEP_ANALYSIS_CANDIDATES = max(10, int(os.getenv("DEEP_ANALYSIS_CANDIDATES", "35")))
LIVE_POSITION_PRICE_WORKERS = max(1, min(8, int(os.getenv("LIVE_POSITION_PRICE_WORKERS", "4"))))
LIVE_STATUS_STALE_SECONDS = max(30, int(os.getenv("LIVE_STATUS_STALE_SECONDS", "90")))

# V35 always-on paper execution. The workers never enter an idle state: a fast
# rolling scan evaluates a small candidate batch between the deeper global
# research cycles. Trades are still executed only when every data, forecast,
# risk, and portfolio gate passes.
ALWAYS_ON_TRADING = os.getenv("ALWAYS_ON_TRADING", "true").lower() == "true"
FAST_SIGNAL_SCAN_ENABLED = os.getenv("FAST_SIGNAL_SCAN_ENABLED", "true").lower() == "true"
STOCK_FAST_SCAN_SECONDS = max(5, int(os.getenv("STOCK_FAST_SCAN_SECONDS", "15")))
STOCK_CLOSED_FAST_SCAN_SECONDS = max(
    STOCK_FAST_SCAN_SECONDS,
    int(os.getenv("STOCK_CLOSED_FAST_SCAN_SECONDS", str(STOCK_CLOSED_SCAN_SECONDS))),
)
CRYPTO_FAST_SCAN_SECONDS = max(5, int(os.getenv("CRYPTO_FAST_SCAN_SECONDS", "10")))
FAST_SCAN_BATCH_SIZE = max(3, min(30, int(os.getenv("FAST_SCAN_BATCH_SIZE", "10"))))
FAST_SCAN_TOP_RANKED = max(3, min(50, int(os.getenv("FAST_SCAN_TOP_RANKED", "20"))))
WORKER_CYCLE_ERROR_BACKOFF_SECONDS = max(1, int(os.getenv("WORKER_CYCLE_ERROR_BACKOFF_SECONDS", "5")))
ENABLE_AUTOTRADE = os.getenv("ENABLE_AUTOTRADE", "true").lower() == "true"
ENABLE_NEWS = os.getenv("ENABLE_NEWS", "true").lower() == "true"
NEWS_PRIORITY_CANDIDATES = max(3, int(os.getenv("NEWS_PRIORITY_CANDIDATES", "8")))
NEWS_CACHE_TTL_SECONDS = max(900, int(os.getenv("NEWS_CACHE_TTL_SECONDS", "7200")))
NEWS_NEGATIVE_CACHE_TTL_SECONDS = max(300, int(os.getenv("NEWS_NEGATIVE_CACHE_TTL_SECONDS", "1800")))
NEWSAPI_MAX_REQUESTS_PER_12H = max(1, int(os.getenv("NEWSAPI_MAX_REQUESTS_PER_12H", "40")))
NEWSAPI_RATE_LIMIT_COOLDOWN_SECONDS = max(3600, int(os.getenv("NEWSAPI_RATE_LIMIT_COOLDOWN_SECONDS", "43200")))
MARKET_NEWS_CACHE_TTL_SECONDS = max(900, int(os.getenv("MARKET_NEWS_CACHE_TTL_SECONDS", "3600")))

# V23 adaptive trading profile. These defaults provide more room for valid
# entries while retaining hard concentration, correlation, and cash controls.
AGGRESSIVE_TRADING = os.getenv("AGGRESSIVE_TRADING", "true").lower() == "true"
CAPITAL_MIN_PRIORITY = float(os.getenv("CAPITAL_MIN_PRIORITY", "50"))
CAPITAL_MIN_TRADE_PCT = float(os.getenv("CAPITAL_MIN_TRADE_PCT", "0.015"))
PROVIDER_PERMISSION_COOLDOWN_SECONDS = max(3600, int(os.getenv("PROVIDER_PERMISSION_COOLDOWN_SECONDS", "86400")))

DEMO_MODE = os.getenv("DEMO_MODE", "true").lower() == "true"
AUTO_UPGRADE_EMPTY_PORTFOLIOS = (
    os.getenv("AUTO_UPGRADE_EMPTY_PORTFOLIOS", "true").lower() == "true"
)

# Broad economic coverage using liquid ETFs plus representative companies.
# The cash worker scans the major indexes, every primary U.S. sector, commodities,
# infrastructure, transportation, agriculture, defense, technology, healthcare,
# consumer markets, real estate, finance, energy, and emerging industries.
CASH_WATCHLIST = {
    # Broad market and style
    "SPY": "S&P 500 ETF",
    "QQQ": "Nasdaq-100 ETF",
    "IWM": "Russell 2000 ETF",
    "DIA": "Dow Jones ETF",
    "RSP": "Equal Weight S&P 500 ETF",
    "VTV": "Value ETF",
    "VUG": "Growth ETF",

    # Primary economic sectors
    "XLK": "Technology ETF",
    "XLF": "Financial ETF",
    "XLE": "Energy ETF",
    "XLV": "Healthcare ETF",
    "XLI": "Industrials ETF",
    "XLY": "Consumer Discretionary ETF",
    "XLP": "Consumer Staples ETF",
    "XLU": "Utilities ETF",
    "XLB": "Materials ETF",
    "XLRE": "Real Estate Sector ETF",
    "XLC": "Communication Services ETF",

    # Money, rates, currency, and real estate
    "TLT": "Long Treasury ETF",
    "IEF": "Intermediate Treasury ETF",
    "HYG": "High Yield Bond ETF",
    "UUP": "US Dollar ETF",
    "VNQ": "Real Estate ETF",
    "ITB": "Home Construction ETF",

    # Precious metals, industrial metals, mining, and strategic materials
    "GLD": "Gold ETF",
    "SLV": "Silver ETF",
    "COPX": "Copper Miners ETF",
    "PICK": "Global Metals and Mining ETF",
    "LIT": "Lithium and Battery Technology ETF",
    "REMX": "Rare Earth and Strategic Metals ETF",
    "URA": "Uranium ETF",

    # Oil, gas, clean energy, and utilities
    "XOM": "Exxon Mobil",
    "CVX": "Chevron",
    "OIH": "Oil Services ETF",
    "UNG": "Natural Gas ETF",
    "ICLN": "Clean Energy ETF",
    "TAN": "Solar ETF",
    "NEE": "NextEra Energy",

    # Technology, AI, semiconductors, cloud, cyber, and robotics
    "AAPL": "Apple",
    "MSFT": "Microsoft",
    "NVDA": "NVIDIA",
    "AMD": "AMD",
    "AVGO": "Broadcom",
    "SMH": "Semiconductor ETF",
    "AMZN": "Amazon",
    "META": "Meta",
    "GOOGL": "Alphabet",
    "PLTR": "Palantir",
    "CLOU": "Cloud Computing ETF",
    "CIBR": "Cybersecurity ETF",
    "BOTZ": "Robotics and AI ETF",
    "ARKQ": "Autonomous Technology ETF",
    "IONQ": "IonQ",
    "GLW": "Corning",

    # Aerospace, space, defense, infrastructure, and construction
    "ITA": "Aerospace and Defense ETF",
    "PPA": "Aerospace and Defense ETF",
    "RKLB": "Rocket Lab",
    "XAR": "Aerospace and Defense ETF",
    "PAVE": "US Infrastructure ETF",
    "CAT": "Caterpillar",
    "DE": "Deere",

    # Agriculture, food, staples, retail, and restaurants
    "MOO": "Agribusiness ETF",
    "DBA": "Agriculture ETF",
    "ADM": "Archer Daniels Midland",
    "WMT": "Walmart",
    "COST": "Costco",
    "XLP": "Consumer Staples ETF",
    "MCD": "McDonald's",
    "SBUX": "Starbucks",

    # Banks, insurance, payments, and financial services
    "JPM": "JPMorgan",
    "BAC": "Bank of America",
    "KRE": "Regional Banks ETF",
    "KIE": "Insurance ETF",
    "V": "Visa",
    "MA": "Mastercard",

    # Healthcare, pharmaceuticals, biotech, and medical devices
    "LLY": "Eli Lilly",
    "JNJ": "Johnson & Johnson",
    "PFE": "Pfizer",
    "IBB": "Biotechnology ETF",
    "IHI": "Medical Devices ETF",

    # Transportation, railroads, logistics, shipping, and airlines
    "IYT": "Transportation ETF",
    "UNP": "Union Pacific",
    "CSX": "CSX",
    "FDX": "FedEx",
    "UPS": "UPS",
    "JETS": "Airlines ETF",
    "SEA": "Global Shipping ETF",

    # Autos and electric vehicles
    "TSLA": "Tesla",
    "F": "Ford",
    "GM": "General Motors",
    "DRIV": "Autonomous and Electric Vehicles ETF",

    # Water and essential infrastructure
    "PHO": "Water Resources ETF",
}

CRYPTO_WATCHLIST = {
    "BTC-USD": "Bitcoin",
    "ETH-USD": "Ethereum",
    "SOL-USD": "Solana",
    "XRP-USD": "XRP",
    "DOGE-USD": "Dogecoin",
    "ADA-USD": "Cardano",
    "AVAX-USD": "Avalanche",
    "LINK-USD": "Chainlink",
    "LTC-USD": "Litecoin",
    "DOT-USD": "Polkadot",
    "UNI-USD": "Uniswap",
    "AAVE-USD": "Aave",
    "ATOM-USD": "Cosmos",
    "NEAR-USD": "NEAR Protocol",
}

WATCHLISTS = {
    "cash": CASH_WATCHLIST,
    "crypto": CRYPTO_WATCHLIST,
}

# Aggressive simulated-trading defaults. Railway environment variables can
# override every value without another code change.
MAX_POSITION_FRACTION = float(os.getenv("MAX_POSITION_FRACTION", "0.10"))
MIN_TRADE_VALUE = float(os.getenv("MIN_TRADE_VALUE", "1.00"))
MAX_OPEN_POSITIONS = int(os.getenv("MAX_OPEN_POSITIONS", "40"))
STOP_LOSS_PCT = float(os.getenv("STOP_LOSS_PCT", "0.06"))
TAKE_PROFIT_PCT = float(os.getenv("TAKE_PROFIT_PCT", "0.10"))
TRAILING_STOP_PCT = float(os.getenv("TRAILING_STOP_PCT", "0.045"))
SIGNAL_BUY_THRESHOLD = float(os.getenv("SIGNAL_BUY_THRESHOLD", "0.58"))
SIGNAL_SELL_THRESHOLD = float(os.getenv("SIGNAL_SELL_THRESHOLD", "0.42"))
TRADE_COOLDOWN_MINUTES = int(os.getenv("TRADE_COOLDOWN_MINUTES", "15"))
MAX_DAILY_DRAWDOWN_PCT = float(os.getenv("MAX_DAILY_DRAWDOWN_PCT", "0.12"))
MIN_COUNCIL_AGREEMENT = float(os.getenv("MIN_COUNCIL_AGREEMENT", "0.52"))

# Extra settings consumed by oracle_bot.py through `from config import *`.
FLEXIBLE_COOLDOWN_FACTOR = float(os.getenv("FLEXIBLE_COOLDOWN_FACTOR", "0.10"))
HIGH_CONFIDENCE_THRESHOLD = float(
    os.getenv("HIGH_CONFIDENCE_THRESHOLD", "0.48")
)
HIGH_SCORE_THRESHOLD = float(os.getenv("HIGH_SCORE_THRESHOLD", "52.0"))
EXTRA_OPEN_POSITIONS = int(os.getenv("EXTRA_OPEN_POSITIONS", "6"))
MIN_CASH_RESERVE_PCT = float(os.getenv("MIN_CASH_RESERVE_PCT", "0.05"))
MAX_TRADE_VALUE_PCT = float(os.getenv("MAX_TRADE_VALUE_PCT", "0.08"))

# Oracle Quantitative Trade Standard (institutional-style, retail-executable)
ENABLE_QUANT_TRADE_STANDARD = os.getenv("ENABLE_QUANT_TRADE_STANDARD", "true").lower() == "true"
QUANT_MIN_QUALITY = float(os.getenv("QUANT_MIN_QUALITY", "68.0"))
QUANT_MIN_NET_EV_PCT = float(os.getenv("QUANT_MIN_NET_EV_PCT", "0.001"))

# =========================================================
# LIVE DECISION DATA-INTEGRITY GATES
# =========================================================
# A recommendation cannot be presented as a trade-ready BUY when the live
# quote, forecast, or data freshness is missing. These values are deliberately
# configurable so the UI and paper broker can stay active across global markets
# without treating stale/incomplete records as current opportunities.
DECISION_STOCK_MAX_AGE_MINUTES = max(5, int(os.getenv("DECISION_STOCK_MAX_AGE_MINUTES", "180")))
DECISION_CRYPTO_MAX_AGE_MINUTES = max(5, int(os.getenv("DECISION_CRYPTO_MAX_AGE_MINUTES", "45")))
MIN_ACTIONABLE_MOVE_STOCK_PCT = max(0.0, float(os.getenv("MIN_ACTIONABLE_MOVE_STOCK_PCT", "0.75")))
MIN_ACTIONABLE_MOVE_CRYPTO_PCT = max(0.0, float(os.getenv("MIN_ACTIONABLE_MOVE_CRYPTO_PCT", "1.25")))
REQUIRE_TARGET_FOR_BUY = os.getenv("REQUIRE_TARGET_FOR_BUY", "true").lower() == "true"
QUANT_MAX_SPREAD_PCT = float(os.getenv("QUANT_MAX_SPREAD_PCT", "0.006"))
QUANT_MAX_SLIPPAGE_PCT = float(os.getenv("QUANT_MAX_SLIPPAGE_PCT", "0.005"))
QUANT_ADVERSE_REJECT_SCORE = float(os.getenv("QUANT_ADVERSE_REJECT_SCORE", "70.0"))

# V11 Market Memory
ENABLE_MARKET_MEMORY = os.getenv("ENABLE_MARKET_MEMORY", "true").lower() == "true"
MEMORY_MIN_ANALOGS = max(3, int(os.getenv("MEMORY_MIN_ANALOGS", "5")))
MEMORY_MAX_ADJUSTMENT = float(os.getenv("MEMORY_MAX_ADJUSTMENT", "8.0"))
MEMORY_LOOKBACK_LIMIT = max(25, int(os.getenv("MEMORY_LOOKBACK_LIMIT", "300")))
MEMORY_VETO_WIN_RATE = float(os.getenv("MEMORY_VETO_WIN_RATE", "0.30"))
MEMORY_VETO_MIN_ANALOGS = max(MEMORY_MIN_ANALOGS, int(os.getenv("MEMORY_VETO_MIN_ANALOGS", "10")))

# V21 Worldwide Market Discovery
GLOBAL_SCANNER_ENABLED = os.getenv("GLOBAL_SCANNER_ENABLED", "true").lower() == "true"
GLOBAL_SCAN_SYMBOLS_PER_CYCLE = max(10, int(os.getenv("GLOBAL_SCAN_SYMBOLS_PER_CYCLE", "45")))
GLOBAL_ACTIVE_CANDIDATES = max(5, int(os.getenv("GLOBAL_ACTIVE_CANDIDATES", "20")))
GLOBAL_MIN_PRICE = float(os.getenv("GLOBAL_MIN_PRICE", "1.00"))
GLOBAL_MIN_AVG_DOLLAR_VOLUME = float(os.getenv("GLOBAL_MIN_AVG_DOLLAR_VOLUME", "5000000"))
GLOBAL_UNIVERSE_TTL_SECONDS = max(3600, int(os.getenv("GLOBAL_UNIVERSE_TTL_SECONDS", "86400")))

# V27 controlled paper-entry mode. This only relaxes the initial opportunity
# classification; hard cash, concentration, correlation and negative-EV vetoes
# remain enforced by the allocator and portfolio supercomputer.
PAPER_TRADING_BREATHING_ROOM = os.getenv("PAPER_TRADING_BREATHING_ROOM", "true").lower() == "true"
