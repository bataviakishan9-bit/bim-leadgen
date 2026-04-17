"""
Industry directory scrapers:
- BuildingSMART member directory
- ENR Top firms
- RICS firm directory
- Google Scholar (BIM/AEC research authors)
"""
import re, time, logging
from urllib.parse import urljoin
from .base import fetch, extract_emails, extract_phones, soup, _throttle
from . import hunter_scraper

log = logging.getLogger(__name__)


# ── BuildingSMART ─────────────────────────────────────────────────────────────

def scrape_buildingsmart(query: str = "") -> list[dict]:
    """Scrape BuildingSMART member directory."""
    url = "https://www.buildingsmart.org/membership/members/"
    log.info("Scraping BuildingSMART members...")
    r = fetch(url, timeout=20)
    if not r:
        log.warning("BuildingSMART fetch failed")
        return []

    s = soup(r.text)
    leads = []

    # Look for member company cards/links
    for elem in s.find_all(["div","article","li"], class_=re.compile(r'member|company|org', re.I)):
        text = elem.get_text(" ", strip=True)
        if not text or len(text) < 3:
            continue

        # Extract company name
        name_tag = elem.find(["h2","h3","h4","strong","a"])
        company  = name_tag.get_text().strip() if name_tag else text[:60]

        # Get website link
        website = ""
        for a in elem.find_all("a", href=True):
            href = a["href"]
            if href.startswith("http") and "buildingsmart" not in href:
                website = href; break

        # Country from text
        country = _extract_country(text)

        if company and len(company) > 2:
            # Try Hunter.io for contacts
            if website:
                from urllib.parse import urlparse
                domain = urlparse(website).netloc.replace("www.","")
                if domain:
                    _throttle(1, 2)
                    hunter_leads = hunter_scraper.domain_search(domain, limit=3)
                    for hl in hunter_leads:
                        hl["search_query"] = f"buildingsmart member {query}"
                        hl["source"]       = "buildingsmart"
                        leads.extend([hl])
                    if not hunter_leads:
                        leads.append({
                            "company"     : company,
                            "website"     : website,
                            "country"     : country,
                            "source"      : "buildingsmart",
                            "source_url"  : url,
                            "search_query": f"buildingsmart {query}",
                            "name":"","first_name":"","last_name":"",
                            "email":"","phone":"",
                        })
            else:
                leads.append({
                    "company"     : company,
                    "country"     : country,
                    "source"      : "buildingsmart",
                    "source_url"  : url,
                    "search_query": f"buildingsmart {query}",
                    "name":"","first_name":"","last_name":"",
                    "email":"","phone":"","website":"",
                })

    log.info("BuildingSMART → %d leads", len(leads))
    return leads


# ── ENR Top Firms ─────────────────────────────────────────────────────────────

ENR_URLS = [
    "https://www.enr.com/toplists/2024-Top-400-Contractors-1",
    "https://www.enr.com/toplists/2024-Top-500-Design-Firms-1",
]

def scrape_enr(query: str = "") -> list[dict]:
    """Scrape ENR top contractors/design firms list."""
    leads = []
    for url in ENR_URLS:
        log.info("Scraping ENR: %s", url)
        _throttle(2, 4)
        r = fetch(url, timeout=20)
        if not r:
            continue
        s = soup(r.text)
        for row in s.find_all(["tr","div","li"], limit=50):
            text = row.get_text(" ", strip=True)
            # ENR rows typically contain rank + company name
            if len(text) < 3 or len(text) > 200:
                continue
            # Find company links
            for a in row.find_all("a", href=True):
                if "/companies/" in a["href"] or "/firm/" in a["href"]:
                    company = a.get_text().strip()
                    if company and len(company) > 2:
                        leads.append({
                            "company"     : company,
                            "source"      : "enr",
                            "source_url"  : url,
                            "search_query": f"enr top firms {query}",
                            "name":"","first_name":"","last_name":"",
                            "email":"","phone":"","website":"","country":"",
                        })
    log.info("ENR → %d leads", len(leads))
    return leads


# ── RICS Firms ────────────────────────────────────────────────────────────────

