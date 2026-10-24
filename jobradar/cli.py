"""
cli.py — one entry point with subcommands.

    python -m jobradar scrape          # last DAYS_BACK days → database
    python -m jobradar evaluate        # score with OpenAI
    python -m jobradar top             # best matches
    python -m jobradar stats           # what's in the database
    python -m jobradar run             # scrape + evaluate + top
    python -m jobradar cv              # show what was read from your CV

The old project needed two separate scripts with different settings.
One entry point means one place to look.
"""

import argparse
import logging
import sys
from datetime import date

from . import config, cv_loader, logging_setup, trace
from .trace import step, detail, warn, section
from .db import connection, repository, schema
from .scraper.linkedin import LinkedInScraper
from .evaluation.evaluator import evaluate_job

log = logging.getLogger(__name__)


# ── scrape ─────────────────────────────────────────────────────

def cmd_scrape(args):
    days = args.days or config.DAYS_BACK
    section("SCRAPE")

    with connection.get_connection() as conn:
        schema.initialize(conn)

        # Skip detail pages for jobs we already have — saves ~3s each.
        if args.force:
            step("--force given, re-fetching even known jobs")
            known = set()
        else:
            known = repository.get_existing_urls(conn)

        step("starting the LinkedIn scraper")
        scraper = LinkedInScraper(days_back=days, known_urls=known)
        terms = [args.term] if args.term else config.SEARCH_TERMS
        totals = {"found": 0, "inserted": 0, "updated": 0}

        # Save after EACH term, so Ctrl+C or a crash halfway through
        # keeps everything scraped up to that point.
        for i, term in enumerate(terms):
            if i:
                scraper.pause(config.DELAY_BETWEEN_TERMS)
            jobs = scraper.scrape_term(term)
            totals["found"] += len(jobs)

            if args.dry_run:
                for j in jobs[:10]:
                    log.info("  %s | %s @ %s (%s)",
                             j.get("formatted_posted_date"), j.get("title"),
                             j.get("company"), j.get("is_remote"))
                continue

            stats = repository.save_jobs(conn, jobs)
            totals["inserted"] += stats["inserted"]
            totals["updated"] += stats["updated"]

        scraper.log_summary()

        if args.dry_run:
            step(f"--dry-run: {totals['found']} jobs found, NOT saved")
        elif not totals["found"]:
            step(f"no new jobs found in the last {days} day(s)")
        else:
            step(f"scrape complete: {totals['inserted']} new, "
                 f"{totals['updated']} updated")


# ── evaluate ───────────────────────────────────────────────────

def cmd_evaluate(args):
    section("EVALUATE")
    step("checking that the OpenAI key is present")
    config.validate_openai()

    # Load the CV before the loop so a bad file fails once, up front.
    cv_loader.load_cv()
    step(f"scoring against CV: {cv_loader.source()}")

    # The window applies to evaluation too, not just scraping.
    # --all-ages turns it off; --days overrides the number.
    days = None if getattr(args, "all_ages", False) else (args.days or config.DAYS_BACK)

    with connection.get_connection() as conn:
        schema.initialize(conn)

        jobs = repository.fetch_pending_evaluation(
            conn,
            limit=1 if args.dry_run else args.limit,
            days_back=days,
        )

        if days is not None:
            skipped = repository.count_stale_unevaluated(conn, days)
            log.info("Only evaluating jobs posted in the last %d day(s).", days)
            if skipped:
                log.info("Skipping %d unscored job(s) older than that "
                         "(use --all-ages to include them).", skipped)

        if not jobs:
            log.info("Nothing to evaluate. Run `scrape` first.")
            return

        log.info("Evaluating %d job(s) with %s\n", len(jobs), config.OPENAI_MODEL)

        for i, job in enumerate(jobs, 1):
            age = ""
            if job.get("formatted_posted_date"):
                days_old = (date.today() - job["formatted_posted_date"]).days
                age = f"  (posted {days_old}d ago)"

            log.info("[%d/%d] %s @ %s%s", i, len(jobs),
                     job["title"], job["company"], age)

            result = evaluate_job(job)

            if "error" in result:
                warn(f"failed: {result['error']}", level=1)
                continue

            detail(f"score {result.get('fit_score')} | "
                   f"{result.get('verdict')} | "
                   f"{result.get('recommended_action')}", level=1)

            if args.dry_run:
                detail(f"strengths: {result.get('strengths')}", level=1)
                detail(f"concerns:  {result.get('concerns')}", level=1)
                step("--dry-run: nothing saved")
                return

            repository.save_evaluation(conn, job["id"], result, config.OPENAI_MODEL)


# ── top ────────────────────────────────────────────────────────

