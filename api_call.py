import os
import requests
from bs4 import BeautifulSoup
import time

import re
import random
import psycopg2
from psycopg2.extras import execute_values
from datetime import datetime, timezone

headers = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                  "AppleWebKit/537.36 (KHTML, like Gecko) "
                  "Chrome/124.0.0.0 Safari/537.36",
    "Accept-Language": "en-US,en;q=0.9",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}

# r259200 = 3 days | r86400 = 1 day
base_url = (
    "https://www.linkedin.com/jobs/search/"
    "?f_TPR=r259200&f_WT=1%2C2&location=Colombia"
    "&origin=JOB_SEARCH_PAGE_JOB_FILTER&keywords="
)

search_terms = [
    "Data%20Analyst",
    "Data%20Engineer",
    "Data%20Scientist",
    "Analyst",
    "Systems%20Integration%20Engineer",
]

seen_urls = set()
results = []


# ==============================================================
# DATABASE FUNCTION
# ==============================================================

def guardar_en_postgres(conn, jobs):
    """
    Inserts a batch of jobs in a single round-trip and skips
    duplicates automatically via ON CONFLICT (url) DO NOTHING.
    """
    if not jobs:
        print("  ⚠️  No jobs to save in this batch.")
        return

    insert_sql = """
    INSERT INTO jobs (
        search_term, title, company, location, seniority_level, employment_type,
        job_function, industries, salary, is_remote, date_posted, url, description,
        scraped_from, jobs_company_url, applicant_link, number_applicants,
        scraped_at, updated_at, formatted_posted_date
    ) VALUES %s
    ON CONFLICT (url) DO NOTHING;
    """

    rows = [
        (
            job.get("search_term"),
            job.get("title"),
            job.get("company"),
            job.get("location"),
            job.get("seniority_level"),
            job.get("employment_type"),
            job.get("job_function"),
            job.get("industries"),
            job.get("salary"),
            job.get("is_remote"),
            job.get("date_posted"),
            job.get("url"),
            job.get("description"),
            job.get("scraped_from"),
            job.get("jobs_company_url"),
            job.get("applicant_link"),
            job.get("number_applicants"),
            job.get("scraped_at"),
            job.get("updated_at"),
            job.get("formatted_posted_date"),
        )
        for job in jobs
    ]

    with conn.cursor() as cur:
        execute_values(cur, insert_sql, rows)
    conn.commit()
    print(f"  💾 Saved batch of {len(rows)} jobs (duplicates by URL were skipped).")


# ==============================================================
# SCRAPING FUNCTIONS
# ==============================================================

def parse_number_applicants(text):
    """
    Extracts an integer from strings like '47 applicants' or '200+ applicants'.
    Returns None if it can't parse.
    """
    if not text or text == "N/A":
        return None
    match = re.search(r"\d+", text.replace(",", ""))
    return int(match.group()) if match else None


