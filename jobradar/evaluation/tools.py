"""
STEP 4 — tools.py  ← THE HEART OF THE FEATURE

A "tool" is a Python function you let the model call.

Each tool has TWO halves:
  1. A SCHEMA (JSON) — you send this to OpenAI so the model knows the
     function exists, what it does, and what arguments it needs.
  2. An IMPLEMENTATION (real Python) — YOUR code, running on YOUR machine.
     OpenAI never runs it; it only asks you to run it.

WHY use tools instead of just asking for JSON?
Language models are bad at exact matching and arithmetic. They are good
at reading and judging. So we split the work:
  - Python does the counting  (calculate_skill_overlap, get_experience_years)
  - The model does the judging (reads the posting, weighs the trade-offs)
That combination gives you scores you can actually trust.
"""

import re
from datetime import datetime

from ..cv_loader import load_cv


def _skills_match(a: str, b: str) -> bool:
    """
    True when one skill appears in the other as a WHOLE word:
    "python 3" ↔ "python" and "node" ↔ "node.js" match, but "sql" does not
    match "nosql" and "r" does not match "rest api" (plain substring
    matching got both of those wrong and inflated scores).
    """
    if a == b:
        return True
    short, long_ = sorted((a, b), key=len)
    return bool(re.search(rf"(?<![\w]){re.escape(short)}(?![\w])", long_))


# ══════════════════════════════════════════════════════════════
# PART A — THE IMPLEMENTATIONS (your real Python functions)
# ══════════════════════════════════════════════════════════════

def calculate_skill_overlap(required_skills: list) -> dict:
    """
    Compares skills the model extracted from the posting against the CV.
    Deterministic: same input always gives the same output.
    """
    cv_skills = set(s.lower() for s in load_cv()["skills"])
    required = [s.lower().strip() for s in required_skills]

    matched, missing = [], []
    for skill in required:
        hit = any(_skills_match(skill, cv) for cv in cv_skills)
        (matched if hit else missing).append(skill)

    total = len(required) or 1
    return {
        "matched_skills": matched,
        "missing_skills": missing,
        "overlap_percentage": round(len(matched) / total * 100, 1),
        "total_required": len(required),
    }


def _parse_month(value):
    """'2024-04' → datetime; 'present' / 'actual' → now; junk → None."""
    if str(value).strip().lower() in ("present", "current", "now", "actual", "actualidad"):
        return datetime.now()
    try:
        return datetime.strptime(str(value).strip()[:7], "%Y-%m")
    except ValueError:
        return None


def total_experience_years() -> float:
    """All roles added up — used in the system prompt to judge seniority."""
    months = 0
    for job in load_cv()["experience"]:
        start, end = _parse_month(job.get("start")), _parse_month(job.get("end"))
        if start and end:
            months += (end.year - start.year) * 12 + (end.month - start.month)
    return round(months / 12, 1)


def get_experience_years(technology: str) -> dict:
    """
    Sums the real months the candidate used a technology, across all roles.
    The model would guess this; Python calculates it from the date ranges.
    """
    tech = technology.lower().strip()
    total_months = 0
    roles = []

    for job in load_cv()["experience"]:
        if not any(_skills_match(tech, t) for t in job["technologies"]):
            continue

        start = _parse_month(job["start"])
        end = _parse_month(job["end"])
        if start is None or end is None:
            continue
        months = (end.year - start.year) * 12 + (end.month - start.month)
        total_months += months
        roles.append(f'{job["title"]} @ {job["company"]} ({months} months)')

    return {
        "technology": technology,
        "total_years": round(total_months / 12, 1),
        "roles_using_it": roles,
        "found": total_months > 0,
    }


def get_candidate_profile() -> dict:
    """Lets the model pull the full CV on demand instead of us always
    pasting it into the prompt — this keeps token usage lower."""
    return load_cv()


# ══════════════════════════════════════════════════════════════
# PART B — THE SCHEMAS (what you send to OpenAI)
# ══════════════════════════════════════════════════════════════
# The "description" fields are NOT comments — the model reads them to
# decide WHEN to call each tool. Write them like instructions.

TOOL_SCHEMAS = [
    {
        "type": "function",
        "function": {
            "name": "calculate_skill_overlap",
            "description": (
                "Compare the skills required by a job posting against the "
                "candidate's actual skills. Call this FIRST, after you have "
                "read the posting and extracted its required skills."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "required_skills": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Skills/technologies the posting requires, e.g. ['python','sql','airflow']",
                    }
                },
                "required": ["required_skills"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_experience_years",
            "description": (
                "Get the candidate's real years of hands-on experience with one "
                "technology. Call this when a posting demands a minimum number "
                "of years. Never estimate this yourself."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "technology": {
                        "type": "string",
                        "description": "A single technology name, e.g. 'python' or 'aws'",
                    }
                },
                "required": ["technology"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_candidate_profile",
            "description": (
                "Retrieve the candidate's full CV: roles, dates, achievements, "
                "education and languages. Call this if you need context beyond "
                "a raw skill list."
            ),
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "submit_evaluation",
            "description": (
                "Submit your FINAL verdict. Call this exactly once, last, after "
                "you have used the other tools to gather evidence."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "fit_score": {
                        "type": "integer",
                        "description": "0-100. Be strict. Below 50 means do not apply.",
                    },
                    "verdict": {
                        "type": "string",
                        "enum": ["strong_match", "good_match", "possible_match", "weak_match", "no_match"],
                    },
                    "matching_skills": {"type": "array", "items": {"type": "string"}},
                    "missing_requirements": {"type": "array", "items": {"type": "string"}},
                    "strengths": {
                        "type": "string",
                        "description": "2 sentences: the strongest reasons this is a fit.",
                    },
                    "concerns": {
                        "type": "string",
                        "description": "2 sentences: honest gaps or risks. Never leave empty.",
                    },
                    "recommended_action": {
                        "type": "string",
                        "enum": ["apply_now", "apply_with_tailored_cv", "stretch_application", "skip"],
                    },
                },
                "required": [
                    "fit_score", "verdict", "matching_skills",
                    "missing_requirements", "strengths", "concerns",
                    "recommended_action",
                ],
            },
        },
    },
]


# Maps the tool NAME (what the model sends back) to the real function.
# Without this dictionary you would need a long if/elif chain.
TOOL_IMPLEMENTATIONS = {
    "calculate_skill_overlap": calculate_skill_overlap,
    "get_experience_years": get_experience_years,
    "get_candidate_profile": get_candidate_profile,
}
