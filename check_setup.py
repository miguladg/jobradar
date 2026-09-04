"""
check_setup.py — run this BEFORE your first real API call.

Verifies that your .env is loaded, your secrets are set, your database
is reachable, and that .env is properly ignored by Git.

SECURITY NOTE: this script never prints a secret value. It only shows
whether one is set, plus a masked preview (sk-pr...4f2a). Get into the
habit of masking — a real key pasted into a screenshot, a log file, or
a chat is a leaked key.

Usage:
    python check_setup.py
"""

import os
import subprocess
import sys
from pathlib import Path

try:
    from dotenv import load_dotenv
    load_dotenv()
    DOTENV = True
except ImportError:
    DOTENV = False

OK, FAIL, WARN = "✅", "❌", "⚠️ "
problems = []


def mask(value: str) -> str:
    """
    Show only the first 5 and last 4 characters — and only if the value
    is long enough that this reveals little (API keys are 40+ chars).

    Short values like passwords are hidden completely: revealing 9 of 13
    characters of a password is a real leak, not a preview.
    """
    if not value:
        return "(not set)"
    if len(value) < 24:
        return f"{'*' * 8} ({len(value)} chars)"
    return f"{value[:5]}...{value[-4:]}"


def check_dotenv_installed():
    print("1. python-dotenv")
    if DOTENV:
        print(f"   {OK} installed — .env loads automatically")
    else:
        print(f"   {WARN} not installed. Run: pip install python-dotenv")
        print("      (env vars still work, but you must export them manually)")


def check_env_file():
    print("\n2. .env file")
    path = Path(".env")
    if not path.exists():
        print(f"   {FAIL} not found. Run: cp .env.example .env")
        problems.append("missing .env")
        return

    print(f"   {OK} found")

    # Permissions: on Linux/Mac the file should not be world-readable.
    if os.name != "nt":
        mode = oct(path.stat().st_mode)[-3:]
        if mode != "600":
            print(f"   {WARN} permissions are {mode}, should be 600")
            print("      Fix with: chmod 600 .env")
        else:
            print(f"   {OK} permissions are 600 (owner-only)")


def check_variables():
    print("\n3. Environment variables")

    required = {
        "OPENAI_API_KEY": True,   # True = secret, mask it
        "DB_PASSWORD": True,
        "DB_HOST": False,
        "DB_PORT": False,
        "DB_NAME": False,
        "DB_USER": False,
        "OPENAI_MODEL": False,
    }

    for name, is_secret in required.items():
        value = os.environ.get(name)

        if not value:
            print(f"   {FAIL} {name:<18} not set")
            problems.append(f"{name} not set")
        elif value == "CHANGE_ME":
            print(f"   {FAIL} {name:<18} still says CHANGE_ME")
            problems.append(f"{name} not filled in")
        else:
            shown = mask(value) if is_secret else value
            print(f"   {OK} {name:<18} {shown}")

    key = os.environ.get("OPENAI_API_KEY", "")
    if key and key != "CHANGE_ME" and not key.startswith("sk-"):
        print(f"   {WARN} OPENAI_API_KEY does not start with 'sk-' — check it")


def check_gitignore():
    print("\n4. Git safety")

    gitignore = Path(".gitignore")
    if not gitignore.exists():
        print(f"   {FAIL} no .gitignore — your .env could be committed!")
        problems.append("no .gitignore")
        return

    content = gitignore.read_text()
    if ".env" in content:
        print(f"   {OK} .gitignore contains .env")
    else:
        print(f"   {FAIL} .gitignore does NOT list .env")
        problems.append(".env not in .gitignore")

    # Is .env already tracked by Git? This is the dangerous case.
    try:
        result = subprocess.run(
            ["git", "ls-files", "--error-unmatch", ".env"],
            capture_output=True, text=True, timeout=5,
        )
        if result.returncode == 0:
            print(f"   {FAIL} .env is ALREADY TRACKED by Git!")
            print("      Fix now:  git rm --cached .env && git commit -m 'remove .env'")
            print("      Then ROTATE your API key — it may already be exposed.")
            problems.append(".env is tracked by git")
        else:
            print(f"   {OK} .env is not tracked by Git")
    except (subprocess.TimeoutExpired, FileNotFoundError):
        print(f"   {WARN} Git not available — skipped tracking check")


def check_database():
    print("\n5. PostgreSQL connection")
    try:
        import psycopg2
    except ImportError:
        print(f"   {FAIL} psycopg2 not installed")
        problems.append("psycopg2 missing")
        return

    try:
        conn = psycopg2.connect(
            host=os.environ.get("DB_HOST", "localhost"),
            port=os.environ.get("DB_PORT", "5432"),
            dbname=os.environ.get("DB_NAME", "linkedin_jobs"),
            user=os.environ.get("DB_USER", "postgres"),
            password=os.environ.get("DB_PASSWORD", ""),
            connect_timeout=5,
        )
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM jobs;")
            count = cur.fetchone()[0]
        conn.close()
        print(f"   {OK} connected — {count} job(s) in the 'jobs' table")
    except Exception as exc:
        first_line = str(exc).strip().split("\n")[0]
        print(f"   {FAIL} {first_line}")
        problems.append("database unreachable")


def main():
    print("=" * 60)
    print("  SETUP CHECK")
    print("=" * 60 + "\n")

    check_dotenv_installed()
    check_env_file()
    check_variables()
    check_gitignore()
    check_database()

    print("\n" + "=" * 60)
    if problems:
        print(f"  {len(problems)} problem(s) to fix:")
        for p in problems:
            print(f"    - {p}")
        print("=" * 60)
        sys.exit(1)

    print("  All checks passed. You are ready to run:")
    print("    python -m job_evaluator.run --dry-run")
    print("=" * 60)


if __name__ == "__main__":
    main()
