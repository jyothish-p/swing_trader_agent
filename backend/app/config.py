"""Application configuration."""
import os
from pathlib import Path

# Base paths
BASE_DIR = Path(__file__).resolve().parent.parent
# Use environment variable for data dir, or a safe default without spaces
# (SQLite can fail on paths with spaces on some systems)
_default_data = str(BASE_DIR / "data")
if " " in _default_data:
    # Fallback: use user's home directory instead of path with spaces
    _default_data = str(Path.home() / ".swing_trader" / "data")
DATA_DIR = Path(os.getenv("SWING_TRADER_DATA_DIR", _default_data))
DATA_DIR.mkdir(parents=True, exist_ok=True)

# Database
DATABASE_URL = os.getenv(
    "DATABASE_URL",
    f"sqlite:///{DATA_DIR / 'swing_trader.db'}"
)

# Screening parameters
MIN_MARKET_CAP_CR = 5_000  # ₹5,000 Crore minimum (Large + Mid cap)
MIN_TURNOVER_CR = 10  # ₹10 Cr average daily turnover
TOP_N_STOCKS = 20  # Number of top stocks to select
ACTIONABLE_TOP_N = 20  # Number of actionable stocks to show
LOOKBACK_DAYS = 365  # 1 year of historical data
LOOKBACK_1M_DAYS = 22  # ~1 month trading days
NEW_HIGH_TOLERANCE = 0.01  # 1% tolerance for new high detection

# Technical analysis timeframes
EMA_PERIODS = [20, 50, 100, 200]
RSI_PERIOD = 14
BOLLINGER_PERIOD = 20
BOLLINGER_STD = 2
MACD_FAST = 12
MACD_SLOW = 26
MACD_SIGNAL = 9

# yfinance NSE suffix
NSE_SUFFIX = ".NS"

# Kite Connect (set via environment or settings page)
KITE_API_KEY = os.getenv("KITE_API_KEY", "")
KITE_API_SECRET = os.getenv("KITE_API_SECRET", "")
KITE_ACCESS_TOKEN = os.getenv("KITE_ACCESS_TOKEN", "")


def _env_flag(name: str, default: str = "0") -> bool:
    return os.getenv(name, default).strip().lower() in {"1", "true", "yes", "on"}


# Optional OpenAI verdict generation
LLM_VERDICTS_PROVIDER = os.getenv("LLM_VERDICTS_PROVIDER", "auto").strip().lower()
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
GEMINI_VERDICTS_ENABLED = _env_flag("GEMINI_VERDICTS_ENABLED", "0")
GEMINI_VERDICTS_MODEL = os.getenv("GEMINI_VERDICTS_MODEL", "gemini-2.5-flash")
GEMINI_VERDICTS_MAX_OUTPUT_TOKENS = int(os.getenv("GEMINI_VERDICTS_MAX_OUTPUT_TOKENS", "90"))
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
OPENAI_VERDICTS_ENABLED = _env_flag("OPENAI_VERDICTS_ENABLED", "0")
OPENAI_VERDICTS_MODEL = os.getenv("OPENAI_VERDICTS_MODEL", "gpt-5.4-mini")
OPENAI_VERDICTS_TIMEOUT_SEC = float(os.getenv("OPENAI_VERDICTS_TIMEOUT_SEC", "20"))
OPENAI_VERDICTS_MAX_OUTPUT_TOKENS = int(os.getenv("OPENAI_VERDICTS_MAX_OUTPUT_TOKENS", "90"))

# Server
HOST = "0.0.0.0"
PORT = 8000
CORS_ORIGINS = ["http://localhost:5174", "http://localhost:5173", "http://localhost:3000", "http://127.0.0.1:5174"]

# Cache TTL (seconds)
CACHE_TTL_UNIVERSE = 86400  # 24 hours - F&O list doesn't change intraday
CACHE_TTL_HISTORICAL = 3600  # 1 hour - historical data refreshed hourly max
CACHE_TTL_QUOTES = 60  # 1 minute - real-time quotes
