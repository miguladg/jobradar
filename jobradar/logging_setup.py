"""
logging_setup.py — one place to configure logging.

WHY replace print() with logging:
  - You get timestamps and severity levels for free.
  - Output goes to a FILE as well as the screen, so a scrape that runs
    for an hour leaves a record you can read afterwards.
  - You can silence noise (`--quiet`) without deleting lines of code.
"""

import logging
import sys

from . import config


def setup(level: str = None, log_file: str = None):
    level = (level or config.LOG_LEVEL).upper()
    log_file = log_file or config.LOG_FILE

    root = logging.getLogger()
    root.setLevel(logging.DEBUG)
    root.handlers.clear()

    # Console: clean and readable while you watch it run.
    console = logging.StreamHandler(sys.stdout)
    console.setLevel(getattr(logging, level, logging.INFO))
    console.setFormatter(logging.Formatter("%(message)s"))
    root.addHandler(console)

    # File: full detail with timestamps, for reading afterwards.
    if log_file:
        file_handler = logging.FileHandler(log_file, encoding="utf-8")
        file_handler.setLevel(logging.DEBUG)
        file_handler.setFormatter(logging.Formatter(
            "%(asctime)s [%(levelname)-7s] %(name)s: %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        ))
        root.addHandler(file_handler)

    # These libraries log a lot at DEBUG level — quiet them down.
    logging.getLogger("urllib3").setLevel(logging.WARNING)
    logging.getLogger("openai").setLevel(logging.WARNING)
    logging.getLogger("httpx").setLevel(logging.WARNING)
