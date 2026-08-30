from __future__ import annotations

import os

APP_NAME = "GARIBALDI MARKET ORACLE™"
DATABASE_PATH = os.getenv("DATABASE_PATH", "oracle.db")
MARKET_SCOPE = os.getenv("MARKET_SCOPE", "US_CRYPTO").strip().upper() or "US_CRYPTO"
# Small-account paper capital. Railway may override these values explicitly,
# but a clean deployment must never silently bootstrap institutional balances.
STARTING_BALANCE = float(os.getenv("STARTING_BALANCE", "2000"))
STOCK_STARTING_BALANCE = float(os.getenv("STOCK_STARTING_BALANCE", "2000"))
CRYPTO_STARTING_BALANCE = float(os.getenv("CRYPTO_STARTING_BALANCE", "2000"))
PAPER_BROKER_MODE = os.getenv("PAPER_BROKER_MODE", "true").lower() == "true"
PAPER_CAPITAL_UPGRADE = os.getenv("PAPER_CAPITAL_UPGRADE", "false").lower() == "true"
PAPER_BROKER_PROFILE = os.getenv("PAPER_BROKER_PROFILE", "small-account-paper")
STOCK_PAPER_LEVERAGE = max(1.0, min(6.0, float(os.getenv("STOCK_PAPER_LEVERAGE", "1.0"))))
CRYPTO_PAPER_LEVERAGE = max(1.0, min(3.0, float(os.getenv("CRYPTO_PAPER_LEVERAGE", "1.0"))))
STOCK_MAINTENANCE_MARGIN_PCT = max(0.15, min(0.60, float(os.getenv("STOCK_MAINTENANCE_MARGIN_PCT", "0.25"))))
CRYPTO_MAINTENANCE_MARGIN_PCT = max(0.25, min(0.80, float(os.getenv("CRYPTO_MAINTENANCE_MARGIN_PCT", "0.50"))))
STOCK_MARGIN_INTEREST_APR = max(0.0, float(os.getenv("STOCK_MARGIN_INTEREST_APR", "0.065")))
CRYPTO_MARGIN_INTEREST_APR = max(0.0, float(os.getenv("CRYPTO_MARGIN_INTEREST_APR", "0.12")))
PAPER_MAX_MARGIN_UTILIZATION_PCT = max(0.25, min(0.95, float(os.getenv("PAPER_MAX_MARGIN_UTILIZATION_PCT", "0.82"))))
PAPER_MARGIN_WARNING_PCT = max(0.10, min(0.90, float(os.getenv("PAPER_MARGIN_WARNING_PCT", "0.70"))))
PAPER_MAX_MARKET_PARTICIPATION_PCT = max(0.001, min(0.05, float(os.getenv("PAPER_MAX_MARKET_PARTICIPATION_PCT", "0.01"))))
PAPER_MARGIN_INTEREST_ACCRUAL_SECONDS = max(60, int(os.getenv("PAPER_MARGIN_INTEREST_ACCRUAL_SECONDS", "300")))
API_CACHE_TTL_SECONDS = max(30, int(os.getenv("API_CACHE_TTL_SECONDS", "300")))
ALPHA_VANTAGE_CACHE_TTL_SECONDS = max(30, int(os.getenv("ALPHA_VANTAGE_CACHE_TTL_SECONDS", "300")))
ALPHA_VANTAGE_FUNDAMENTALS_TTL_SECONDS = max(3600, int(os.getenv("ALPHA_VANTAGE_FUNDAMENTALS_TTL_SECONDS", "43200")))
ALPHA_VANTAGE_RATE_LIMIT_COOLDOWN_SECONDS = max(60, int(os.getenv("ALPHA_VANTAGE_RATE_LIMIT_COOLDOWN_SECONDS", "900")))
ALPHA_VANTAGE_MIN_REQUEST_INTERVAL_SECONDS = max(0.0, float(os.getenv("ALPHA_VANTAGE_MIN_REQUEST_INTERVAL_SECONDS", "12")))
ALPHA_VANTAGE_DAILY_REQUEST_BUDGET = max(0, int(os.getenv("ALPHA_VANTAGE_DAILY_REQUEST_BUDGET", "20")))
ALPHA_VANTAGE_PREMIUM = os.getenv("ALPHA_VANTAGE_PREMIUM", "false").lower() == "true"
POLYGON_DAILY_REQUEST_BUDGET = max(0, int(os.getenv("POLYGON_DAILY_REQUEST_BUDGET", "1000")))
FINNHUB_DAILY_REQUEST_BUDGET = max(0, int(os.getenv("FINNHUB_DAILY_REQUEST_BUDGET", "200")))
EODHD_DAILY_REQUEST_BUDGET = max(0, int(os.getenv("EODHD_DAILY_REQUEST_BUDGET", "200")))
FINNHUB_CRYPTO_DAILY_REQUEST_BUDGET = max(0, int(os.getenv("FINNHUB_CRYPTO_DAILY_REQUEST_BUDGET", "0")))
EODHD_CRYPTO_DAILY_REQUEST_BUDGET = max(0, int(os.getenv("EODHD_CRYPTO_DAILY_REQUEST_BUDGET", "0")))
NEWSAPI_DAILY_REQUEST_BUDGET = max(0, int(os.getenv("NEWSAPI_DAILY_REQUEST_BUDGET", "40")))
YAHOO_DAILY_REQUEST_BUDGET = max(0, int(os.getenv("YAHOO_DAILY_REQUEST_BUDGET", "0")))
PROVIDER_DAILY_REQUEST_BUDGETS = {
    "alpha vantage": ALPHA_VANTAGE_DAILY_REQUEST_BUDGET,
    "polygon": POLYGON_DAILY_REQUEST_BUDGET,
    "finnhub": FINNHUB_DAILY_REQUEST_BUDGET,
    "eodhd": EODHD_DAILY_REQUEST_BUDGET,
    "newsapi": NEWSAPI_DAILY_REQUEST_BUDGET,
    "yahoo": YAHOO_DAILY_REQUEST_BUDGET,
}
FINNHUB_CRYPTO_EXCHANGES = [
    value.strip().upper()
    for value in os.getenv("FINNHUB_CRYPTO_EXCHANGES", "BINANCE,COINBASE,KRAKEN").split(",")
    if value.strip()
]
PROVIDER_CAPABILITY_DAILY_BUDGETS = {
    ("alpha vantage", "history"): ALPHA_VANTAGE_DAILY_REQUEST_BUDGET,
    ("alpha vantage", "us_history"): ALPHA_VANTAGE_DAILY_REQUEST_BUDGET,
    ("alpha vantage", "crypto"): 0,
    ("alpha vantage", "quote"): ALPHA_VANTAGE_DAILY_REQUEST_BUDGET,
    ("alpha vantage", "symbol_search"): ALPHA_VANTAGE_DAILY_REQUEST_BUDGET,
    ("alpha vantage", "news"): ALPHA_VANTAGE_DAILY_REQUEST_BUDGET,
    ("polygon", "history"): POLYGON_DAILY_REQUEST_BUDGET,
    ("polygon", "us_history"): POLYGON_DAILY_REQUEST_BUDGET,
    ("polygon", "crypto"): POLYGON_DAILY_REQUEST_BUDGET,
    ("polygon", "quote"): POLYGON_DAILY_REQUEST_BUDGET,
    ("polygon", "movers"): POLYGON_DAILY_REQUEST_BUDGET,
    ("finnhub", "history"): FINNHUB_DAILY_REQUEST_BUDGET,
    ("finnhub", "us_history"): FINNHUB_DAILY_REQUEST_BUDGET,
    ("finnhub", "crypto"): FINNHUB_CRYPTO_DAILY_REQUEST_BUDGET,
    ("finnhub", "quote"): FINNHUB_DAILY_REQUEST_BUDGET,
    ("finnhub", "earnings"): FINNHUB_DAILY_REQUEST_BUDGET,
    ("eodhd", "history"): EODHD_DAILY_REQUEST_BUDGET,
    ("eodhd", "us_history"): EODHD_DAILY_REQUEST_BUDGET,
    ("eodhd", "crypto"): EODHD_CRYPTO_DAILY_REQUEST_BUDGET,
    ("eodhd", "quote"): EODHD_DAILY_REQUEST_BUDGET,
    ("newsapi", "news"): NEWSAPI_DAILY_REQUEST_BUDGET,
    ("yahoo", "history"): YAHOO_DAILY_REQUEST_BUDGET,
    ("yahoo", "quote"): YAHOO_DAILY_REQUEST_BUDGET,
}
PROVIDER_RATE_LIMIT_COOLDOWN_SECONDS = max(60, int(os.getenv("PROVIDER_RATE_LIMIT_COOLDOWN_SECONDS", "900")))
UNAVAILABLE_SYMBOL_COOLDOWN_SECONDS = max(60, int(os.getenv("UNAVAILABLE_SYMBOL_COOLDOWN_SECONDS", "1800")))
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
WORKER_DB_READY_INITIAL_DELAY_SECONDS = max(1, int(os.getenv("WORKER_DB_READY_INITIAL_DELAY_SECONDS", "2")))
WORKER_DB_READY_MAX_DELAY_SECONDS = max(WORKER_DB_READY_INITIAL_DELAY_SECONDS, int(os.getenv("WORKER_DB_READY_MAX_DELAY_SECONDS", "30")))
DATABASE_MAINTENANCE_INTERVAL_SECONDS = max(300, int(os.getenv("DATABASE_MAINTENANCE_INTERVAL_SECONDS", "21600")))
DATABASE_RETENTION_BATCH_SIZE = max(100, int(os.getenv("DATABASE_RETENTION_BATCH_SIZE", "1000")))
_database_volume_capacity_raw = os.getenv("DATABASE_VOLUME_CAPACITY_GB", "").strip()
DATABASE_VOLUME_CAPACITY_GB = float(_database_volume_capacity_raw) if _database_volume_capacity_raw else 0.0
ENABLE_AUTOTRADE = os.getenv("ENABLE_AUTOTRADE", "false").lower() == "true"
# V36 separates stock and crypto execution switches. Missing environment
# variables default to false so Railway can continue scanning/advising without
# accidentally enabling broker mutations. ENABLE_AUTOTRADE remains a legacy
# global kill switch and must also be true.
ENABLE_STOCK_AUTOTRADE = os.getenv("ENABLE_STOCK_AUTOTRADE", "false").lower() == "true"
ENABLE_CRYPTO_AUTOTRADE = os.getenv("ENABLE_CRYPTO_AUTOTRADE", "false").lower() == "true"
ENABLE_NEW_ENTRIES = os.getenv("ENABLE_NEW_ENTRIES", "false").lower() == "true"
ENABLE_AUTOMATED_EXITS = os.getenv("ENABLE_AUTOMATED_EXITS", "false").lower() == "true"
ENABLE_PORTFOLIO_ROTATION = os.getenv("ENABLE_PORTFOLIO_ROTATION", "false").lower() == "true"
ENABLE_BROKER_SUBMISSION = os.getenv("ENABLE_BROKER_SUBMISSION", "false").lower() == "true"
ENABLE_OPENAI = os.getenv("ENABLE_OPENAI", "false").lower() == "true"
GLOBAL_KILL_SWITCH = os.getenv("GLOBAL_KILL_SWITCH", "false").lower() == "true"
LIVE_TRADING_ARMED = os.getenv("LIVE_TRADING_ARMED", "false").lower() == "true"
LIVE_ORDER_APPROVAL_MODE = os.getenv("LIVE_ORDER_APPROVAL_MODE", "manual").strip().lower()
BROKER_MODE = os.getenv("BROKER_MODE", "paper").strip().lower()
LIVE_TRADING_KILL_SWITCH = os.getenv("LIVE_TRADING_KILL_SWITCH", "true").lower() == "true"
LIVE_MAX_SINGLE_ORDER_DOLLARS = max(0.0, float(os.getenv("LIVE_MAX_SINGLE_ORDER_DOLLARS", "100")))
LIVE_MAX_DAILY_NEW_EXPOSURE_DOLLARS = max(0.0, float(os.getenv("LIVE_MAX_DAILY_NEW_EXPOSURE_DOLLARS", "250")))
LIVE_MAX_DAILY_LOSS_DOLLARS = max(0.0, float(os.getenv("LIVE_MAX_DAILY_LOSS_DOLLARS", "50")))
LIVE_MAX_POSITION_PCT = max(0.0, min(1.0, float(os.getenv("LIVE_MAX_POSITION_PCT", "0.05"))))
LIVE_MAX_TOTAL_DEPLOYED_PCT = max(0.0, min(1.0, float(os.getenv("LIVE_MAX_TOTAL_DEPLOYED_PCT", "0.25"))))
PAPER_TAX_LOT_METHOD = os.getenv("PAPER_TAX_LOT_METHOD", "FIFO").strip().upper() or "FIFO"
MAX_RECONCILIATION_DIFFERENCE_PCT = max(
    0.0,
    min(0.10, float(os.getenv("MAX_RECONCILIATION_DIFFERENCE_PCT", "0.005"))),
)
ROBINHOOD_CRYPTO_ENABLED = os.getenv("ROBINHOOD_CRYPTO_ENABLED", "false").lower() == "true"
ROBINHOOD_CRYPTO_API_KEY = os.getenv("ROBINHOOD_CRYPTO_API_KEY", "").strip()
ROBINHOOD_CRYPTO_PRIVATE_KEY_BASE64 = os.getenv("ROBINHOOD_CRYPTO_PRIVATE_KEY_BASE64", "").strip()
ROBINHOOD_CRYPTO_API_VERSION = os.getenv("ROBINHOOD_CRYPTO_API_VERSION", "v2").strip()
ROBINHOOD_CRYPTO_BASE_URL = os.getenv("ROBINHOOD_CRYPTO_BASE_URL", "https://trading.robinhood.com").strip().rstrip("/")
ROBINHOOD_CRYPTO_API_MODE = os.getenv("ROBINHOOD_CRYPTO_API_MODE", "disabled").strip().lower()
CRYPTO_PRIORITY_WEIGHT = max(0.0, min(1.0, float(os.getenv("CRYPTO_PRIORITY_WEIGHT", "0.70"))))
STOCK_PRIORITY_WEIGHT = max(0.0, min(1.0, float(os.getenv("STOCK_PRIORITY_WEIGHT", "0.30"))))
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

