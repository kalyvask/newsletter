"""Configuration and constants for the AI Deployment Research Monitor.

Personal content (reading list, target companies, voice/style notes) is loaded
from local files that are gitignored:

- ``reading-list.json`` (falls back to ``reading-list.example.json``)
- ``voice.md``          (falls back to ``voice.example.md``)

This keeps the orchestration code public and reusable while letting each user
plug in their own taste without committing it to the repo. See the README
section "Configure for your own newsletter" for setup.
"""

import json
import os
from pathlib import Path
from dotenv import load_dotenv

# Load environment variables (override=True ensures .env takes precedence)
load_dotenv(override=True)

# Base paths — use /tmp on Vercel (read-only filesystem)
VERCEL = os.getenv("VERCEL", "")
BASE_DIR = Path(__file__).parent.parent

if VERCEL:
    DATA_DIR = Path("/tmp/data")
    OUTPUT_DIR = Path("/tmp/output")
    REPORTS_DIR = OUTPUT_DIR / "reports"
else:
    DATA_DIR = BASE_DIR / "data"
    OUTPUT_DIR = BASE_DIR / "output"
    REPORTS_DIR = OUTPUT_DIR / "reports"

# Ensure directories exist
DATA_DIR.mkdir(exist_ok=True)
OUTPUT_DIR.mkdir(exist_ok=True)
REPORTS_DIR.mkdir(exist_ok=True)

# Database
DATABASE_PATH = DATA_DIR / "research.db"

# API Keys
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY")
REDDIT_CLIENT_ID = os.getenv("REDDIT_CLIENT_ID")
REDDIT_CLIENT_SECRET = os.getenv("REDDIT_CLIENT_SECRET")
REDDIT_USER_AGENT = os.getenv("REDDIT_USER_AGENT", "AIDeploymentMonitor/1.0")

# Email / SMTP settings
SMTP_HOST = os.getenv("SMTP_HOST", "smtp.gmail.com")
SMTP_PORT = os.getenv("SMTP_PORT", "587")
SMTP_USER = os.getenv("SMTP_USER", "")
SMTP_PASSWORD = os.getenv("SMTP_PASSWORD", "")
REPORT_EMAIL_TO = os.getenv("REPORT_EMAIL_TO", "")
REPORT_EMAIL_FROM = os.getenv("REPORT_EMAIL_FROM", "")

# Claude model
CLAUDE_MODEL = os.getenv("CLAUDE_MODEL", "claude-sonnet-4-20250514")

# Scraping settings
DEFAULT_SCRAPE_INTERVAL_HOURS = 6
REQUEST_TIMEOUT = 10 if VERCEL else 30
REQUEST_DELAY = 0.1 if VERCEL else 1.0  # Faster on Vercel, polite locally

# Relevance threshold
RELEVANCE_THRESHOLD = 0.6

# Hacker News settings
HN_API_BASE = "https://hacker-news.firebaseio.com/v0"
HN_ITEMS_PER_FETCH = 100  # Number of stories to check per run

# Claude API settings
MAX_TOKENS_SUMMARY = 500
MAX_TOKENS_ANALYSIS = 2000

# Report settings
WEEKLY_REPORT_DAY = "monday"  # Day to generate weekly reports
DAILY_REPORT_TIME = "09:00"  # Time to generate daily reports

# Logging
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")

# =============================================================================
# READING LIST LOADER
# =============================================================================
# Personal sources, keywords, and target companies live in `reading-list.json`
# (gitignored). If that file isn't present we fall back to the example, which
# ships with a small generic schema so the project still runs out of the box.

def _load_reading_list() -> dict:
    """Load reading-list.json, falling back to reading-list.example.json."""
    candidates = [
        Path(os.getenv("READING_LIST_PATH", "")) if os.getenv("READING_LIST_PATH") else None,
        BASE_DIR / "reading-list.json",
        BASE_DIR / "reading-list.example.json",
    ]
    for path in candidates:
        if path and path.exists():
            try:
                with open(path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                # Drop comment fields used for documentation in the JSON itself
                return {k: v for k, v in data.items() if not k.startswith("_")}
            except (json.JSONDecodeError, OSError):
                continue
    return {}


_READING_LIST = _load_reading_list()


def _load_voice_prompt() -> str:
    """Load the user's voice/style notes for system prompts.

    Falls back to the example file. Returned text is appended to system prompts
    in the report generator and Streamlit UI.
    """
    candidates = [
        Path(os.getenv("VOICE_PROMPT_PATH", "")) if os.getenv("VOICE_PROMPT_PATH") else None,
        BASE_DIR / "voice.md",
        BASE_DIR / "voice.example.md",
    ]
    for path in candidates:
        if path and path.exists():
            try:
                return path.read_text(encoding="utf-8").strip()
            except OSError:
                continue
    return ""


VOICE_PROMPT = _load_voice_prompt()

# =============================================================================
# CONTENT LISTS (loaded from reading-list.json)
# =============================================================================

PRIMARY_KEYWORDS = _READING_LIST.get("primary_keywords", [])
SECONDARY_KEYWORDS = _READING_LIST.get("secondary_keywords", [])
EXCLUSION_KEYWORDS = _READING_LIST.get("exclusion_keywords", [])
TARGET_COMPANIES = _READING_LIST.get("target_companies", [])
OPPORTUNITY_KEYWORDS = _READING_LIST.get("opportunity_keywords", [])
INDUSTRY_VERTICALS = _READING_LIST.get("industry_verticals", {})
REDDIT_SUBREDDITS = _READING_LIST.get("reddit_subreddits", [])
RSS_FEEDS = _READING_LIST.get("rss_feeds", {})

# =============================================================================
# TREND CATEGORIES FOR UI
# =============================================================================
TREND_CATEGORIES = _READING_LIST.get("trend_categories", {})

