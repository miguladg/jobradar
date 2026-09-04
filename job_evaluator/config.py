"""
STEP 1 — config.py
Every setting lives here. No other file reads os.environ directly.

WHY: when you need to change the model or the database, you edit ONE file.
This is called "single source of truth".
"""

import os

# Load a .env file automatically if python-dotenv is installed.
# This means you set your keys ONCE in a file instead of exporting
# them in every new terminal.
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass  # optional dependency — env vars still work without it

# ── OpenAI ────────────────────────────────────────────────────
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY")
MODEL = os.environ.get("OPENAI_MODEL", "gpt-4o-mini")

# Safety valve: how many times the model is allowed to call tools
# before we stop the loop. Prevents an infinite loop (and an infinite bill).
MAX_TOOL_TURNS = 6

# ── PostgreSQL (same variables your api_call.py scraper uses) ──
DB_CONFIG = {
    "host": os.environ.get("DB_HOST", "localhost"),
    "port": os.environ.get("DB_PORT", "5432"),
    "dbname": os.environ.get("DB_NAME", "linkedin_jobs"),
    "user": os.environ.get("DB_USER", "postgres"),
    "password": os.environ.get("DB_PASSWORD", ""),
}


def validate():
    """Fail early with a clear message instead of a confusing error later."""
    if not OPENAI_API_KEY:
        raise RuntimeError(
            "OPENAI_API_KEY is not set.\n"
            'Run:  export OPENAI_API_KEY="sk-..."'
        )
