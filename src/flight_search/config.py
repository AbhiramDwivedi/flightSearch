"""Configuration: loads .env and validates required API keys."""

import os
import sys
from pathlib import Path
from dotenv import load_dotenv

# Load .env from project root (src/flight_search/config.py -> parents[2] = project root)
_ROOT = Path(__file__).resolve().parents[2]
load_dotenv(_ROOT / ".env")

# ── API keys ──────────────────────────────────────────────────────────────────

SERPAPI_KEY: str = os.getenv("SERPAPI_KEY", "")
OPENAI_API_KEY: str = os.getenv("OPENAI_API_KEY", "")

def validate_keys() -> None:
    """Raise a clear error if any required API key is missing."""
    missing = []
    if not SERPAPI_KEY or SERPAPI_KEY == "your_serpapi_key_here":
        missing.append("SERPAPI_KEY")
    if not OPENAI_API_KEY or OPENAI_API_KEY == "your_openai_api_key_here":
        missing.append("OPENAI_API_KEY")
    if missing:
        print(f"\n❌  Missing API key(s): {', '.join(missing)}")
        print(f"    Edit {_ROOT / '.env'} and add your keys.\n")
        sys.exit(1)

# ── Paths & limits ────────────────────────────────────────────────────────────

# Default query file (can be overridden via CLI arg)
DEFAULT_QUERY_FILE: Path = _ROOT / "query.txt"

# Usage tracking file — counts monthly SerpAPI calls
USAGE_FILE: Path = _ROOT / ".usage.json"

# Max search combinations before prompting user for confirmation
MAX_COMBINATIONS: int = 20

# Monthly SerpAPI free-tier limit
SERPAPI_MONTHLY_LIMIT: int = 250

# Seconds to wait between SerpAPI calls (stay under 50/hr limit)
SERPAPI_CALL_DELAY: float = 1.2

# OpenAI model
OPENAI_MODEL: str = "gpt-5.2"

# ── Cache settings ────────────────────────────────────────────────────────────

# Local SerpAPI response cache file
CACHE_FILE: Path = _ROOT / ".serp_cache.json"

# How long a cached response is considered fresh (hours)
SERPAPI_CACHE_TTL_HOURS: int = 12

# Set to True by --no-cache CLI flag to skip local cache for this run
NO_CACHE: bool = False

# Persisted GPT parse output — reused across runs so seeder and main run share params
PARSED_CACHE_FILE: Path = _ROOT / ".last_parse.json"
