import os
import requests
from bs4 import BeautifulSoup
import time
import csv
import re
import random
import psycopg2
from psycopg2.extras import execute_values

headers = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                  "AppleWebKit/537.36 (KHTML, like Gecko) "
                  "Chrome/124.0.0.0 Safari/537.36",
    "Accept-Language": "en-US,en;q=0.9",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}

# ✅ CAMBIO CLAVE: r86400 = 24h (3 días = r259200)
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


# ============================================================
# POSTGRESQL SECTION
# ============================================================
# STUDENT NOTE:
# We NEVER hardcode DB credentials in the script (same rule that
# applies to API keys). Instead, we read them from environment
# variables. Before running this script, set these in your terminal:
#
#   export DB_HOST=localhost
#   export DB_PORT=5432
#   export DB_NAME=linkedin_jobs
#   export DB_USER=postgres
#   export DB_PASSWORD=your_password
#
# Or put them in a .env file and load it with python-dotenv.
# ============================================================

def get_db_connection():
    """
    Opens a connection to PostgreSQL using environment variables.
    STUDENT NOTE: a 'connection' is like opening a phone line to the
    database. You open it, do your queries, then close it.
    """
    return psycopg2.connect(
        host=os.environ.get("DB_HOST", "localhost"),
        port=os.environ.get("DB_PORT", "5432"),
        dbname=os.environ.get("DB_NAME", "linkedin_jobs"),
        user=os.environ.get("DB_USER", "postgres"),
        password=os.environ.get("DB_PASSWORD", "")
    )


def crear_tabla_si_no_existe(conn):
    """
    Creates the jobs table if it doesn't exist yet.
    STUDENT NOTE: 'IF NOT EXISTS' makes this safe to run every time
    the script starts — it won't error out or wipe existing data.

    'url' is UNIQUE because it's the natural identifier for a job
    posting: if we scrape the same job twice, we don't want two rows.
    """
    create_sql = """
    CREATE TABLE IF NOT EXISTS jobs (
        id SERIAL PRIMARY KEY,
        search_term      TEXT,
        title            TEXT,
        company          TEXT,
        location         TEXT,
        seniority_level  TEXT,
        employment_type  TEXT,
        job_function     TEXT,
        industries       TEXT,
        salary           TEXT,
        is_remote        TEXT,
        date_posted      TEXT,
        url              TEXT UNIQUE NOT NULL,
        description      TEXT,
        scraped_at       TIMESTAMP DEFAULT NOW()
    );
    """
    with conn.cursor() as cur:
        cur.execute(create_sql)
    conn.commit()
    print("✅ Table 'jobs' is ready.")


def guardar_en_postgres(conn, jobs):
    """
    Inserts all scraped jobs in a single batch (fast) and skips
    duplicates automatically.

    STUDENT NOTE: 'ON CONFLICT (url) DO NOTHING' means: if a row
    with this url already exists (from a previous scrape run),
    PostgreSQL just skips it instead of raising an error. This is
    how we avoid duplicate job postings piling up every time you
    re-run the scraper.

    'execute_values' sends all rows in one network round-trip
    instead of one INSERT per row — much faster for hundreds of jobs.
    """
    if not jobs:
        print("No jobs to save.")
        return

    insert_sql = """
    INSERT INTO jobs (
        search_term, title, company, location, seniority_level,
        employment_type, job_function, industries, salary,
        is_remote, date_posted, url, description
    ) VALUES %s
    ON CONFLICT (url) DO NOTHING;
    """

    rows = [
        (
            job["search_term"], job["title"], job["company"], job["location"],
            job["seniority_level"], job["employment_type"], job["job_function"],
            job["industries"], job["salary"], job["is_remote"],
            job["date_posted"], job["url"], job["description"]
        )
        for job in jobs
    ]

    with conn.cursor() as cur:
        execute_values(cur, insert_sql, rows)
    conn.commit()
    print(f"✅ Attempted to save {len(rows)} jobs to PostgreSQL (duplicates by URL were skipped).")