CRYPTO_CORE_WEIGHTS = {
    "BTC-USD": 0.40,
    "ETH-USD": 0.25,
    "XRP-USD": 0.10,
    "SOL-USD": 0.10,
    "BNB-USD": 0.05,
    "DOGE-USD": 0.04,
    "ADA-USD": 0.03,
    "AVAX-USD": 0.02,
    "LINK-USD": 0.01,
}

CRYPTO_WATCHLIST = {
    "BTC-USD": "Bitcoin",
    "ETH-USD": "Ethereum",
    "XRP-USD": "XRP",
    "SOL-USD": "Solana",
    "BNB-USD": "BNB",
    "DOGE-USD": "Dogecoin",
    "ADA-USD": "Cardano",
    "AVAX-USD": "Avalanche",
    "LINK-USD": "Chainlink",
    "LTC-USD": "Litecoin",
    "DOT-USD": "Polkadot",
    "ATOM-USD": "Cosmos",
    "NEAR-USD": "NEAR Protocol",
    "AAVE-USD": "Aave",
    "UNI-USD": "Uniswap",
    "BCH-USD": "Bitcoin Cash",
    "ETC-USD": "Ethereum Classic",
    "FIL-USD": "Filecoin",
    "ARB-USD": "Arbitrum",
    "OP-USD": "Optimism",
    "INJ-USD": "Injective",
    "SUI-USD": "Sui",
    "APT-USD": "Aptos",
    "RENDER-USD": "Render",
    "FET-USD": "Fetch.ai",
    "ICP-USD": "Internet Computer",
    "HBAR-USD": "Hedera",
    "ALGO-USD": "Algorand",
    "MATIC-USD": "Polygon",
    "PEPE-USD": "Pepe",
    "SHIB-USD": "Shiba Inu",
}