def cmd_top(args):
    section("TOP MATCHES")
    with connection.get_connection() as conn:
        schema.initialize(conn)
        days = None if args.all_ages else (args.days or config.DAYS_BACK)
        rows = repository.fetch_top_matches(
            conn, limit=args.limit or 10, min_score=args.min_score, days_back=days
        )

    if not rows:
        if days is not None:
            print(f"No scored jobs posted in the last {days} day(s).")
            print("Try: python -m jobradar top --all-ages")
        else:
            print("No evaluations yet. Run: python -m jobradar evaluate")
        return

    print()
    for r in rows:
        print(f"  [{r['fit_score']:>3}] {r['title']} @ {r['company']}")
        print(f"        {r['verdict']} → {r['recommended_action']}")
        print(f"        {r['location']} | remote: {r.get('is_remote') or '?'} | posted {r['formatted_posted_date']}")
        if r.get("strengths"):
            print(f"        + {r['strengths']}")
        if r.get("concerns"):
            print(f"        - {r['concerns']}")
        print(f"        {r['url']}")
        print()


# ── stats ──────────────────────────────────────────────────────

def cmd_stats(args):
    section("DATABASE STATS")
    with connection.get_connection() as conn:
        schema.initialize(conn)
        step("counting rows")
        s = repository.get_stats(conn)

    print(f"""
  Total jobs stored     {s['total_jobs']}
  Posted last {config.DAYS_BACK} day(s)   {s['in_window']}
  Evaluated             {s['evaluated']}
  Good matches (70+)    {s['good_matches']}
  Last scrape           {s['last_scrape']}
""")


# ── cv ─────────────────────────────────────────────────────────

def cmd_cv(args):
    """Shows what the evaluator will see, so you can check the extraction
    before spending money scoring jobs against it."""
    section("CV PROFILE")
    cv = cv_loader.load_cv(refresh=args.refresh)
    print(f"""
  Source        {cv_loader.source()}
  Name          {cv.get("name")}
  Location      {cv.get("location")}  (open to: {", ".join(cv.get("open_to", [])) or "-"})
  Target roles  {", ".join(cv.get("target_roles", [])) or "-"}
  Languages     {", ".join(f"{k} {v}" for k, v in cv.get("languages", {}).items()) or "-"}
  Skills ({len(cv.get("skills", []))})   {", ".join(cv.get("skills", []))}
""")
    for job in cv.get("experience", []):
        print(f"  {job['start']} → {job['end']}  {job['title']} @ {job['company']}")
        print(f"      {', '.join(job.get('technologies', []))}")
    print()


# ── run (full pipeline) ────────────────────────────────────────

def cmd_run(args):
    cmd_scrape(args)
    cmd_evaluate(args)
    cmd_top(args)


# ── argument parsing ───────────────────────────────────────────

def build_parser():
    p = argparse.ArgumentParser(
        prog="jobradar",
        description="Scrape recent job postings and score them against your CV.",
    )
    p.add_argument("--quiet", action="store_true",
                   help="turn off the step-by-step output")
    sub = p.add_subparsers(dest="command", required=True)

    def add_common(sp):
        sp.add_argument("--days", type=int,
                        help=f"days back (default {config.DAYS_BACK})")
        sp.add_argument("--limit", type=int, help="max items")
        sp.add_argument("--dry-run", action="store_true", help="save nothing")
        sp.add_argument("--force", action="store_true", help="re-fetch known jobs")
        sp.add_argument("--term", help="scrape a single search term")
        sp.add_argument("--min-score", type=int, default=0)
        sp.add_argument("--all-ages", action="store_true",
                        help="ignore the day window (evaluate/show old jobs too)")

    for name, func, help_text in [
        ("scrape",   cmd_scrape,   "fetch recent jobs into the database"),
        ("evaluate", cmd_evaluate, "score saved jobs with OpenAI"),
        ("top",      cmd_top,      "show your best matches"),
        ("stats",    cmd_stats,    "database summary"),
        ("run",      cmd_run,      "scrape + evaluate + top"),
    ]:
        sp = sub.add_parser(name, help=help_text)
        add_common(sp)
        sp.set_defaults(func=func)

    sp = sub.add_parser("cv", help="show the profile read from your CV file")
    sp.add_argument("--refresh", action="store_true",
                    help="re-read the CV file even if a cached profile exists")
    sp.set_defaults(func=cmd_cv)

    return p


def main():
    args = build_parser().parse_args()
    trace.enabled = not args.quiet
    logging_setup.setup(level="WARNING" if args.quiet else config.LOG_LEVEL)

    try:
        args.func(args)
    except RuntimeError as exc:
        print(f"\n{exc}\n", file=sys.stderr)
        sys.exit(1)
    except KeyboardInterrupt:
        print("\nInterrupted. Data scraped so far was already saved.", file=sys.stderr)
        sys.exit(130)


if __name__ == "__main__":
    main()
