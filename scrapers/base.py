"""Base scraper — shared helpers for all scrapers."""
import re, time, random, logging
import requests
from bs4 import BeautifulSoup

log = logging.getLogger(__name__)

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}

EMAIL_RE   = re.compile(r'[\w.+\-]+@[\w\-]+\.[\w.]{2,}')
PHONE_RE   = re.compile(r'(?:\+?\d[\d\s\-().]{7,}\d)')

FREE_DOMAINS = {
    "gmail.com","yahoo.com","hotmail.com","outlook.com","live.com",
    "rediffmail.com","aol.com","icloud.com","protonmail.com",
}


def fetch(url, timeout=15, retries=2) -> requests.Response | None:
    for attempt in range(retries + 1):
        try:
            r = requests.get(url, headers=HEADERS, timeout=timeout)
            if r.status_code == 200:
                return r
            log.debug("fetch %s → %s", url, r.status_code)
        except Exception as e:
            log.debug("fetch error %s: %s", url, e)
        if attempt < retries:
            time.sleep(random.uniform(1.5, 3.5))
    return None


def extract_emails(text: str) -> list[str]:
    """Extract all email addresses from text, filtering obvious junk."""
    emails = []
    for m in EMAIL_RE.finditer(text):
        e = m.group(0).lower().strip(".,;:")
        if "@" in e and "." in e.split("@")[1] and e not in emails:
            if not e.endswith((".png",".jpg",".gif",".svg",".css",".js")):
                emails.append(e)
    return emails


def extract_phones(text: str) -> list[str]:
    phones = []
    for m in PHONE_RE.finditer(text):
        p = m.group(0).strip()
        if len(re.sub(r'\D','',p)) >= 7 and p not in phones:
            phones.append(p)
    return phones


def parse_name(full_name: str) -> tuple[str, str]:
    """Split 'John Doe' → ('John', 'Doe')"""
    parts = full_name.strip().split()
    if not parts:
        return "", ""
    if len(parts) == 1:
        return parts[0], ""
    return parts[0], " ".join(parts[1:])


def classify_email(email: str) -> str:
    """Return 'personal', 'generic', 'free', or 'unknown'."""
    if not email or "@" not in email:
        return "unknown"
    prefix, domain = email.lower().split("@", 1)
    if domain in FREE_DOMAINS:
        return "free"
    generic = {"info","contact","hello","admin","support","office","mail","enquiries",
               "query","sales","noreply","no-reply","team","help","business","general"}
    if prefix in generic:
        return "generic"
    return "personal"


def soup(html: str) -> BeautifulSoup:
    return BeautifulSoup(html, "lxml")


def _throttle(min_s=1.0, max_s=3.0):
    time.sleep(random.uniform(min_s, max_s))
