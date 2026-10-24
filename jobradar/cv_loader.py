"""
cv_loader.py — turns the CV file in your cv/ folder into structured data.

Drop a PDF, Word (.docx) or text file into cv/. The NEWEST file wins, so
updating your CV is just saving a new file there.

WHY structured and not raw text: the evaluation tools *calculate* things
(skill overlap, years per technology) and need lists and dates, not a
paragraph. So OpenAI reads the file once and fills in the same shape as
cv_profile.py. The result is cached by file content, so the extraction
only runs again when the CV actually changes.

No file in cv/ → falls back to the hand-written cv_profile.py.
"""

import hashlib
import json
import logging
import re
import zipfile
from pathlib import Path

from . import config
from .trace import step, detail

log = logging.getLogger(__name__)

SUPPORTED = {".pdf", ".docx", ".txt", ".md"}

_cv = None          # loaded once per run
_source = None      # which file it came from (or "cv_profile.py")


# ── Finding and reading the file ───────────────────────────────

def find_latest_cv(folder=None):
    folder = Path(folder or config.CV_DIR)
    if not folder.is_dir():
        return None
    files = [p for p in folder.iterdir()
             if p.is_file() and p.suffix.lower() in SUPPORTED
             and not p.name.startswith((".", "~$"))]   # skip hidden/Word lock files
    return max(files, key=lambda p: p.stat().st_mtime, default=None)


def _read_pdf(path):
    try:
        from pypdf import PdfReader
    except ImportError:
        from PyPDF2 import PdfReader
    return "\n".join(page.extract_text() or "" for page in PdfReader(str(path)).pages)


def _read_docx(path):
    # A .docx is a zip; the text lives in word/document.xml. Reading it
    # directly avoids needing the python-docx package.
    with zipfile.ZipFile(path) as z:
        xml = z.read("word/document.xml").decode("utf-8")
    xml = re.sub(r"</w:p>", "\n", xml)          # paragraph ends → newlines
    xml = re.sub(r"<w:tab/>", "\t", xml)
    return re.sub(r"<[^>]+>", "", xml)


def read_text(path):
    path = Path(path)
    suffix = path.suffix.lower()
    if suffix == ".pdf":
        text = _read_pdf(path)
    elif suffix == ".docx":
        text = _read_docx(path)
    else:
        text = path.read_text(encoding="utf-8", errors="ignore")
    text = re.sub(r"[ \t]+", " ", text)
    return re.sub(r"\n\s*\n+", "\n\n", text).strip()


# ── Extraction with OpenAI ─────────────────────────────────────

