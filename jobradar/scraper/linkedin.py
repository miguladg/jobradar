"""
scraper/linkedin.py — fetches LinkedIn search pages and job detail pages.

Two kinds of request:
  1. SEARCH page  → a list of job cards (links + posting date)
  2. DETAIL page  → the full posting for one job

Everything tunable (delays, retries, URLs) lives in config.py.
"""

import logging
import random
import re
import time
from datetime import date, timedelta
from urllib.parse import quote, urlsplit, urlunsplit

import requests
from bs4 import BeautifulSoup

from .. import config
from ..trace import step, detail, warn

log = logging.getLogger(__name__)


REMOTE_KEYWORDS = ["remote", "remoto", "home office", "work from home",
                   "teletrabajo", "híbrido", "hybrid"]


# ── Small pure helpers (easy to test, no network) ──────────────

def normalize_url(url: str) -> str:
    """
    Drop the query string and fragment.

    WHY: LinkedIn appends ?refId=...&trackingId=... and those values change
    on every request. Without this, the same job gets a different URL each
    run and the UNIQUE (url) dedupe never fires.
    """
    parts = urlsplit(url.strip())
    return urlunsplit((parts.scheme, parts.netloc, parts.path, "", ""))


def parse_number_applicants(text):
    """'47 applicants' → 47, '200+ applicants' → 200, junk → None."""
    if not text:
        return None
    match = re.search(r"\d+", text.replace(",", "").replace(".", ""))
    return int(match.group()) if match else None


# English and Spanish units LinkedIn uses in "posted X ago" text.
_UNIT_DAYS = [
    (r"minut|hour|hora|segund|second|just now|justo ahora", 0),
    (r"day|día|dia", 1),
    (r"week|semana", 7),
    (r"month|mes", 30),
    (r"year|año", 365),
]


def parse_relative_date(text, today=None):
    """
    '3 days ago' / 'hace 3 días' / '1 week ago' → a date.
    Returns None when the text can't be understood.
    """
    if not text:
        return None
    today = today or date.today()
    lowered = text.lower()
    number = re.search(r"\d+", lowered)
    amount = int(number.group()) if number else 1   # "a day ago", "hace un día"

    for pattern, days_per_unit in _UNIT_DAYS:
        if re.search(pattern, lowered):
            return today - timedelta(days=amount * days_per_unit)
    return None


def _text(soup, *finders):
    """First matching element's text, or None. Each finder is (tag, class_regex)."""
    for tag, pattern in finders:
        el = soup.find(tag, class_=re.compile(pattern, re.I))
        if el:
            return " ".join(el.get_text(" ", strip=True).split())
    return None


# ── The scraper ────────────────────────────────────────────────