def scrape_rics(query: str = "", country: str = "") -> list[dict]:
    """Scrape RICS regulated firms directory."""
    base_url = "https://www.ricsfirms.com/"
    params   = f"?keywords={query.replace(' ','+')}"
    if country:
        params += f"&country={country}"
    url = base_url + params

    log.info("Scraping RICS: %s", url)
    _throttle(2, 4)
    r = fetch(url, timeout=20)
    if not r:
        return []

    s     = soup(r.text)
    leads = []

    for card in s.find_all(["div","article"], class_=re.compile(r'firm|result|company|card', re.I)):
        name_tag = card.find(["h2","h3","h4","a"])
        company  = name_tag.get_text().strip() if name_tag else ""
        if not company or len(company) < 3:
            continue

        # Website
        website = ""
        for a in card.find_all("a", href=True):
            h = a["href"]
            if h.startswith("http") and "rics" not in h:
                website = h; break

        country_found = _extract_country(card.get_text())

        if website:
            from urllib.parse import urlparse
            domain = urlparse(website).netloc.replace("www.","")
            if domain:
                _throttle(1, 2)
                hunter_leads = hunter_scraper.domain_search(domain, limit=3)
                for hl in hunter_leads:
                    hl["search_query"] = f"rics {query}"
                    hl["source"]       = "rics"
                    leads.append(hl)
                    continue

        leads.append({
            "company"     : company,
            "website"     : website,
            "country"     : country_found or country,
            "source"      : "rics",
            "source_url"  : url,
            "search_query": f"rics {query}",
            "name":"","first_name":"","last_name":"","email":"","phone":"",
        })

    log.info("RICS → %d leads", len(leads))
    return leads


# ── Google Scholar ────────────────────────────────────────────────────────────

def scrape_scholar(query: str = "") -> list[dict]:
    """
    Search Google Scholar for BIM/AEC paper authors.
    Extract author names + affiliations → use Hunter for email.
    Rate limited — 1 request per 8 seconds.
    """
    import urllib.parse
    sq    = urllib.parse.quote(query or "scan to bim contractor")
    url   = f"https://scholar.google.com/scholar?q={sq}&hl=en"

    log.info("Scraping Scholar: %s", url)
    time.sleep(8)  # Scholar blocks if too fast
    r = fetch(url, timeout=20)
    if not r:
        return []

    s     = soup(r.text)
    leads = []

    for result in s.find_all("div", class_="gs_r"):
        # Author + affiliation
        auth_div = result.find("div", class_="gs_a")
        if not auth_div:
            continue
        auth_text = auth_div.get_text()

        # Extract authors (before the year)
        parts    = auth_text.split(" - ")
        authors  = parts[0].split(",") if parts else []
        affil    = parts[1].strip() if len(parts) > 1 else ""
        year_str = parts[-1].strip() if len(parts) > 2 else ""

        for author in authors[:2]:  # max 2 authors per paper
            author = author.strip()
            if len(author) < 3:
                continue
            fn, ln = _split_scholar_name(author)
            # Try Hunter if we know the affiliation domain
            domain = _affil_to_domain(affil)
            email  = ""
            if domain and fn and ln:
                result_h = hunter_scraper.email_finder(domain, fn, ln)
                if result_h:
                    email = result_h.get("email","")

            leads.append({
                "name"        : f"{fn} {ln}".strip() or author,
                "first_name"  : fn,
                "last_name"   : ln,
                "email"       : email,
                "company"     : affil[:100] if affil else "",
                "source"      : "scholar",
                "source_url"  : url,
                "search_query": query,
                "raw_data"    : auth_text[:200],
                "phone":"","website":"","country":"",
            })
        time.sleep(8)  # be polite to Scholar

    log.info("Scholar '%s' → %d leads", query, len(leads))
    return leads


# ── Helpers ───────────────────────────────────────────────────────────────────

COUNTRY_KEYWORDS = {
    "germany":"Germany","german":"Germany","deutschland":"Germany",
    "uk":"United Kingdom","united kingdom":"United Kingdom","england":"United Kingdom",
    "india":"India","australia":"Australia","singapore":"Singapore",
    "uae":"UAE","dubai":"UAE","netherlands":"Netherlands","usa":"USA",
    "canada":"Canada","france":"France","switzerland":"Switzerland",
}

def _extract_country(text: str) -> str:
    tl = text.lower()
    for kw, country in COUNTRY_KEYWORDS.items():
        if kw in tl:
            return country
    return ""

def _split_scholar_name(name: str) -> tuple[str, str]:
    parts = name.strip().split()
    if len(parts) >= 2:
        return parts[0], " ".join(parts[1:])
    return name, ""

def _affil_to_domain(affil: str) -> str:
    """Try to guess a domain from an affiliation string."""
    import re
    # Look for explicit URLs
    m = re.search(r'([\w-]+\.[\w.]{2,5})', affil)
    if m:
        return m.group(1)
    # Convert university name to likely domain
    words = re.sub(r'[^a-zA-Z ]', '', affil.lower()).split()
    if "university" in words or "institute" in words:
        return ""  # skip academic
    if words:
        return f"{words[0]}.com"
    return ""
