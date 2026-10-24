"""
evaluation/evaluator.py — the OpenAI tool-calling loop.

A tool-calling request is a CONVERSATION, not a single call:

  1. You  → OpenAI : job posting + available tools
  2. OpenAI → You  : "run calculate_skill_overlap(['sql','python'])"
  3. You           : execute the real Python function locally
  4. You  → OpenAI : "result: 60% overlap, missing airflow"
  5. repeat 2-4
  6. OpenAI → You  : calls submit_evaluation(...) = final answer

OpenAI never runs your code — it only asks you to run it.
"""

import json
import logging

from openai import OpenAI

from .. import config
from ..cv_loader import load_cv
from .tools import TOOL_SCHEMAS, TOOL_IMPLEMENTATIONS, total_experience_years
from ..trace import step, detail, warn

log = logging.getLogger(__name__)

_client = None


def get_client():
    """Lazy: built on first use, so importing this module never requires
    an API key (the `top` and `stats` commands don't need one)."""
    global _client
    if _client is None:
        step("creating the OpenAI client", level=2)
        config.validate_openai()
        _client = OpenAI(api_key=config.OPENAI_API_KEY)
    return _client


def build_system_prompt() -> str:
    """
    Built from the CURRENT CV, so a new CV file changes the rules too
    (location, languages, seniority) — nothing about you is hardcoded here.
    """
    cv = load_cv()
    languages = ", ".join(f"{k} ({v})" for k, v in cv.get("languages", {}).items()) or "not stated"
    open_to = ", ".join(cv.get("open_to", [])) or "not stated"
    roles = ", ".join(cv.get("target_roles", [])) or "not stated"

    return f"""You are a strict, honest career advisor evaluating whether a
job posting fits a specific candidate.

Candidate snapshot:
- Target roles: {roles}
- Total professional experience: {total_experience_years()} years
- Based in: {cv.get("location", "not stated")}; open to: {open_to}
- Languages: {languages}

Your process:
1. Read the posting and extract the skills it genuinely requires.
2. Call calculate_skill_overlap with those skills.
3. If the posting demands a minimum number of years for a technology,
   call get_experience_years. Never estimate years yourself.
4. Call get_candidate_profile if you need more context.
5. Call submit_evaluation exactly once with your final verdict.

Scoring rules — an inflated score wastes the candidate's time:
- Penalize heavily when a required core technology is entirely absent
  from the candidate's history.
- Penalize seniority mismatch: compare the role's level and required years
  with the candidate's total experience above.
- Flag language requirements above the candidate's stated levels as a concern.
- Flag on-site roles outside the candidate's location as a concern.
- Below 50 = "skip". Only 80+ deserves "apply_now"."""


def build_job_text(job: dict) -> str:
    """Format a database row for the model. More context than before:
    applicant count and posting date both affect whether it's worth applying."""
    parts = [
        f"Title: {job.get('title')}",
        f"Company: {job.get('company')}",
        f"Location: {job.get('location')}",
        f"Remote: {job.get('is_remote')}",
        f"Seniority: {job.get('seniority_level')}",
        f"Employment type: {job.get('employment_type')}",
        f"Salary: {job.get('salary') or 'not stated'}",
        f"Applicants so far: {job.get('number_applicants') or 'unknown'}",
        f"Posted: {job.get('formatted_posted_date')}",
        "",
        "DESCRIPTION:",
        (job.get("description") or "")[:8000],   # cap tokens per request
    ]
    return "\n".join(parts)


def evaluate_job(job: dict, verbose: bool = True) -> dict:
    """Run the full tool-calling loop for ONE job."""

    messages = [
        {"role": "system", "content": build_system_prompt()},
        {"role": "user", "content": build_job_text(job)},
    ]

    step(f"sending job to {config.OPENAI_MODEL} with "
         f"{len(TOOL_SCHEMAS)} tools available", level=1)

    for turn in range(config.MAX_TOOL_TURNS):
        try:
            response = get_client().chat.completions.create(
                model=config.OPENAI_MODEL,
                messages=messages,
                tools=TOOL_SCHEMAS,
                tool_choice="auto",
                temperature=0,      # repeatable scores, not creative ones
            )
        except Exception as exc:
            warn(f"OpenAI request failed: {exc}", level=2)
            log.error("OpenAI request failed: %s", exc)
            return {"error": f"API error: {exc}"}

        message = response.choices[0].message

        # No tool call = the model just talked. Nudge it back.
        if not message.tool_calls:
            detail("model replied without calling a tool, nudging it", level=2)
            messages.append({"role": "assistant", "content": message.content or ""})
            messages.append({"role": "user",
                             "content": "Call submit_evaluation now with your final verdict."})
            continue

        # The assistant's tool-call message MUST be appended before the
        # results, or the next request is rejected.
        messages.append(message)

        for call in message.tool_calls:
            name = call.function.name
            try:
                args = json.loads(call.function.arguments or "{}")
            except json.JSONDecodeError:
                args = {}

            if name == "submit_evaluation":
                if verbose:
                    detail(f"model submitted its verdict "
                           f"(after {turn + 1} exchanges)", level=2)
                return args

            func = TOOL_IMPLEMENTATIONS.get(name)
            if func is None:
                result = {"error": f"Unknown tool: {name}"}
            else:
                try:
                    result = func(**args)
                except Exception as exc:
                    result = {"error": str(exc)}

            if verbose:
                detail(f"model asked us to run {name}(); "
                       f"executing it in Python", level=2)

            messages.append({
                "role": "tool",
                "tool_call_id": call.id,
                "content": json.dumps(result, ensure_ascii=False, default=str),
            })

    return {"error": f"No verdict after {config.MAX_TOOL_TURNS} turns"}
