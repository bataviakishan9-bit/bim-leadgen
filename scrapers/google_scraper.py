"""
Google Search scraper.
Uses googlesearch-python (free) to get URLs, then:
  1. Scrapes each page for emails/phones/company info
  2. Passes found domains to Hunter.io for deeper contact data
"""
import re, time, random, logging
from urllib.parse import urlparse
from .base import fetch, extract_emails, extract_phones, parse_name, soup, _throttle
from . import hunter_scraper

log = logging.getLogger(__name__)

# Sub-query templates — each user query becomes multiple targeted searches
QUERY_TEMPLATES = [
    '{q} company contact email',
    '"{q}" firm outsourcing BIM',
    '"{q}" AEC company website',
    '{q} architecture engineering contact',
    '{q} drone survey company email',
]


def _build_queries(user_query: str) -> list[str]:
    """Expand one user query into multiple targeted search strings."""
    q = user_query.strip()
    queries = []
    # Primary
    queries.append(f'"{q}" company contact')
    # If query has a country keyword, add site: variant
    countries = {"germany":"de","uk":"uk","australia":"au","india":"in",
                 "singapore":"sg","netherlands":"nl","usa":"com","canada":"ca"}
    for country, tld in countries.items():
        if country in q.lower():
            queries.append(f'"{q.replace(country,"").strip()}" site:{tld} BIM firm contact')
            break
    queries.append(f'{q} BIM outsourcing company email')
    queries.append(f'{q} AEC firm contact details')
    return queries[:4]  # max 4 sub-queries per user query


def search(user_query: str, max_results: int = 20) -> list[dict]:
    """
    Search Google for leads matching user_query.
    Returns list of raw lead dicts (partially enriched).
    """
    leads = []
    seen_domains = set()

    try:
        from googlesearch import search as gsearch
    except ImportError:
        log.warning("googlesearch-python not installed. pip install googlesearch-python")
        return []

    sub_queries = _build_queries(user_query)
    log.info("Google search: %d sub-queries for '%s'", len(sub_queries), user_query)

    for sq in sub_queries:
        try:
            _throttle(2, 5)
            urls = list(gsearch(sq, num_results=10, lang="en"))
            log.info("  sub-query '%s' → %d URLs", sq, len(urls))
        except Exception as e:
            log.warning("Google search error for '%s': %s", sq, e)
            continue

        for url in urls:
            try:
                domain = urlparse(url).netloc.replace("www.","")
                if not domain or domain in seen_domains:
                    continue
                # Skip irrelevant domains
                if any(skip in domain for skip in [
                    "wikipedia","linkedin","facebook","twitter","youtube",
                    "instagram","pinterest","reddit","quora","amazon",
                    "glassdoor","indeed","naukri","monster",
                ]):
                    continue
                seen_domains.add(domain)

                # First try Hunter.io domain-search (fastest, best quality)
                hunter_leads = hunter_scraper.domain_search(domain, limit=5)
                if hunter_leads:
                    for hl in hunter_leads:
                        hl["search_query"] = user_query
                        hl["source_url"]   = url
                    leads.extend(hunter_leads)
                    log.info("  Hunter %s → %d contacts", domain, len(hunter_leads))
                else:
                    # Fallback: scrape the page directly
                    page_leads = _scrape_page(url, domain, user_query)
                    leads.extend(page_leads)

                _throttle(1, 2)
            except Exception as e:
                log.debug("Error processing URL %s: %s", url, e)
                continue

    log.info("Google search '%s' total raw leads: %d", user_query, len(leads))
    return leads


def _scrape_page(url: str, domain: str, query: str) -> list[dict]:
    """Scrape a single web page for contact information."""
    leads = []
    r = fetch(url, timeout=12)
    if not r:
        return []

    s   = soup(r.text)
    txt = s.get_text(" ", strip=True)

    # Company name from title / h1
    title_tag = s.find("title")
    h1_tag    = s.find("h1")
    company   = ""
    if title_tag:
        company = title_tag.get_text().split("|")[0].split("-")[0].strip()
    if not company and h1_tag:
        company = h1_tag.get_text().strip()
    if not company:
        company = domain.split(".")[0].title()

    emails = extract_emails(txt)
    phones = extract_phones(txt)

    # Try to find contact page for better email coverage
    contact_urls = _find_contact_page(s, url)
    for curl in contact_urls[:2]:
        cr = fetch(curl, timeout=10)
        if cr:
            extra_emails = extract_emails(cr.text)
            emails.extend(e for e in extra_emails if e not in emails)

    phone = phones[0] if phones else ""

    # Try to infer country from domain
    country = _infer_country(domain, txt)

    if emails:
        for email in emails[:3]:
            first, last = _guess_name_from_email(email)
            leads.append({
                "name"       : f"{first} {last}".strip() or company,
                "first_name" : first,
                "last_name"  : last,
                "email"      : email,
                "phone"      : phone,
                "company"    : company,
                "website"    : f"https://{domain}",
                "country"    : country,
                "source"     : "google",
                "source_url" : url,
                "search_query": query,
                "raw_data"   : f"scraped from {url}",
            })
    else:
        # No email but we found the company — partial lead
        leads.append({
            "name"       : company,
            "first_name" : "",
            "last_name"  : "",
            "email"      : "",
            "phone"      : phone,
            "company"    : company,
            "website"    : f"https://{domain}",
            "country"    : country,
            "source"     : "google",
            "source_url" : url,
            "search_query": query,
            "raw_data"   : f"scraped from {url} (no email found)",
        })

    return leads


def _find_contact_page(s, base_url: str) -> list[str]:
    """Find contact/about/team page links."""
    links = []
    base = "/".join(base_url.split("/")[:3])
    for a in s.find_all("a", href=True):
        href = a["href"].lower()
        if any(kw in href for kw in ["contact","about","team","people","staff"]):
            full = a["href"] if a["href"].startswith("http") else base + a["href"]
            if full not in links:
                links.append(full)
    return links[:3]


def _infer_country(domain: str, text: str) -> str:
    tld_map = {
        ".de":"Germany",".at":"Austria",".ch":"Switzerland",
        ".co.uk":"United Kingdom",".uk":"United Kingdom",
        ".com.au":"Australia",".au":"Australia",
        ".sg":"Singapore",".nz":"New Zealand",
        ".nl":"Netherlands",".be":"Belgium",
        ".ae":"UAE",".qa":"Qatar",".sa":"Saudi Arabia",
        ".ca":"Canada",".fr":"France",
        ".in":"India",".se":"Sweden",".no":"Norway",
        ".dk":"Denmark",".fi":"Finland",".it":"Italy",
        ".es":"Spain",".pl":"Poland",
    }
    for tld, country in tld_map.items():
        if domain.endswith(tld):
            return country
    return ""


def _guess_name_from_email(email: str) -> tuple[str, str]:
    """
    Try to extract name from email like john.doe@company.com → John, Doe
    """
    prefix = email.split("@")[0]
    # Remove numbers
    prefix = re.sub(r'\d+', '', prefix)
    # Split on common separators
    parts = re.split(r'[.\-_]', prefix)
    parts = [p.title() for p in parts if len(p) > 1]
    if len(parts) >= 2:
        return parts[0], " ".join(parts[1:])
    if len(parts) == 1:
        return parts[0], ""
    return "", ""
