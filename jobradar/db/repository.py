"""
db/repository.py — every SQL query in the project lives here.

The "repository pattern": the rest of the code asks for data in Python
terms ("give me pending jobs") and never writes SQL itself. If you move
to another database later, this is the only file you rewrite.
"""

import logging

from psycopg2.extras import Json, execute_values

from .. import config
from .connection import dict_cursor
from ..trace import step, detail, warn

log = logging.getLogger(__name__)


# Columns we insert. Order must match the tuple built in _to_row().
INSERT_COLUMNS = [
    "url", "title", "company", "jobs_company_url", "location",
    "formatted_posted_date", "date_posted", "seniority_level",
    "employment_type", "job_function", "industries", "salary",
    "number_applicants", "applicant_link", "is_remote",
    "description", "search_term", "scraped_from",
]


def _to_row(job: dict) -> tuple:
    return tuple(job.get(col) for col in INSERT_COLUMNS)


def save_jobs(conn, jobs: list) -> dict:
    """
    Bulk-insert jobs, updating any that already exist.

    ON CONFLICT (url) DO UPDATE (an "upsert") instead of DO NOTHING:
    if a posting is re-scraped, fields like the applicant count may have
    changed, and you want the fresh value. COALESCE keeps the old value
    whenever the new scrape returned NULL, so a failed parse never
    erases good data you already had.
    """
    if not jobs:
        step("nothing to save")
        return {"inserted": 0, "updated": 0}

    step(f"saving {len(jobs)} jobs to PostgreSQL")
    detail(f"writing {len(INSERT_COLUMNS)} columns per job")

    cols = ", ".join(INSERT_COLUMNS)
    updates = ", ".join(
        f"{c} = COALESCE(EXCLUDED.{c}, jobs.{c})"
        for c in INSERT_COLUMNS if c != "url"
    )

    sql = f"""
        INSERT INTO jobs ({cols})
        VALUES %s
        ON CONFLICT (url) DO UPDATE SET
            {updates},
            updated_at = NOW()
        RETURNING (xmax = 0) AS was_inserted;
    """

    rows = [_to_row(j) for j in jobs]

    with conn.cursor() as cur:
        # xmax = 0 is a PostgreSQL trick: it's true for INSERTs and
        # false for UPDATEs, so we can count each separately.
        results = execute_values(cur, sql, rows, fetch=True)

    conn.commit()

    inserted = sum(1 for r in results if r[0])
    detail(f"INSERT ... ON CONFLICT (url) DO UPDATE sent as one batch")
    stats = {"inserted": inserted, "updated": len(results) - inserted}
    detail(f"{stats['inserted']} new rows, {stats['updated']} existing rows updated")
    log.info("Saved: %d new, %d updated", stats["inserted"], stats["updated"])
    return stats


def get_existing_urls(conn, days_back: int = 30) -> set:
    """
    URLs already stored, so the scraper can skip fetching their detail
    pages. Each skipped job saves an HTTP request and ~3 seconds.
    """
    with conn.cursor() as cur:
        cur.execute(
            "SELECT url FROM jobs WHERE scraped_at > NOW() - make_interval(days => %s);",
            (days_back,),
        )
        return {row[0] for row in cur.fetchall()}