WATCHLISTS = {
    "cash": CASH_WATCHLIST,
    "crypto": CRYPTO_WATCHLIST,
}

CRYPTO_ALWAYS_INVESTED = os.getenv("CRYPTO_ALWAYS_INVESTED", "true").lower() == "true"
CRYPTO_CORE_TARGET_PCT = max(0.0, min(1.0, float(os.getenv("CRYPTO_CORE_TARGET_PCT", "0.30"))))
CRYPTO_TACTICAL_MAX_PCT = max(0.0, min(1.0, float(os.getenv("CRYPTO_TACTICAL_MAX_PCT", "0.60"))))
CRYPTO_MIN_CASH_RESERVE_PCT = max(0.0, min(1.0, float(os.getenv("CRYPTO_MIN_CASH_RESERVE_PCT", "0.10"))))
CRYPTO_DYNAMIC_UNIVERSE_SIZE = max(0, int(os.getenv("CRYPTO_DYNAMIC_UNIVERSE_SIZE", "40")))
CRYPTO_MAX_ACTIVE_SCAN_SYMBOLS = max(1, int(os.getenv("CRYPTO_MAX_ACTIVE_SCAN_SYMBOLS", "50")))
CRYPTO_MIN_24H_DOLLAR_VOLUME = max(0.0, float(os.getenv("CRYPTO_MIN_24H_DOLLAR_VOLUME", "25000000")))
CRYPTO_MAX_SPREAD_PCT = max(0.0, float(os.getenv("CRYPTO_MAX_SPREAD_PCT", "0.02")))
MAX_SINGLE_CRYPTO_TACTICAL_POSITION_PCT = max(
    0.0,
    min(1.0, float(os.getenv("MAX_SINGLE_CRYPTO_TACTICAL_POSITION_PCT", "0.12"))),
)
CRYPTO_ROTATION_MIN_SCORE_IMPROVEMENT = max(0.0, float(os.getenv("CRYPTO_ROTATION_MIN_SCORE_IMPROVEMENT", "7")))
CRYPTO_FULL_SCAN_SECONDS = max(30, int(os.getenv("CRYPTO_FULL_SCAN_SECONDS", "120")))
CRYPTO_SYMBOL_COOLDOWN_MINUTES = max(0, int(os.getenv("CRYPTO_SYMBOL_COOLDOWN_MINUTES", "20")))
CRYPTO_ROTATION_COOLDOWN_MINUTES = max(0, int(os.getenv("CRYPTO_ROTATION_COOLDOWN_MINUTES", "30")))
CRYPTO_STOP_OUT_COOLDOWN_MINUTES = max(0, int(os.getenv("CRYPTO_STOP_OUT_COOLDOWN_MINUTES", "60")))
CRYPTO_BREAKEVEN_TRIGGER_R = max(0.0, float(os.getenv("CRYPTO_BREAKEVEN_TRIGGER_R", "1.0")))
CRYPTO_TRAILING_STOP_TRIGGER_R = max(0.0, float(os.getenv("CRYPTO_TRAILING_STOP_TRIGGER_R", "1.5")))
CRYPTO_TIER_SIZE_MULTIPLIERS = {"A": 1.00, "B": 0.60, "C": 0.30}
CRYPTO_REGIMES = {"risk_on", "neutral", "risk_off", "high_volatility"}
MAX_PORTFOLIO_RISK_PER_TRADE = max(0.0, min(0.10, float(os.getenv("MAX_PORTFOLIO_RISK_PER_TRADE", "0.01"))))
MAX_TOTAL_DEPLOYED_PCT = max(0.0, min(1.0, float(os.getenv("MAX_TOTAL_DEPLOYED_PCT", "0.90"))))
MIN_TRADE_NOTIONAL = max(0.0, float(os.getenv("MIN_TRADE_NOTIONAL", "2.00")))
ENABLE_FRACTIONAL_EQUITIES = os.getenv("ENABLE_FRACTIONAL_EQUITIES", "true").lower() == "true"
ENABLE_FRACTIONAL_CRYPTO = os.getenv("ENABLE_FRACTIONAL_CRYPTO", "true").lower() == "true"
SMALL_ACCOUNT_THRESHOLD = max(0.0, float(os.getenv("SMALL_ACCOUNT_THRESHOLD", "500")))
LARGE_ACCOUNT_THRESHOLD = max(SMALL_ACCOUNT_THRESHOLD, float(os.getenv("LARGE_ACCOUNT_THRESHOLD", "100000")))
MAX_POSITION_VS_DAILY_DOLLAR_VOLUME_PCT = max(
    0.0,
    min(0.05, float(os.getenv("MAX_POSITION_VS_DAILY_DOLLAR_VOLUME_PCT", "0.001"))),
)
MAX_SINGLE_STOCK_POSITION_PCT = max(0.0, min(1.0, float(os.getenv("MAX_SINGLE_STOCK_POSITION_PCT", "0.12"))))
STOCK_MIN_CASH_RESERVE_PCT = max(0.0, min(1.0, float(os.getenv("STOCK_MIN_CASH_RESERVE_PCT", "0.15"))))
STOCK_ALWAYS_INVESTED = os.getenv("STOCK_ALWAYS_INVESTED", "true").lower() == "true"
STOCK_CORE_TARGET_PCT = max(0.0, min(1.0, float(os.getenv("STOCK_CORE_TARGET_PCT", "0.30"))))
STOCK_TACTICAL_MAX_PCT = max(0.0, min(1.0, float(os.getenv("STOCK_TACTICAL_MAX_PCT", "0.55"))))
STOCK_CORE_WEIGHTS = {
    "SPY": max(0.0, float(os.getenv("STOCK_CORE_SPY_WEIGHT", "0.45"))),
    "QQQ": max(0.0, float(os.getenv("STOCK_CORE_QQQ_WEIGHT", "0.35"))),
    "IWM": max(0.0, float(os.getenv("STOCK_CORE_IWM_WEIGHT", "0.10"))),
    "DIA": max(0.0, float(os.getenv("STOCK_CORE_DIA_WEIGHT", "0.10"))),
}
MAX_STOCK_TACTICAL_POSITIONS = max(1, int(os.getenv("MAX_STOCK_TACTICAL_POSITIONS", "8")))
MAX_STOCK_SECTOR_EXPOSURE_PCT = max(
    0.0,
    min(1.0, float(os.getenv("MAX_STOCK_SECTOR_EXPOSURE_PCT", os.getenv("MAX_SECTOR_EXPOSURE_PCT", "0.30")))),
)
MIN_STOCK_PRICE = max(0.0, float(os.getenv("MIN_STOCK_PRICE", "3.00")))
MIN_AVG_DOLLAR_VOLUME = max(0.0, float(os.getenv("MIN_AVG_DOLLAR_VOLUME", "20000000")))
MIN_AVG_VOLUME = max(0.0, float(os.getenv("MIN_AVG_VOLUME", "500000")))
MAX_STOCK_SPREAD_PCT = max(0.0, float(os.getenv("MAX_STOCK_SPREAD_PCT", os.getenv("MAX_SPREAD_PCT", "0.015"))))
STOCK_ROTATION_MIN_SCORE_IMPROVEMENT = max(0.0, float(os.getenv("STOCK_ROTATION_MIN_SCORE_IMPROVEMENT", "8")))
MAX_ENTRY_EXTENSION_FROM_VWAP_PCT = max(0.0, float(os.getenv("MAX_ENTRY_EXTENSION_FROM_VWAP_PCT", "0.06")))
MAX_SINGLE_BAR_SPIKE_PCT = max(0.0, float(os.getenv("MAX_SINGLE_BAR_SPIKE_PCT", "0.08")))
ENABLE_STOCK_REGULAR_HOURS = os.getenv("ENABLE_STOCK_REGULAR_HOURS", "true").lower() == "true"
ENABLE_STOCK_EXTENDED_HOURS = os.getenv("ENABLE_STOCK_EXTENDED_HOURS", "true").lower() == "true"
TIER_SIZE_MULTIPLIERS = {"A": 1.00, "B": 0.60, "C": 0.30}
MARKET_REGIME_SIZE_MULTIPLIERS = {"risk_on": 1.00, "neutral": 0.75, "risk_off": 0.40, "high_volatility": 0.35}

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
MAX_SECTOR_EXPOSURE_PCT = float(os.getenv("MAX_SECTOR_EXPOSURE_PCT", "0.35"))

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
FORECAST_MIN_VALIDATION_SAMPLES = max(0, int(os.getenv("FORECAST_MIN_VALIDATION_SAMPLES", "30")))
FORECAST_MIN_DIRECTIONAL_ACCURACY = max(0.0, min(1.0, float(os.getenv("FORECAST_MIN_DIRECTIONAL_ACCURACY", "0.52"))))
FORECAST_MAX_CALIBRATION_ERROR = max(0.0, min(1.0, float(os.getenv("FORECAST_MAX_CALIBRATION_ERROR", "0.18"))))
FORECAST_MIN_DATA_QUALITY_SCORE = max(0.0, min(100.0, float(os.getenv("FORECAST_MIN_DATA_QUALITY_SCORE", "55"))))
FORECAST_MODEL_VERSION = os.getenv("FORECAST_MODEL_VERSION", "v36-timeframe-aware")
PRICE_CONSENSUS_ENABLED = os.getenv("PRICE_CONSENSUS_ENABLED", "false").lower() == "true"
PRICE_CONSENSUS_MAX_DIFF_PCT = max(0.0, float(os.getenv("PRICE_CONSENSUS_MAX_DIFF_PCT", "0.50")))
ADVISOR_MODEL_VERSION = os.getenv("ADVISOR_MODEL_VERSION", "v36-advisor-foundation")
ADVISOR_RECOMMENDATION_TTL_MINUTES = max(5, int(os.getenv("ADVISOR_RECOMMENDATION_TTL_MINUTES", "120")))
MAX_DAILY_TURNOVER_PCT = max(0.0, float(os.getenv("MAX_DAILY_TURNOVER_PCT", "0.20")))
MAX_NEW_ENTRIES_PER_DAY = max(0, int(os.getenv("MAX_NEW_ENTRIES_PER_DAY", "3")))
MAX_WEEKLY_LOSS_PCT = max(0.0, float(os.getenv("MAX_WEEKLY_LOSS_PCT", "0.18")))
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
GLOBAL_CANDIDATE_TTL_SECONDS = max(60, int(os.getenv("GLOBAL_CANDIDATE_TTL_SECONDS", "1800")))
GLOBAL_INCLUDE_PROVIDER_DISCOVERY = os.getenv("GLOBAL_INCLUDE_PROVIDER_DISCOVERY", "true").lower() == "true"
GLOBAL_CORE_SYMBOLS_PER_CYCLE = max(3, int(os.getenv("GLOBAL_CORE_SYMBOLS_PER_CYCLE", "12")))
GLOBAL_ETF_SYMBOLS_PER_CYCLE = max(3, int(os.getenv("GLOBAL_ETF_SYMBOLS_PER_CYCLE", "8")))
GLOBAL_MAJOR_MOVER_MIN_CHANGE_PCT = float(os.getenv("GLOBAL_MAJOR_MOVER_MIN_CHANGE_PCT", "3.0"))
GLOBAL_GAP_MOVER_MIN_CHANGE_PCT = float(os.getenv("GLOBAL_GAP_MOVER_MIN_CHANGE_PCT", "2.0"))
GLOBAL_UNUSUAL_VOLUME_MIN_RATIO = float(os.getenv("GLOBAL_UNUSUAL_VOLUME_MIN_RATIO", "1.8"))

