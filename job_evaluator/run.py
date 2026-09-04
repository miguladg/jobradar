"""
STEP 6 — run.py
The entry point. It orchestrates; it contains no business logic.

Usage:
    python -m job_evaluator.run --limit 10    # evaluate 10 pending jobs
    python -m job_evaluator.run --top 15      # show your best 15 matches
    python -m job_evaluator.run --dry-run     # test 1 job, nothing saved
"""

import argparse
import sys

from . import config, db
from .evaluator import evaluate_job


def cmd_evaluate(limit=None, dry_run=False):
    config.validate()

    conn = db.get_connection()
    db.ensure_results_table(conn)

    jobs = db.fetch_pending_jobs(conn, limit=1 if dry_run else limit)
    print(f"\n{len(jobs)} job(s) pending evaluation.\n")

    for i, job in enumerate(jobs, 1):
        print(f"[{i}/{len(jobs)}] {job['title']} @ {job['company']}")

        result = evaluate_job(job)

        if "error" in result:
            print(f"    ✗ {result['error']}\n")
            continue

        print(f"    score={result['fit_score']} "
              f"verdict={result['verdict']} "
              f"action={result['recommended_action']}")

        if dry_run:
            print(f"    strengths: {result['strengths']}")
            print(f"    concerns:  {result['concerns']}")
            print("\n[dry run — nothing saved]\n")
        else:
            db.save_evaluation(conn, job["id"], result, config.MODEL)
        print()

    conn.close()


def cmd_top(limit=10):
    conn = db.get_connection()
    rows = db.fetch_top_matches(conn, limit)
    conn.close()

    if not rows:
        print("No evaluations yet. Run the evaluate command first.")
        return

    print(f"\n{'SCORE':<6}{'VERDICT':<17}{'ACTION':<24}JOB")
    print("─" * 100)
    for r in rows:
        print(f"{r['fit_score']:<6}{r['verdict']:<17}{r['recommended_action']:<24}"
              f"{r['title']} @ {r['company']}")
        print(f"{'':<47}{r['url']}")


def main():
    parser = argparse.ArgumentParser(description="Evaluate scraped jobs against your CV")
    parser.add_argument("--limit", type=int, help="max jobs to evaluate")
    parser.add_argument("--top", type=int, help="show best N results and exit")
    parser.add_argument("--dry-run", action="store_true", help="test 1 job, save nothing")
    args = parser.parse_args()

    try:
        if args.top:
            cmd_top(args.top)
        else:
            cmd_evaluate(limit=args.limit, dry_run=args.dry_run)
    except RuntimeError as exc:
        print(f"\n{exc}\n", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
