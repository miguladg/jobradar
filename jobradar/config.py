"""
config.py — every setting in one place.

Change behaviour HERE, never by editing code deeper in the package.
"""

import os

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass


# ═══════════════════════════════════════════════════════════════
# SCRAPER SETTINGS
# ═══════════════════════════════════════════════════════════════

# ── How far back to look ───────────────────────────────────────
# Scrape and evaluate jobs posted in the last 24 hours only.
# Change ONE number here and both the LinkedIn URL filter and the
# local date check follow it automatically.
DAYS_BACK = int(os.environ.get("DAYS_BACK", "1"))

# LinkedIn expects seconds, not days: 1 day = 86,400 seconds.
# Computed, not hardcoded — so DAYS_BACK stays the single source of truth.
SECONDS_BACK = DAYS_BACK * 24 * 60 * 60

LOCATION = os.environ.get("SEARCH_LOCATION", "Colombia")

# ── Which LinkedIn URL to use ──────────────────────────────────
# PRIMARY: the exact search URL from your working scraper. Proven to
# return results — this is the default and the fallback.
SEARCH_URL = (
    "https://www.linkedin.com/jobs/search/"
    "?f_TPR=r{seconds}&f_WT={work_types}&location={location}"
    "&origin=JOB_SEARCH_PAGE_JOB_FILTER&keywords={keywords}"
)

# OPTIONAL: LinkedIn's guest endpoint. It supports a `start` offset, so
# it can page past the first ~25 results — but it is undocumented and
# may break. Off by default; turn it on only after confirming the
# primary URL works for you.
GUEST_API_URL = "https://www.linkedin.com/jobs-guest/jobs/api/seeMoreJobPostings/search"
USE_GUEST_API = os.environ.get("USE_GUEST_API", "false").lower() == "true"

SEARCH_TERMS = [
    "Data Analyst",
    "Data Engineer",
    "Data Scientist",
    "Backend Developer",
    "Systems Integration Engineer",
    "Automation Engineer",
]

# Work type: 1=On-site, 2=Remote, 3=Hybrid.
# "1,2" matches your working scraper. Add ",3" to include hybrid roles.
WORK_TYPES = os.environ.get("WORK_TYPES", "1,2")

# ── Pagination ─────────────────────────────────────────────────
# LinkedIn's guest API returns 10 cards per request. The old script
# read only the first page (~25 jobs total); this walks through pages.
RESULTS_PER_PAGE = 10
MAX_PAGES_PER_TERM = int(os.environ.get("MAX_PAGES_PER_TERM", "8"))

# ── Politeness / anti-blocking ─────────────────────────────────
# Random delays look human. Fixed delays look like a bot.
DELAY_BETWEEN_JOBS = (2.0, 4.5)      # seconds, random in this range
DELAY_BETWEEN_PAGES = (3.0, 6.0)
DELAY_BETWEEN_TERMS = (5.0, 9.0)

REQUEST_TIMEOUT = 15
MAX_RETRIES = 3
BACKOFF_FACTOR = 2.0                 # 2s, 4s, 8s between retries

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)


# ═══════════════════════════════════════════════════════════════
# OPENAI SETTINGS
# ═══════════════════════════════════════════════════════════════

OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY")
# Folder for your CV file (PDF, DOCX, TXT or MD). The newest file is used;
# if the folder is empty, cv_profile.py is used instead.
CV_DIR = os.environ.get("CV_DIR", "cv")
OPENAI_MODEL = os.environ.get("OPENAI_MODEL", "gpt-4o-mini")
MAX_TOOL_TURNS = 6


# ═══════════════════════════════════════════════════════════════
# DATABASE SETTINGS
# ═══════════════════════════════════════════════════════════════

DB_CONFIG = {
    "host": os.environ.get("DB_HOST", "localhost"),
    "port": os.environ.get("DB_PORT", "5432"),
    "dbname": os.environ.get("DB_NAME", "linkedin_jobs"),
    "user": os.environ.get("DB_USER", "postgres"),
    "password": os.environ.get("DB_PASSWORD", ""),
}


# ═══════════════════════════════════════════════════════════════
# LOGGING
# ═══════════════════════════════════════════════════════════════

LOG_LEVEL = os.environ.get("LOG_LEVEL", "INFO")
LOG_FILE = os.environ.get("LOG_FILE", "jobradar.log")


def validate_openai():
    """Called only by commands that actually need OpenAI."""
    if not OPENAI_API_KEY or OPENAI_API_KEY == "CHANGE_ME":
        raise RuntimeError(
            "OPENAI_API_KEY is not set.\n"
            "Edit your .env file and add your real key."
        )