# V38 Global Wall Street Pit mode. These settings coordinate broad market
# surveillance and paper-capital planning while preserving every execution gate.
GLOBAL_PIT_MODE = os.getenv("GLOBAL_PIT_MODE", "true").lower() == "true"
GLOBAL_PIT_FAST_LOOP_SECONDS = max(3, int(os.getenv("GLOBAL_PIT_FAST_LOOP_SECONDS", "5")))
GLOBAL_PIT_MARKET_LOOP_SECONDS = max(30, int(os.getenv("GLOBAL_PIT_MARKET_LOOP_SECONDS", "60")))
GLOBAL_PIT_DEEP_RESEARCH_SECONDS = max(300, int(os.getenv("GLOBAL_PIT_DEEP_RESEARCH_SECONDS", "900")))
GLOBAL_PIT_TARGET_INVESTED_PCT = max(0.0, min(0.95, float(os.getenv("GLOBAL_PIT_TARGET_INVESTED_PCT", "0.95"))))
GLOBAL_PIT_RESERVE_PCT = max(0.05, min(0.50, float(os.getenv("GLOBAL_PIT_RESERVE_PCT", "0.05"))))
GLOBAL_PIT_MAX_POSITION_PCT = max(0.01, min(0.10, float(os.getenv("GLOBAL_PIT_MAX_POSITION_PCT", "0.10"))))
GLOBAL_PIT_PREFERRED_POSITION_PCT = max(0.01, min(GLOBAL_PIT_MAX_POSITION_PCT, float(os.getenv("GLOBAL_PIT_PREFERRED_POSITION_PCT", "0.08"))))
GLOBAL_PIT_HOT_ATTENTION_SCORE = max(60.0, float(os.getenv("GLOBAL_PIT_HOT_ATTENTION_SCORE", "70")))
GLOBAL_PIT_CRITICAL_ATTENTION_SCORE = max(GLOBAL_PIT_HOT_ATTENTION_SCORE, float(os.getenv("GLOBAL_PIT_CRITICAL_ATTENTION_SCORE", "88")))
GLOBAL_PIT_ROTATION_MIN_ADVANTAGE_PCT = max(0.0, float(os.getenv("GLOBAL_PIT_ROTATION_MIN_ADVANTAGE_PCT", "2.5")))
GLOBAL_PIT_MAX_PARALLEL_LANES = max(1, min(12, int(os.getenv("GLOBAL_PIT_MAX_PARALLEL_LANES", "9"))))
CORE_SIGNAL_SUPPORT_THRESHOLD = max(1.0, min(100.0, float(os.getenv("CORE_SIGNAL_SUPPORT_THRESHOLD", "60"))))
MIN_CORE_SIGNALS_AGREE = max(1, min(5, int(os.getenv("MIN_CORE_SIGNALS_AGREE", "3"))))
MIN_CONFIDENCE_TO_TRADE = max(1.0, min(100.0, float(os.getenv("MIN_CONFIDENCE_TO_TRADE", "70"))))
MIN_REWARD_RISK_RATIO = max(0.1, float(os.getenv("MIN_REWARD_RISK_RATIO", "1.5")))
MAX_SECONDARY_SCORE_ADJUSTMENT = max(0.0, min(10.0, float(os.getenv("MAX_SECONDARY_SCORE_ADJUSTMENT", "5"))))
PENNY_STOCK_MAX_PRICE = float(os.getenv("PENNY_STOCK_MAX_PRICE", "5.00"))
PENNY_STOCK_ENABLED = os.getenv("PENNY_STOCK_ENABLED", "true").lower() == "true"
PENNY_STOCK_MIN_PRICE = float(os.getenv("PENNY_STOCK_MIN_PRICE", "0.50"))
PENNY_STOCK_MIN_DAILY_VOLUME = float(os.getenv("PENNY_STOCK_MIN_DAILY_VOLUME", "500000"))
PENNY_STOCK_MIN_AVG_DOLLAR_VOLUME = float(os.getenv("PENNY_STOCK_MIN_AVG_DOLLAR_VOLUME", "2500000"))
PENNY_STOCK_MAX_TRADE_VALUE_PCT = float(os.getenv("PENNY_STOCK_MAX_TRADE_VALUE_PCT", "0.01"))
PENNY_STOCK_MAX_OPEN_POSITIONS = max(0, int(os.getenv("PENNY_STOCK_MAX_OPEN_POSITIONS", "3")))
PENNY_STOCK_MAX_PORTFOLIO_PCT = float(os.getenv("PENNY_STOCK_MAX_PORTFOLIO_PCT", "0.02"))
PENNY_STOCK_MIN_SCORE = float(os.getenv("PENNY_STOCK_MIN_SCORE", "75"))
PENNY_STOCK_MIN_CONFIDENCE = float(os.getenv("PENNY_STOCK_MIN_CONFIDENCE", "0.70"))
OTC_STOCKS_ENABLED = os.getenv("OTC_STOCKS_ENABLED", "false").lower() == "true"

# V27 controlled paper-entry mode. This only relaxes the initial opportunity
# classification; hard cash, concentration, correlation and negative-EV vetoes
# remain enforced by the allocator and portfolio supercomputer.
PAPER_TRADING_BREATHING_ROOM = os.getenv("PAPER_TRADING_BREATHING_ROOM", "true").lower() == "true"
