"""Settings loaded from .env (see .env.example)."""
from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
CACHE_DIR = DATA_DIR / "cache"
JOBS_DIR = DATA_DIR / "jobs"
CONTACTS_DIR = DATA_DIR / "contacts"
WEB_DIR = ROOT / "web"

load_dotenv(ROOT / ".env", override=True)  # .env wins over stale machine-wide vars

for d in (CACHE_DIR, JOBS_DIR, CONTACTS_DIR):
    d.mkdir(parents=True, exist_ok=True)

# LLM — provider is "openai" (default) or "anthropic"
LLM_PROVIDER = (os.getenv("LLM_PROVIDER") or "openai").lower()
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
OPENAI_BASE_URL = os.getenv("OPENAI_BASE_URL", "")
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-5.6-terra")
OPENAI_MODEL_FAST = os.getenv("OPENAI_MODEL_FAST", "gpt-5.6-luna")
OPENAI_REASONING_EFFORT = os.getenv("OPENAI_REASONING_EFFORT", "low")
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
LLM_MODEL = os.getenv("LLM_MODEL", "claude-sonnet-5")
LLM_MODEL_FAST = os.getenv("LLM_MODEL_FAST", "claude-haiku-4-5-20251001")

# Optional data providers
APIFY_TOKEN = os.getenv("APIFY_TOKEN", "")
APIFY_POST_SEARCH_ACTOR = os.getenv("APIFY_POST_SEARCH_ACTOR", "")
APIFY_POST_ENGAGERS_ACTOR = os.getenv("APIFY_POST_ENGAGERS_ACTOR", "")
APOLLO_API_KEY = os.getenv("APOLLO_API_KEY", "")
CLAY_WEBHOOK_URL = os.getenv("CLAY_WEBHOOK_URL", "")
CLAY_CALLBACK_SECRET = os.getenv("CLAY_CALLBACK_SECRET", "")

LINKEDIN_FREE_LANE = os.getenv("LINKEDIN_FREE_LANE", "1") not in ("0", "false", "False", "")

# Pipeline knobs
LOOKBACK_DAYS = int(os.getenv("LOOKBACK_DAYS", "45"))
MAX_NEWS_ITEMS_PER_QUERY = int(os.getenv("MAX_NEWS_ITEMS_PER_QUERY", "40"))
MAX_JOBS_PER_QUERY = int(os.getenv("MAX_JOBS_PER_QUERY", "25"))
TOP_ACCOUNTS_TO_ENRICH = int(os.getenv("TOP_ACCOUNTS_TO_ENRICH", "25"))
MAX_ACCOUNTS_STORED = int(os.getenv("MAX_ACCOUNTS_STORED", "300"))
CONTACTS_PER_ACCOUNT = int(os.getenv("CONTACTS_PER_ACCOUNT", "3"))

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
)


def llm_available() -> bool:
    return bool(OPENAI_API_KEY) if LLM_PROVIDER == "openai" else bool(ANTHROPIC_API_KEY)