# Same shape as cv_profile.CV. Languages come back as a list because
# JSON schemas can't describe "a dict with any keys" well; we convert after.
_PROFILE_SCHEMA = {
    "type": "object",
    "properties": {
        "name": {"type": "string"},
        "location": {"type": "string", "description": "City, Country"},
        "open_to": {"type": "array", "items": {"type": "string"},
                    "description": "Work arrangements the candidate wants, e.g. Remote, Hybrid (City). Empty if not stated."},
        "target_roles": {"type": "array", "items": {"type": "string"},
                         "description": "Job titles the candidate is aiming for, inferred from the summary and recent roles."},
        "languages": {"type": "array", "items": {
            "type": "object",
            "properties": {"language": {"type": "string"},
                           "level": {"type": "string", "description": "e.g. Native, C1, B2"}},
            "required": ["language", "level"]}},
        "education": {"type": "object", "properties": {
            "degree": {"type": "string"}, "school": {"type": "string"},
            "year": {"type": "integer"}}},
        "skills": {"type": "array", "items": {"type": "string"},
                   "description": "Every technical skill, tool and technology, lowercase, one per item, e.g. 'python', 'sql server', 'aws lambda'."},
        "experience": {"type": "array", "items": {
            "type": "object",
            "properties": {
                "title": {"type": "string"},
                "company": {"type": "string"},
                "start": {"type": "string", "description": "YYYY-MM"},
                "end": {"type": "string", "description": "YYYY-MM, or 'present' for the current job"},
                "technologies": {"type": "array", "items": {"type": "string"},
                                 "description": "Lowercase technologies used IN THIS ROLE, using the same names as in skills."},
                "highlights": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["title", "company", "start", "end", "technologies", "highlights"]}},
    },
    "required": ["name", "location", "open_to", "target_roles", "languages",
                 "education", "skills", "experience"],
}

_EXTRACT_PROMPT = """You convert a CV into structured data. Copy facts exactly
from the CV; never invent skills, dates or roles.

Be EXHAUSTIVE with skills and technologies: include every language, library,
framework, cloud service, database, tool, platform and methodology mentioned
ANYWHERE — the skills section, the summary, and inside each job's bullet
points (e.g. pandas, etl, aws lambda, notion, mercadolibre, qa testing).
A downstream tool scores job matches from this list, so a missed skill
unfairly lowers the candidate's score. Write them in lowercase.

For open_to, capture any stated work arrangement (remote, hybrid, on-site,
relocation). Keep language levels as written (e.g. "B2", "Professional").

Dates are YYYY-MM; if only a year is given use YYYY-01 for a start and
YYYY-12 for an end. Use 'present' for an ongoing role.
Call save_profile exactly once."""


def extract_profile(text):
    """CV text → structured dict, using a forced tool call so the model
    must answer in the exact shape above."""
    from openai import OpenAI     # only needed when a CV file is used
    config.validate_openai()
    client = OpenAI(api_key=config.OPENAI_API_KEY)

    response = client.chat.completions.create(
        model=config.CV_EXTRACT_MODEL,
        temperature=0,
        messages=[{"role": "system", "content": _EXTRACT_PROMPT},
                  {"role": "user", "content": text[:20000]}],
        tools=[{"type": "function", "function": {
            "name": "save_profile",
            "description": "Save the structured CV.",
            "parameters": _PROFILE_SCHEMA}}],
        tool_choice={"type": "function", "function": {"name": "save_profile"}},
    )
    raw = json.loads(response.choices[0].message.tool_calls[0].function.arguments)

    # Tidy up what the tools rely on.
    raw["languages"] = {l["language"]: l["level"] for l in raw.get("languages", [])}
    for job in raw.get("experience", []):
        job["technologies"] = [t.lower().strip() for t in job.get("technologies", [])]
    # A tool mentioned only inside a job description is still a skill.
    role_techs = [t for job in raw.get("experience", []) for t in job["technologies"]]
    raw["skills"] = sorted({s.lower().strip() for s in raw.get("skills", []) + role_techs
                            if s.strip()})
    return raw


# ── Public entry point ─────────────────────────────────────────

def load_cv(refresh=False):
    """
    The CV every other module should use. Cached in memory for the run,
    and on disk (cv/.cache/<hash>.json) across runs.
    """
    global _cv, _source
    if _cv is not None and not refresh:
        return _cv

    path = find_latest_cv()
    if path is None:
        from .cv_profile import CV
        log.info("No CV file in %s/ — using cv_profile.py", config.CV_DIR)
        _cv, _source = CV, "cv_profile.py"
        return _cv

    data = path.read_bytes()
    digest = hashlib.sha256(data).hexdigest()[:16]
    cache = Path(config.CV_DIR) / ".cache" / f"{digest}.json"

    if cache.exists() and not refresh:
        detail(f"using cached profile for {path.name}")
        _cv = json.loads(cache.read_text(encoding="utf-8"))
    else:
        step(f"reading CV file {path.name}")
        text = read_text(path)
        if len(text) < 200:
            raise RuntimeError(
                f"Could only read {len(text)} characters from {path.name}.\n"
                "If it is a scanned PDF (an image), export it as text-based "
                "PDF or save it as .docx/.txt instead.")
        detail(f"{len(text)} characters read; asking {config.CV_EXTRACT_MODEL} to structure it")
        _cv = extract_profile(text)
        cache.parent.mkdir(parents=True, exist_ok=True)
        cache.write_text(json.dumps(_cv, ensure_ascii=False, indent=2), encoding="utf-8")
        detail(f"profile cached at {cache}")

    _source = path.name
    log.info("CV loaded from %s: %d skills, %d roles",
             path.name, len(_cv.get("skills", [])), len(_cv.get("experience", [])))
    return _cv


def source():
    """Where the current CV came from — shown in the CLI output."""
    load_cv()
    return _source