def extract_job_details(url):
    try:
        response = requests.get(url, headers=headers, timeout=10)
        soup = BeautifulSoup(response.text, "html.parser")

        # ── Title ──────────────────────────────────────────────
        title = (
            soup.find("h1", class_=re.compile(r"top-card-layout__title"))
            or soup.find("h1", class_=re.compile(r"t-24"))
        )
        title_text = title.get_text(strip=True) if title else "N/A"

        # ── Company ────────────────────────────────────────────
        company = (
            soup.find("a", class_=re.compile(r"topcard__org-name-link"))
            or soup.find("a", class_=re.compile(r"company-name"))
            or soup.find("span", class_=re.compile(r"company-name"))
        )
        company_text = company.get_text(strip=True) if company else "N/A"

        # ── Location ───────────────────────────────────────────
        location = soup.find("span", class_=re.compile(r"topcard__flavor--bullet"))
        location_text = location.get_text(strip=True) if location else "N/A"

        # ── Date posted (raw text) ─────────────────────────────
        date_posted = soup.find("span", class_=re.compile(r"posted-time-ago"))
        date_text = date_posted.get_text(strip=True) if date_posted else "N/A"

        # ── Criteria (seniority, contract type, etc.) ──────────
        criteria_items = soup.find_all("li", class_=re.compile(r"description__job-criteria-item"))
        seniority = employment_type = job_function = industries = "N/A"

        for item in criteria_items:
            header_el = item.find("h3")
            value_el  = item.find("span")
            if not header_el or not value_el:
                continue
            key = header_el.get_text(strip=True).lower()
            val = value_el.get_text(strip=True)
            if "seniority" in key or "nivel" in key:
                seniority = val
            elif "employment" in key or "tipo" in key or "jornada" in key:
                employment_type = val
            elif "function" in key or "función" in key:
                job_function = val
            elif "industr" in key:
                industries = val

        # ── Salary ─────────────────────────────────────────────
        salary_el = soup.find("span", class_=re.compile(r"salary|compensation", re.I))
        salary_text = salary_el.get_text(strip=True) if salary_el else "N/A"

        # ── Number of applicants (integer) ─────────────────────
        applicants_el = soup.find("span", class_=re.compile(r"num-applicants|applicant-count", re.I))
        applicants_raw = applicants_el.get_text(strip=True) if applicants_el else None
        number_applicants = parse_number_applicants(applicants_raw)

        # ── Full description ───────────────────────────────────
        description = soup.find("div", class_=re.compile(r"show-more-less-html__markup"))
        description_text = description.get_text(strip=True) if description else "N/A"

        # ── Detect REMOTE ──────────────────────────────────────
        remote_keywords = ["remote", "remoto", "100% remoto", "home office",
                           "work from home", "híbrido", "hybrid"]
        full_text = (description_text + " " + location_text).lower()
        is_remote = "Sí" if any(kw in full_text for kw in remote_keywords) else "No"

        # ── Timestamps ─────────────────────────────────────────
        now = datetime.now(timezone.utc)

        return {
            "title":                 title_text,
            "company":               company_text,
            "location":              location_text,
            "seniority_level":       seniority,
            "employment_type":       employment_type,
            "job_function":          job_function,
            "industries":            industries,
            "salary":                salary_text,
            "is_remote":             is_remote,
            "date_posted":           date_text,
            "url":                   url,
            "description":           description_text,
            "scraped_from":          None,
            "jobs_company_url":      None,
            "applicant_link":        None,
            "number_applicants":     number_applicants,
            "scraped_at":            now,
            "updated_at":            now,
            "formatted_posted_date": None,
        }

    except Exception as e:
        print(f"  ⚠️  Error scraping {url}: {e}")
        return None


# ==============================================================
# OPEN DB CONNECTION ONCE
# ==============================================================

try:
    conn = psycopg2.connect(
        host=os.environ.get("DB_HOST", "localhost"),
        port=os.environ.get("DB_PORT", "5432"),
        dbname=os.environ.get("DB_NAME", "linkedin_jobs"),
        user=os.environ.get("DB_USER", "postgres"),
        password=os.environ.get("DB_PASSWORD", "")
    )
    print("✅ Connected to PostgreSQL.\n")
except psycopg2.OperationalError as e:
    print(f"⚠️  Could not connect to PostgreSQL: {e}")
    print("The script will still run and save to CSV only.\n")
    conn = None


# ==============================================================
# MAIN LOOP — saves after each keyword batch
# ==============================================================

for term in search_terms:
    search_url = base_url + term
    clean_term = term.replace("%20", " ")
    print(f"\n🔍 Searching: {clean_term}")

    try:
        response = requests.get(search_url, headers=headers, timeout=10)
        soup = BeautifulSoup(response.text, "html.parser")
    except Exception as e:
        print(f"  ⚠️  Failed to load search page: {e}")
        continue

    links = soup.find_all("a", class_="base-card__full-link")
    job_urls = [
        link.get("href") for link in links
        if link.get("href") and link.get("href") not in seen_urls
    ]

    print(f"  Found {len(job_urls)} new job links")

    batch = []  # ← one batch per keyword

    for url in job_urls:
        seen_urls.add(url)
        job_data = extract_job_details(url)

        if job_data:
            job_data["search_term"] = clean_term
            batch.append(job_data)
            results.append(job_data)
            print(f"  ✅ {job_data['title']} @ {job_data['company']} | {job_data['date_posted']}")

        # Random pause to avoid getting blocked
        time.sleep(random.uniform(2, 5))

    # ── Save this keyword's batch to PostgreSQL immediately ────
    if conn:
        guardar_en_postgres(conn, batch)

    time.sleep(random.uniform(3, 6))


# ==============================================================
# CLOSE DB CONNECTION
# ==============================================================

if conn:
    conn.close()
    print("\n✅ PostgreSQL connection closed.")
