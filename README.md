# JobRadar

A CLI tool that scrapes LinkedIn job postings in Colombia, stores them in PostgreSQL, and scores each one against your CV using OpenAI's tool-calling API — so you wake up to a ranked list of the best matches from the last day (configurable with `DAYS_BACK`).

---

## How it works

```
python -m jobradar run
```

1. **Scrape** — fetches LinkedIn listings for each search term, extracts structured data, and saves them to PostgreSQL in per-term batches.
2. **Evaluate** — sends each unscored job to OpenAI, which calls Python tools to calculate real skill overlap and experience years from your CV, then returns a score and verdict.
3. **Top** — prints your best matches ranked by fit score.

Each step can also be run independently (see [Commands](#commands)).

---

## Project structure

```
search_bot/
├── api_call.py                  # Standalone scraper (no package needed)
└── jobradar/                    # Main CLI package
    ├── __init__.py
    ├── __main__.py              # Makes `python -m jobradar` work
    ├── cli.py                   # Entry point — all subcommands
    ├── config.py                # Every setting in one place
    ├── cv_profile.py            # Your CV as structured data
    ├── logging_setup.py         # Console + file logging
    ├── trace.py                 # Step-by-step progress output
    ├── scraper/
    │   ├── __init__.py
    │   └── linkedin.py          # LinkedInScraper class
    ├── db/
    │   ├── __init__.py
    │   ├── connection.py        # Context-managed PostgreSQL connection
    │   ├── repository.py        # All SQL queries live here
    │   └── schema.py            # CREATE TABLE / ADD COLUMN IF NOT EXISTS
    └── evaluation/
        ├── __init__.py
        ├── evaluator.py         # OpenAI tool-calling loop
        └── tools.py             # Tool schemas + Python implementations
```

### File responsibilities

| File | What it does |
|------|-------------|
| `api_call.py` | Self-contained flat scraper — run directly without installing the package |
| `config.py` | Single source of truth for every setting — edit this, never the internals |
| `cv_profile.py` | Your skills, roles and dates as Python dicts — powers the evaluation tools |
| `cli.py` | Parses CLI arguments and calls the right function for each subcommand |
| `scraper/linkedin.py` | `LinkedInScraper` class — fetches search pages and detail pages with retries |
| `db/connection.py` | `get_connection()` context manager — guarantees the connection closes on error |
| `db/repository.py` | Every SQL query in the project — nowhere else writes SQL |
| `db/schema.py` | Idempotent schema setup — safe to call on every run |
| `evaluation/tools.py` | Python tools OpenAI can call: skill overlap, experience years, full CV |
| `evaluation/evaluator.py` | Manages the multi-turn tool-calling conversation with OpenAI |
| `logging_setup.py` | Configures console (clean) + file (timestamped) handlers |
| `trace.py` | `step()` / `detail()` / `warn()` / `section()` — consistent progress output |

---

## Requirements

- Python 3.10+
- PostgreSQL database (tables created automatically on first run)
- OpenAI API key (only needed for the `evaluate` command)

### Install dependencies

```bash
pip install requests beautifulsoup4 psycopg2-binary openai python-dotenv
```

---

## Configuration

Create a `.env` file in the project root:

```env
# Database
DB_HOST=localhost
DB_PORT=5432
DB_NAME=linkedin_jobs
DB_USER=postgres
DB_PASSWORD=yourpassword

# OpenAI (only needed for `evaluate`)
OPENAI_API_KEY=sk-...
OPENAI_MODEL=gpt-4o-mini

# Scraper behaviour (all optional — defaults shown)
DAYS_BACK=1
SEARCH_LOCATION=Colombia
WORK_TYPES=1,2
MAX_PAGES_PER_TERM=8
LOG_LEVEL=INFO
LOG_FILE=jobradar.log
```

### Environment variable reference

| Variable | Default | Description |
|----------|---------|-------------|
| `DB_HOST` | `localhost` | PostgreSQL host |
| `DB_PORT` | `5432` | PostgreSQL port |
| `DB_NAME` | `linkedin_jobs` | Database name |
| `DB_USER` | `postgres` | Database user |
| `DB_PASSWORD` | *(empty)* | Database password |
| `OPENAI_API_KEY` | — | Required for `evaluate` |
| `OPENAI_MODEL` | `gpt-4o-mini` | Model used for scoring |
| `DAYS_BACK` | `1` | How many days back to scrape and evaluate |
| `SEARCH_LOCATION` | `Colombia` | LinkedIn location filter |
| `WORK_TYPES` | `1,2` | `1`=On-site, `2`=Remote, `3`=Hybrid |
| `MAX_PAGES_PER_TERM` | `8` | LinkedIn result pages per search term |
| `LOG_LEVEL` | `INFO` | Console log verbosity |
| `LOG_FILE` | `jobradar.log` | Log file path (`""` to disable) |

---

## Commands

```bash
# Full pipeline: scrape + evaluate + show top matches
python -m jobradar run

# Fetch new jobs into the database
python -m jobradar scrape

# Score unscored jobs with OpenAI
python -m jobradar evaluate

# Show best matches
python -m jobradar top

# Database summary
python -m jobradar stats
```

### Common flags (all commands)

| Flag | Description |
|------|-------------|
| `--days N` | Override `DAYS_BACK` for this run |
| `--dry-run` | Process but save nothing |
| `--quiet` | Suppress step-by-step output |
| `--force` | Re-fetch jobs already in the database |
| `--term "Data Analyst"` | Scrape a single search term only |
| `--limit N` | Process at most N items |
| `--min-score N` | Show only jobs scoring at or above N (default 0) |
| `--all-ages` | Ignore the day window — evaluate/show older jobs too |

### Examples

```bash
# Scrape only Data Engineer postings from the last 7 days
python -m jobradar scrape --days 7 --term "Data Engineer"

# Score up to 20 jobs, save nothing (preview mode)
python -m jobradar evaluate --limit 20 --dry-run

# Show top 5 matches scoring 70 or above
python -m jobradar top --limit 5 --min-score 70

# Show all-time top matches, ignoring the day filter
python -m jobradar top --all-ages --min-score 60

# Run everything quietly (no step output)
python -m jobradar run --quiet
```

---

## Database schema

Tables are created automatically on first run. The schema matches your existing database exactly — running `schema.initialize()` on an existing database is a no-op.

### `jobs`

| Column | Type | Notes |
|--------|------|-------|
| `id` | SERIAL PK | |
| `url` | TEXT NOT NULL UNIQUE | Deduplication key |
| `title` | TEXT | |
| `company` | TEXT | |
| `location` | TEXT | |
| `seniority_level` | TEXT | |
| `employment_type` | TEXT | |
| `job_function` | TEXT | |
| `industries` | TEXT | |
| `salary` | TEXT | |
| `is_remote` | TEXT | `"Sí"` or `"No"` |
| `date_posted` | TEXT | Raw LinkedIn string, e.g. `"2 days ago"` |
| `formatted_posted_date` | DATE | Parsed from `date_posted` |
| `description` | TEXT | Full job description |
| `number_applicants` | INTEGER | |
| `search_term` | TEXT | Which keyword found this job |
| `scraped_from` | TEXT | Source tag (`"linkedin"`) |
| `scraped_at` | TIMESTAMP | When it was scraped |
| `updated_at` | TIMESTAMPTZ | Updated on each upsert |
| `jobs_company_url` | TEXT | Company LinkedIn URL (reserved) |
| `applicant_link` | TEXT | Apply URL (reserved) |

### `job_evaluations`

| Column | Type | Notes |
|--------|------|-------|
| `job_id` | INTEGER PK → `jobs.id` | |
| `fit_score` | INTEGER | 0–100 |
| `verdict` | TEXT | `strong_match` / `good_match` / `possible_match` / `weak_match` / `no_match` |
| `matching_skills` | JSONB | Skills the candidate has |
| `missing_requirements` | JSONB | Skills the candidate lacks |
| `strengths` | TEXT | 2-sentence positive summary |
| `concerns` | TEXT | 2-sentence honest gaps |
| `recommended_action` | TEXT | `apply_now` / `apply_with_tailored_cv` / `stretch_application` / `skip` |
| `model_used` | TEXT | OpenAI model that scored this job |
| `evaluated_at` | TIMESTAMP | |

---

## How the evaluation works

The evaluator uses OpenAI's **tool-calling** API — the model does not guess; it calls Python functions to compute exact answers.

### Tools available to the model

| Tool | What it does |
|------|-------------|
| `calculate_skill_overlap` | Compares the posting's required skills against `cv_profile.py` — returns matched, missing, and overlap % |
| `get_experience_years` | Sums the real months you used a technology across all roles from your CV dates — the model cannot fabricate this |
| `get_candidate_profile` | Returns the full CV dict on demand — keeps token usage low when the model only needs a skill list |
| `submit_evaluation` | Final call — the model submits its structured verdict after gathering evidence |

### Scoring rules (in the system prompt)

- Penalizes heavily when a **required core technology is entirely absent** from the CV.
- Penalizes **seniority mismatch** (Senior/Lead for a mid-level candidate).
- Flags **native/C1 English** requirements as a concern (candidate is B2).
- Flags **on-site roles outside Bogotá**.
- Below 50 = `skip`. Only 80+ = `apply_now`.

### Conversation flow

```
You → OpenAI   job text + 4 tool schemas
OpenAI → You   "call calculate_skill_overlap(['sql','python','airflow'])"
You            executes the real Python function
You → OpenAI   result: {"matched": [...], "missing": ["airflow"], "overlap_percentage": 72}
OpenAI → You   "call get_experience_years('sql')"
You            executes → {"total_years": 3.5, "roles_using_it": [...]}
You → OpenAI   result
OpenAI → You   calls submit_evaluation({fit_score: 74, verdict: "good_match", ...})
Done
```

---

## Customising your CV

Edit `jobradar/cv_profile.py`. The file is plain Python — no JSON, no YAML.

```python
CV = {
    "skills": ["python", "sql", "aws", "docker", ...],
    "experience": [
        {
            "title": "Backend Developer",
            "company": "Acme Corp",
            "start": "2023-01",   # YYYY-MM
            "end":   "2024-06",
            "technologies": ["python", "aws", "postgresql"],
        },
    ],
}
```

Key points:
- `skills` drives `calculate_skill_overlap` — keep it lowercase.
- `experience[].technologies` drives `get_experience_years` — also lowercase.
- `start` / `end` must be `"YYYY-MM"` — the tool does exact date arithmetic.

---

## Customising search terms

Edit `SEARCH_TERMS` in `config.py`:

```python
SEARCH_TERMS = [
    "Data Analyst",
    "Data Engineer",
    "Backend Developer",
    "Systems Integration Engineer",
]
```

Or run a single term without changing the file:

```bash
python -m jobradar scrape --term "Machine Learning Engineer"
```

---

## Using `api_call.py` (standalone mode)

If you don't want the full package, `api_call.py` is a self-contained scraper that writes directly to PostgreSQL. It reads the same `DAYS_BACK` and `DB_*` environment variables. It requires the `jobs` table to already exist.

```bash
# Set env vars, then:
python api_call.py
```

It does not evaluate jobs — use the `jobradar` package for that.

---

## Logs

Every run appends to `jobradar.log` (configurable via `LOG_FILE`). The console shows clean step output; the file includes timestamps and module names for debugging.

```
2026-09-08 09:14:02 [INFO   ] jobradar.scraper.linkedin: Saved: 12 new, 3 updated
2026-09-08 09:14:45 [INFO   ] jobradar.evaluation.evaluator: [1/12] Data Analyst @ Bancolombia
```

---

## Notes

- LinkedIn's HTML structure changes over time. If scraping stops returning results, the CSS class selectors in `scraper/linkedin.py` (`_parse_detail`) may need updating.
- The scraper uses random delays between requests to avoid rate limiting. Do not remove them.
- This tool is for personal/educational use. Review LinkedIn's Terms of Service before using in production or at scale.
