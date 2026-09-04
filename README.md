# Job Evaluator — OpenAI Tool Calling

Evaluates the LinkedIn jobs your scraper saved in PostgreSQL against your CV,
using the OpenAI **tool calling** API.

## Project layout

```
your_project/
├── api_call.py            # your existing scraper (add it here)
├── requirements.txt
├── .env                   # YOUR SECRETS — never commit
├── .env.example           # template — safe to commit
├── .gitignore
├── check_setup.py         # verify setup before spending money
├── test_loop.py           # offline test, no API key needed
└── job_evaluator/
    ├── __init__.py
    ├── config.py          # all settings
    ├── cv_profile.py      # your CV as structured data
    ├── db.py              # all SQL
    ├── tools.py           # OpenAI tool definitions  ← core
    ├── evaluator.py       # the tool-calling loop    ← core
    └── run.py             # CLI entry point
```

## Setup (5 commands)

```bash
# 1. Install dependencies
pip install -r requirements.txt --break-system-packages

# 2. Lock down the secrets file (Linux/Mac)
chmod 600 .env

# 3. Fill in your real OPENAI_API_KEY and DB_PASSWORD
nano .env

# 4. Verify everything BEFORE spending money (prints no secrets)
python check_setup.py

# 5. Test the tool loop offline — free, no API key needed
python test_loop.py
```

## Security checklist

- [ ] `.env` is listed in `.gitignore`
- [ ] `chmod 600 .env` — owner-only permissions
- [ ] Spending limit set at platform.openai.com → Billing → Limits
- [ ] Ran `git log --all --full-history -- .env` — if it returns
      anything, the key is in your history: **rotate it**
- [ ] Database user has only the permissions it needs (see below)

### Least-privilege database user

Connecting as `postgres` (superuser) means a bug could drop your
scraped data. Create a limited user instead:

```sql
CREATE USER job_evaluator WITH PASSWORD 'strong_password';
GRANT SELECT ON jobs TO job_evaluator;
GRANT SELECT, INSERT, UPDATE ON job_evaluations TO job_evaluator;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO job_evaluator;
```

Then set `DB_USER=job_evaluator` in `.env`. Now this tool can read
postings and write scores, but can never delete your data.

## Usage

```bash
# Test on ONE job — costs ~1 cent, saves nothing
python -m job_evaluator.run --dry-run

# Evaluate 10 pending jobs and save results
python -m job_evaluator.run --limit 10

# Evaluate everything not yet scored
python -m job_evaluator.run

# Show your best matches
python -m job_evaluator.run --top 15
```

## Typical workflow

```bash
python api_call.py                       # 1. scrape new jobs
python -m job_evaluator.run --limit 20   # 2. score them
python -m job_evaluator.run --top 10     # 3. read the best ones
```

## How tool calling works

A tool-calling request is a **conversation**, not one request:

| Step | Who | What |
|------|-----|------|
| 1 | You → OpenAI | Job posting + list of available tools |
| 2 | OpenAI → You | "Run `calculate_skill_overlap(['sql','python'])`" |
| 3 | You | Execute the real Python function locally |
| 4 | You → OpenAI | "Result: 60% overlap, missing: airflow" |
| 5 | | Repeat 2–4 |
| 6 | OpenAI → You | Calls `submit_evaluation(...)` = final answer |

OpenAI never runs your code — it only asks you to run it.

**Why tools instead of plain JSON output?** Models are unreliable at exact
matching and arithmetic. So the work is split:

- **Python calculates** — skill overlap, years of experience (deterministic)
- **The model judges** — reads the posting, weighs seniority and trade-offs

## Results

Written to a new `job_evaluations` table. Your `jobs` table is only read,
never modified.

| Column | Meaning |
|--------|---------|
| `fit_score` | 0–100 |
| `verdict` | strong_match / good_match / possible_match / weak_match / no_match |
| `matching_skills` | JSONB array |
| `missing_requirements` | JSONB array |
| `strengths` / `concerns` | short prose |
| `recommended_action` | apply_now / apply_with_tailored_cv / stretch_application / skip |

Re-running is safe and cheap: jobs already scored are skipped automatically.

## Cost

With `gpt-4o-mini`, roughly **$0.01 per job**. 100 jobs ≈ $1.
Switch models with `OPENAI_MODEL=gpt-4o` in `.env` for higher quality.

## Customising

- **Change scoring rules** → `SYSTEM_PROMPT` in `evaluator.py`
- **Update your skills** → `CV` in `cv_profile.py`
- **Add a new tool** → write the function + schema in `tools.py`,
  then register it in `TOOL_IMPLEMENTATIONS`
