"""
STEP 3 — db.py
All SQL lives here. The evaluator never touches psycopg2 directly.

WHY: if you later switch to SQLite or a CSV, you only rewrite this file.
This is called "separation of concerns".
"""

import psycopg2
from psycopg2.extras import Json, RealDictCursor

from . import config


def get_connection():
    return psycopg2.connect(**config.DB_CONFIG)


def ensure_results_table(conn):
    """Safe to run every time — 'IF NOT EXISTS' never wipes existing data."""
    with conn.cursor() as cur:
        cur.execute("""
            CREATE TABLE IF NOT EXISTS job_evaluations (
                job_id               INTEGER PRIMARY KEY REFERENCES jobs(id),
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
        """)
    conn.commit()


def fetch_pending_jobs(conn, limit=None):
    """
    Jobs already scraped but NOT yet evaluated.

    The LEFT JOIN + 'IS NULL' pattern means: give me rows from `jobs`
    that have no matching row in `job_evaluations`. This is how you
    make the script resumable — re-running it never re-pays for
    jobs you already scored.
    """
    sql = """
        SELECT id, title, company, location, seniority_level,
               employment_type, is_remote, salary, description, url
        FROM jobs
        WHERE id NOT IN (SELECT job_id FROM job_evaluations)
          AND description IS NOT NULL
          AND description <> 'N/A'
        ORDER BY scraped_at DESC
    """
    params = []
    if limit:
        sql += " LIMIT %s"
        params.append(int(limit))

    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(sql, params)
        return [dict(row) for row in cur.fetchall()]


def save_evaluation(conn, job_id, result, model_used):
    """UPSERT: insert, or overwrite if this job was scored before."""
    with conn.cursor() as cur:
        cur.execute("""
            INSERT INTO job_evaluations (
                job_id, fit_score, verdict, matching_skills,
                missing_requirements, strengths, concerns,
                recommended_action, model_used
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (job_id) DO UPDATE SET
                fit_score            = EXCLUDED.fit_score,
                verdict              = EXCLUDED.verdict,
                matching_skills      = EXCLUDED.matching_skills,
                missing_requirements = EXCLUDED.missing_requirements,
                strengths            = EXCLUDED.strengths,
                concerns             = EXCLUDED.concerns,
                recommended_action   = EXCLUDED.recommended_action,
                model_used           = EXCLUDED.model_used,
                evaluated_at         = NOW();
        """, (
            job_id,
            result.get("fit_score"),
            result.get("verdict"),
            Json(result.get("matching_skills", [])),
            Json(result.get("missing_requirements", [])),
            result.get("strengths"),
            result.get("concerns"),
            result.get("recommended_action"),
            model_used,
        ))
    conn.commit()


def fetch_top_matches(conn, limit=10):
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute("""
            SELECT j.title, j.company, j.url, j.is_remote,
                   e.fit_score, e.verdict, e.recommended_action, e.strengths
            FROM job_evaluations e
            JOIN jobs j ON j.id = e.job_id
            ORDER BY e.fit_score DESC NULLS LAST
            LIMIT %s;
        """, (limit,))
        return [dict(row) for row in cur.fetchall()]
