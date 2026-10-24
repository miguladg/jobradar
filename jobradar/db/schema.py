"""
db/schema.py — matches the EXISTING database exactly.

No invented columns. Every name here comes from the live schema, so
running this never creates duplicates alongside columns you already have.
"""

import logging

from ..trace import step, detail, warn

log = logging.getLogger(__name__)


# Exactly the columns in your `jobs` table.
JOBS_COLUMNS = [
    ("search_term",           "TEXT"),
    ("title",                 "TEXT"),
    ("company",               "TEXT"),
    ("location",              "TEXT"),
    ("seniority_level",       "TEXT"),
    ("employment_type",       "TEXT"),
    ("job_function",          "TEXT"),
    ("industries",            "TEXT"),
    ("salary",                "TEXT"),
    ("is_remote",             "TEXT"),
    ("date_posted",           "TEXT"),
    ("description",           "TEXT"),
    ("scraped_at",            "TIMESTAMP DEFAULT NOW()"),
    ("scraped_from",          "TEXT"),
    ("jobs_company_url",      "TEXT"),
    ("applicant_link",        "TEXT"),
    ("updated_at",            "TIMESTAMPTZ DEFAULT NOW()"),
    ("formatted_posted_date", "DATE"),
    ("number_applicants",     "INTEGER"),
]


CREATE_JOBS = """
CREATE TABLE IF NOT EXISTS jobs (
    id                    SERIAL PRIMARY KEY,
    search_term           TEXT,
    title                 TEXT,
    company               TEXT,
    location              TEXT,
    seniority_level       TEXT,
    employment_type       TEXT,
    job_function          TEXT,
    industries            TEXT,
    salary                TEXT,
    is_remote             TEXT,
    date_posted           TEXT,
    url                   TEXT NOT NULL,
    description           TEXT,
    scraped_at            TIMESTAMP DEFAULT NOW(),
    scraped_from          TEXT,
    jobs_company_url      TEXT,
    applicant_link        TEXT,
    updated_at            TIMESTAMPTZ DEFAULT NOW(),
    formatted_posted_date DATE,
    number_applicants     INTEGER
);
"""

CREATE_EVALUATIONS = """
CREATE TABLE IF NOT EXISTS job_evaluations (
    job_id               INTEGER PRIMARY KEY REFERENCES jobs(id) ON DELETE CASCADE,
    fit_score            INTEGER,
    verdict              TEXT,
    matching_skills      JSONB,
    missing_requirements JSONB,
    strengths            TEXT,
    concerns             TEXT,
    recommended_action   TEXT,
    model_used           TEXT,
    evaluated_at         TIMESTAMP DEFAULT NOW()
);
"""

INDEXES = [
    "CREATE UNIQUE INDEX IF NOT EXISTS idx_jobs_url ON jobs (url);",
    "CREATE INDEX IF NOT EXISTS idx_jobs_posted ON jobs (formatted_posted_date DESC);",
    "CREATE INDEX IF NOT EXISTS idx_jobs_scraped ON jobs (scraped_at DESC);",
    "CREATE INDEX IF NOT EXISTS idx_eval_score ON job_evaluations (fit_score DESC);",
]


def initialize(conn):
    """
    Create tables if missing; add only columns that are genuinely absent.

    Because JOBS_COLUMNS mirrors your live schema, every ADD COLUMN
    IF NOT EXISTS is a no-op on your database — nothing gets duplicated.
    """
    step("checking database schema")
    with conn.cursor() as cur:
        detail("ensuring table 'jobs' exists")
        cur.execute(CREATE_JOBS)
        detail("ensuring table 'job_evaluations' exists")
        cur.execute(CREATE_EVALUATIONS)

        detail(f"checking {len(JOBS_COLUMNS)} columns on 'jobs'")
        for name, coltype in JOBS_COLUMNS:
            cur.execute(f"ALTER TABLE jobs ADD COLUMN IF NOT EXISTS {name} {coltype};")

    conn.commit()

    # A unique index on url fails if duplicate urls already exist, so each
    # index runs in its own transaction — one failure must not roll back
    # the table creation above.
    detail(f"ensuring {len(INDEXES)} indexes")
    for stmt in INDEXES:
        try:
            with conn.cursor() as cur:
                cur.execute(stmt)
            conn.commit()
        except Exception as exc:
            conn.rollback()
            warn(f"index skipped: {str(exc).split(chr(10))[0]}")
            log.warning("Index skipped: %s", str(exc).split("\n")[0])

    detail("schema ready")