def fetch_pending_evaluation(conn, limit=None, days_back="default"):
    """
    Jobs that are (a) not yet scored AND (b) posted within the window.

    THE WINDOW IS ON BY DEFAULT. Scraping and evaluating are separate
    steps, so a job scraped last week is still sitting in the database
    today. Without this filter you would pay OpenAI to score a posting
    that is already 10 days old and effectively closed.

    Pass days_back=None explicitly to score everything regardless of age.

    NULL formatted_posted_date: a few postings don't expose a date. Excluding them
    outright would silently drop real jobs, so we fall back to when we
    scraped them — if we saw it within the window, it was recent then.
    """
    if days_back == "default":
        days_back = config.DAYS_BACK

    sql = """
        SELECT id, title, company, location, seniority_level,
               employment_type, is_remote, salary, number_applicants,
               formatted_posted_date, description, url
        FROM jobs
        WHERE id NOT IN (SELECT job_id FROM job_evaluations)
          AND description IS NOT NULL
          AND length(description) > 100
    """
    params = []

    if days_back is not None:
        # CURRENT_DATE - <int> is plain date arithmetic — clearer and
        # safer than interpolating into an INTERVAL string literal.
        sql += """
          AND (
                formatted_posted_date >= CURRENT_DATE - %s
             OR (formatted_posted_date IS NULL
                 AND scraped_at >= NOW() - make_interval(days => %s))
          )
        """
        params.extend([days_back, days_back])

    sql += " ORDER BY formatted_posted_date DESC NULLS LAST, scraped_at DESC"

    if limit:
        sql += " LIMIT %s"
        params.append(int(limit))

    with dict_cursor(conn) as cur:
        cur.execute(sql, params)
        return [dict(r) for r in cur.fetchall()]


def count_stale_unevaluated(conn, days_back=None):
    """
    How many unscored jobs the window is skipping. Reported by the CLI so
    the filter is visible rather than a silent omission.
    """
    days_back = days_back if days_back is not None else config.DAYS_BACK
    with conn.cursor() as cur:
        cur.execute("""
            SELECT COUNT(*) FROM jobs
            WHERE id NOT IN (SELECT job_id FROM job_evaluations)
              AND description IS NOT NULL
              AND formatted_posted_date IS NOT NULL
              AND formatted_posted_date < CURRENT_DATE - %s;
        """, (days_back,))
        return cur.fetchone()[0]


def save_evaluation(conn, job_id: int, result: dict, model: str):
    step(f"saving evaluation for job id {job_id}", level=1)
    with conn.cursor() as cur:
        cur.execute("""
            INSERT INTO job_evaluations (
                job_id, fit_score, verdict, matching_skills,
                missing_requirements, strengths, concerns,
                recommended_action, model_used
            ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)
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
            model,
        ))
    conn.commit()
    detail("evaluation committed", level=2)


def fetch_top_matches(conn, limit=10, min_score=0, days_back="default"):
    """
    Best matches. Also windowed by default — a great score on a job posted
    two weeks ago is not an opportunity, it's a closed listing.
    """
    if days_back == "default":
        days_back = config.DAYS_BACK

    sql = """
        SELECT j.title, j.company, j.location, j.url, j.formatted_posted_date,
               j.is_remote, j.salary, j.number_applicants,
               e.fit_score, e.verdict, e.recommended_action,
               e.strengths, e.concerns, e.missing_requirements
        FROM job_evaluations e
        JOIN jobs j ON j.id = e.job_id
        WHERE e.fit_score >= %s
    """
    params = [min_score]

    if days_back is not None:
        sql += """
          AND (
                j.formatted_posted_date >= CURRENT_DATE - %s
             OR (j.formatted_posted_date IS NULL
                 AND j.scraped_at >= NOW() - make_interval(days => %s))
          )
        """
        params.extend([days_back, days_back])

    sql += " ORDER BY e.fit_score DESC NULLS LAST LIMIT %s"
    params.append(limit)

    with dict_cursor(conn) as cur:
        cur.execute(sql, params)
        return [dict(r) for r in cur.fetchall()]


def get_stats(conn) -> dict:
    with dict_cursor(conn) as cur:
        cur.execute("""
            SELECT
                (SELECT COUNT(*) FROM jobs)                          AS total_jobs,
                (SELECT COUNT(*) FROM jobs
                  WHERE formatted_posted_date >= CURRENT_DATE - %s)            AS in_window,
                (SELECT COUNT(*) FROM job_evaluations)               AS evaluated,
                (SELECT COUNT(*) FROM job_evaluations
                  WHERE fit_score >= 70)                             AS good_matches,
                (SELECT MAX(scraped_at) FROM jobs)                   AS last_scrape;
        """, (config.DAYS_BACK,))
        return dict(cur.fetchone())