class LinkedInScraper:
    def __init__(self, days_back=None, known_urls=None):
        self.days_back = days_back or config.DAYS_BACK
        self.known_urls = {normalize_url(u) for u in (known_urls or ())}
        self.seen_urls = set()

        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": config.USER_AGENT,
            "Accept-Language": "en-US,en;q=0.9",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        })

        self.stats = {"cards": 0, "skipped_known": 0, "skipped_old": 0,
                      "fetched": 0, "failed": 0}

    # ── HTTP ───────────────────────────────────────────────────

    def _get(self, url, params=None):
        """GET with retries and exponential backoff. Returns HTML or None."""
        for attempt in range(1, config.MAX_RETRIES + 1):
            try:
                resp = self.session.get(url, params=params,
                                        timeout=config.REQUEST_TIMEOUT)
                # 429 = rate limited; 5xx = LinkedIn hiccup. Both are worth a retry.
                if resp.status_code == 429 or resp.status_code >= 500:
                    raise requests.HTTPError(f"HTTP {resp.status_code}")
                if resp.status_code != 200:
                    log.debug("GET %s → HTTP %s (not retrying)", url, resp.status_code)
                    return None
                return resp.text
            except requests.RequestException as exc:
                if attempt == config.MAX_RETRIES:
                    log.warning("Giving up on %s: %s", url, exc)
                    return None
                wait = config.BACKOFF_FACTOR ** attempt
                warn(f"{exc}; retrying in {wait:.0f}s "
                     f"(attempt {attempt}/{config.MAX_RETRIES})", level=2)
                time.sleep(wait)
        return None

    @staticmethod
    def pause(delay_range):
        time.sleep(random.uniform(*delay_range))

    # ── Search pages ───────────────────────────────────────────

    def _search_pages(self, term):
        """Yields the HTML of each search results page for one term."""
        if not config.USE_GUEST_API:
            # The proven URL returns one page (~25 cards) and has no offset.
            yield self._get(config.SEARCH_URL.format(
                seconds=self.days_back * 24 * 60 * 60,
                work_types=quote(config.WORK_TYPES),
                location=quote(config.LOCATION),
                keywords=quote(term),
            ))
            return

        for page in range(config.MAX_PAGES_PER_TERM):
            if page:
                self.pause(config.DELAY_BETWEEN_PAGES)
            html = self._get(config.GUEST_API_URL, params={
                "keywords": term,
                "location": config.LOCATION,
                "f_TPR": f"r{self.days_back * 24 * 60 * 60}",
                "f_WT": config.WORK_TYPES,
                "start": page * config.RESULTS_PER_PAGE,
            })
            if not html or "base-card" not in html:
                return          # ran out of results
            yield html

    def _parse_cards(self, html):
        """Search page HTML → [(url, posted_date_or_None), ...]"""
        soup = BeautifulSoup(html, "html.parser")
        cards = []
        for link in soup.find_all("a", class_=re.compile(r"base-card__full-link")):
            href = link.get("href")
            if not href:
                continue
            posted = None
            card = link.find_parent(class_=re.compile(r"base-card|job-search-card"))
            time_el = card.find("time") if card else None
            if time_el and time_el.get("datetime"):
                try:
                    posted = date.fromisoformat(time_el["datetime"][:10])
                except ValueError:
                    pass
            cards.append((normalize_url(href), posted))
        return cards

    # ── Detail page ────────────────────────────────────────────

    def _parse_detail(self, html, url):
        """Detail page HTML → job dict. LinkedIn class names change over
        time; when scraping stops returning fields, update these regexes."""
        soup = BeautifulSoup(html, "html.parser")

        criteria = {"seniority_level": None, "employment_type": None,
                    "job_function": None, "industries": None}
        for item in soup.find_all("li", class_=re.compile(r"description__job-criteria-item")):
            header_el, value_el = item.find("h3"), item.find("span")
            if not header_el or not value_el:
                continue
            key = header_el.get_text(strip=True).lower()
            val = value_el.get_text(strip=True)
            if "seniority" in key or "nivel" in key:
                criteria["seniority_level"] = val
            elif "employment" in key or "tipo" in key or "jornada" in key:
                criteria["employment_type"] = val
            elif "function" in key or "función" in key:
                criteria["job_function"] = val
            elif "industr" in key or "sector" in key:
                criteria["industries"] = val

        location = _text(soup, ("span", r"topcard__flavor--bullet"))
        date_posted = _text(soup, ("span", r"posted-time-ago"))
        description = _text(soup, ("div", r"show-more-less-html__markup"))
        applicants = _text(soup, ("span", r"num-applicants"),
                                 ("figcaption", r"num-applicants"))

        haystack = f"{description or ''} {location or ''}".lower()
        company_link = soup.find("a", class_=re.compile(r"topcard__org-name-link"))

        return {
            "url": url,
            "title": _text(soup, ("h1", r"top-card-layout__title"), ("h1", r"t-24")),
            "company": company_link.get_text(strip=True) if company_link
                       else _text(soup, ("span", r"company-name")),
            "jobs_company_url": normalize_url(company_link["href"])
                                if company_link and company_link.get("href") else None,
            "location": location,
            "date_posted": date_posted,
            "formatted_posted_date": parse_relative_date(date_posted),
            "salary": _text(soup, ("div", r"salary|compensation"),
                                  ("span", r"salary|compensation")),
            "number_applicants": parse_number_applicants(applicants),
            "is_remote": "Sí" if any(k in haystack for k in REMOTE_KEYWORDS) else "No",
            "description": description,
            "scraped_from": "linkedin",
            **criteria,
        }

    # ── Main loop ──────────────────────────────────────────────

    def _is_too_old(self, posted):
        return posted is not None and posted < date.today() - timedelta(days=self.days_back)

    def scrape_term(self, term):
        step(f"searching '{term}' (last {self.days_back} day(s), {config.LOCATION})")
        jobs = []

        for html in self._search_pages(term):
            if not html:
                warn("search page failed to load", level=1)
                continue

            cards = self._parse_cards(html)
            self.stats["cards"] += len(cards)
            detail(f"{len(cards)} job cards on this page")

            for url, card_date in cards:
                if url in self.seen_urls:
                    continue            # already handled under another term
                self.seen_urls.add(url)

                if url in self.known_urls:
                    self.stats["skipped_known"] += 1
                    continue
                if self._is_too_old(card_date):
                    self.stats["skipped_old"] += 1
                    continue

                self.pause(config.DELAY_BETWEEN_JOBS)
                page = self._get(url)
                if not page:
                    self.stats["failed"] += 1
                    continue

                job = self._parse_detail(page, url)
                # The card's <time datetime> is exact; the detail page's
                # "2 days ago" is approximate. Prefer the exact one.
                job["formatted_posted_date"] = card_date or job["formatted_posted_date"]
                if self._is_too_old(job["formatted_posted_date"]):
                    self.stats["skipped_old"] += 1
                    continue

                job["search_term"] = term
                jobs.append(job)
                self.stats["fetched"] += 1
                detail(f"{job['title']} @ {job['company']} "
                       f"({job['formatted_posted_date'] or job['date_posted']})", level=1)

        return jobs

    def run(self, terms):
        all_jobs = []
        for i, term in enumerate(terms):
            if i:
                self.pause(config.DELAY_BETWEEN_TERMS)
            all_jobs.extend(self.scrape_term(term))
        return all_jobs

    def log_summary(self):
        s = self.stats
        log.info("Scraper: %d cards seen, %d fetched, %d already known, "
                 "%d too old, %d failed",
                 s["cards"], s["fetched"], s["skipped_known"],
                 s["skipped_old"], s["failed"])
