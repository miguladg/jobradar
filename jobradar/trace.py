"""
trace.py — prints what the code is doing, as it does it.

WHY a helper instead of bare print() everywhere:
  - One consistent look, so the output is scannable.
  - Indentation shows nesting: which action happens inside which.
  - One switch (`enabled`) silences all of it for a quiet run.

NO STEP NUMBERS. A label like "saving to database" stays correct
forever; "step 7" becomes a lie the moment you insert something above it.
"""

import sys

enabled = True


def step(message: str, level: int = 0):
    """An action the code is about to take, or has just taken."""
    if enabled:
        print(f"{'   ' * level}> {message}", flush=True)


def detail(message: str, level: int = 1):
    """Supporting information under a step."""
    if enabled:
        print(f"{'   ' * level}  {message}", flush=True)


def warn(message: str, level: int = 1):
    """Something went wrong but the run continues."""
    if enabled:
        print(f"{'   ' * level}! {message}", flush=True, file=sys.stderr)


def section(title: str):
    """A major phase of the run."""
    if enabled:
        print(f"\n{'=' * 62}\n  {title}\n{'=' * 62}", flush=True)