# ============================================================
# SCRAPING SECTION (unchanged from your original script)
# ============================================================

def extract_job_details(url):
    try:
        response = requests.get(url, headers=headers, timeout=10)
        soup = BeautifulSoup(response.text, "html.parser")

        # ── Título ─────────────────────────────────────────────
        title = (
            soup.find("h1", class_=re.compile(r"top-card-layout__title"))
            or soup.find("h1", class_=re.compile(r"t-24"))
        )
        title_text = title.get_text(strip=True) if title else "N/A"

        # ── Empresa ────────────────────────────────────────────
        company = (
            soup.find("a", class_=re.compile(r"topcard__org-name-link"))
            or soup.find("a", class_=re.compile(r"company-name"))
            or soup.find("span", class_=re.compile(r"company-name"))
        )
        company_text = company.get_text(strip=True) if company else "N/A"

        # ── Ubicación ──────────────────────────────────────────
        location = soup.find("span", class_=re.compile(r"topcard__flavor--bullet"))
        location_text = location.get_text(strip=True) if location else "N/A"

        # ── Fecha de publicación ───────────────────────────────
        date_posted = soup.find("span", class_=re.compile(r"posted-time-ago"))
        date_text = date_posted.get_text(strip=True) if date_posted else "N/A"

        # ── Criterios (nivel, tipo contrato, etc.) ─────────────
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

        # ── Salario ────────────────────────────────────────────
        salary_el = soup.find("span", class_=re.compile(r"salary|compensation", re.I))
        salary_text = salary_el.get_text(strip=True) if salary_el else "N/A"

        # ── Descripción completa ───────────────────────────────
        description = soup.find("div", class_=re.compile(r"show-more-less-html__markup"))
        description_text = description.get_text(strip=True) if description else "N/A"

        # ── Detectar si es REMOTO ──────────────────────────────
        remote_keywords = ["remote", "remoto", "100% remoto", "home office",
                           "work from home", "híbrido", "hybrid"]
        full_text = (description_text + " " + location_text).lower()
        is_remote = "Sí" if any(kw in full_text for kw in remote_keywords) else "No"

        return {
            "title":           title_text,
            "company":         company_text,
            "location":        location_text,
            "seniority_level": seniority,
            "employment_type": employment_type,
            "job_function":    job_function,
            "industries":      industries,
            "salary":          salary_text,
            "is_remote":       is_remote,
            "date_posted":     date_text,
            "url":             url,
            "description":     description_text,
        }

    except Exception as e:
        print(f"  ⚠️  Error scraping {url}: {e}")
        return None


# ── MAIN LOOP ─────────────────────────────────────────────────
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

    for url in job_urls:
        seen_urls.add(url)
        job_data = extract_job_details(url)

        if job_data:
            job_data["search_term"] = clean_term
            results.append(job_data)
            print(f"  ✅ {job_data['title']} @ {job_data['company']} | {job_data['date_posted']}")

        # ✅ Pausa aleatoria para evitar bloqueos
        time.sleep(random.uniform(2, 5))

    time.sleep(random.uniform(3, 6))


# ── GUARDAR CSV (se mantiene como respaldo) ────────────────────
fieldnames = [
    "search_term", "title", "company", "location",
    "seniority_level", "employment_type", "job_function",
    "industries", "salary", "is_remote", "date_posted",
    "url", "description"
]

with open("linkedin_jobs.csv", "w", newline="", encoding="utf-8") as f:
    writer = csv.DictWriter(f, fieldnames=fieldnames)
    writer.writeheader()
    writer.writerows(results)

print(f"\n✅ Saved {len(results)} jobs to linkedin_jobs.csv")

# ── GUARDAR EN POSTGRESQL ───────────────────────────────────────
try:
    conn = get_db_connection()
    crear_tabla_si_no_existe(conn)
    guardar_en_postgres(conn, results)
    conn.close()
except psycopg2.OperationalError as e:
    print(f"⚠️  Could not connect to PostgreSQL: {e}")
    print("Your CSV file was still saved successfully above.")