"""
STEP 5 — evaluator.py
The tool-calling LOOP. This is the concept most people get wrong.

An API call with tools is NOT one request. It is a conversation:

    1. YOU  → OpenAI : here is the job + the tools available
    2. OpenAI → YOU  : "please run calculate_skill_overlap(['sql','python'])"
                       (the model does NOT run it — it asks you to)
    3. YOU           : run the real Python function locally
    4. YOU  → OpenAI : "here is the result: {overlap: 78%...}"
    5. OpenAI → YOU  : maybe another tool call → repeat 3-4
    6. OpenAI → YOU  : calls submit_evaluation(...) = the final answer

Steps 3-5 repeat, so you need a loop, not a single call.
"""

import json

from openai import OpenAI

from . import config
from .tools import TOOL_SCHEMAS, TOOL_IMPLEMENTATIONS

_client = None


def get_client():
    """
    Lazy initialization: build the client the FIRST time it is needed,
    then reuse it.

    WHY: if you write `client = OpenAI(...)` at the top of the file, the
    client is built the moment anything imports this module — so
    `--top` (which only reads the database) would crash without an
    API key. Import should never have side effects like this.
    """
    global _client
    if _client is None:
        config.validate()
        _client = OpenAI(api_key=config.OPENAI_API_KEY)
    return _client


SYSTEM_PROMPT = """You are a strict, honest career advisor evaluating whether a
job posting fits a specific candidate.

Your process:
1. Read the posting and extract the skills it actually requires.
2. Call calculate_skill_overlap with those skills.
3. If the posting demands a minimum number of years for a technology,
   call get_experience_years for it. Never estimate years yourself.
4. Call get_candidate_profile if you need more context.
5. Call submit_evaluation exactly once with your final verdict.

Scoring rules — be honest, an inflated score wastes the candidate's time:
- Penalize heavily if the role requires a technology the candidate has never used.
- Penalize seniority mismatch (a Senior/Lead role for a mid-level candidate).
- The candidate's English is B2: flag roles demanding native/C1 fluency as a concern.
- The candidate is in Colombia: flag roles requiring on-site work elsewhere.
- Below 50 means "skip". Only 80+ deserves "apply_now"."""


def build_job_text(job: dict) -> str:
    """Turn a database row into readable text for the model."""
    return f"""JOB POSTING
Title: {job.get('title')}
Company: {job.get('company')}
Location: {job.get('location')}
Seniority: {job.get('seniority_level')}
Employment type: {job.get('employment_type')}
Remote: {job.get('is_remote')}
Salary: {job.get('salary')}

DESCRIPTION:
{job.get('description')}"""


def evaluate_job(job: dict, verbose: bool = True) -> dict:
    """Run the full tool-calling loop for ONE job. Returns the final verdict."""

    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": build_job_text(job)},
    ]

    for turn in range(config.MAX_TOOL_TURNS):
        response = get_client().chat.completions.create(
            model=config.MODEL,
            messages=messages,
            tools=TOOL_SCHEMAS,
            tool_choice="auto",   # let the model decide which tool to use
            temperature=0,        # 0 = repeatable scores, not creative ones
        )

        message = response.choices[0].message

        # No tool call = the model just talked. Nudge it back on track.
        if not message.tool_calls:
            messages.append({"role": "assistant", "content": message.content or ""})
            messages.append({
                "role": "user",
                "content": "Please call submit_evaluation now with your final verdict.",
            })
            continue

        # IMPORTANT: append the assistant's tool-call message BEFORE the results,
        # otherwise the API rejects the next request.
        messages.append(message)

        for call in message.tool_calls:
            name = call.function.name
            args = json.loads(call.function.arguments or "{}")

            # The final tool ends the loop — we return its arguments as the answer.
            if name == "submit_evaluation":
                if verbose:
                    print(f"    ✓ verdict after {turn + 1} turn(s)")
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
                print(f"    → tool: {name}({args})")

            # Feed the result back. role="tool" + the matching id is required.
            messages.append({
                "role": "tool",
                "tool_call_id": call.id,
                "content": json.dumps(result, ensure_ascii=False),
            })

    return {"error": f"No verdict after {config.MAX_TOOL_TURNS} turns"}
