"""
LinkedIn scraper — two approaches:
1. OAuth user token (stored after one-time auth at /linkedin/auth)
2. Google site:linkedin.com search fallback (no token needed)
Both enrich found leads with Hunter.io for email discovery.
"""
import os, re, logging, requests, time
from urllib.parse import urlparse, urlencode, quote
from .base import fetch, soup, _throttle
from . import hunter_scraper

log = logging.getLogger(__name__)

_AUTH_BASE  = "https://www.linkedin.com/oauth/v2"
_API_BASE   = "https://api.linkedin.com/v2"
_REDIRECT   = os.getenv("LINKEDIN_REDIRECT_URI", "https://bim-leadgen.onrender.com/linkedin/callback")

# In-memory token cache (also persisted to DB via app.py)
_token_cache = {"access_token": None, "expires_at": 0}


# ── Token helpers ─────────────────────────────────────────────────────────────

def get_auth_url() -> str:
    """Build LinkedIn OAuth authorization URL (user opens this in browser)."""
    client_id = os.getenv("LINKEDIN_CLIENT_ID", "")
    params = {
        "response_type": "code",
        "client_id"    : client_id,
        "redirect_uri" : _REDIRECT,
        "scope"        : "openid profile email",
        "state"        : "bim_leadgen",
    }
    return f"{_AUTH_BASE}/authorization?" + urlencode(params)


def exchange_code(code: str) -> dict:
    """Exchange authorization code for access + refresh tokens."""
    r = requests.post(f"{_AUTH_BASE}/accessToken", data={
        "grant_type"  : "authorization_code",
        "code"        : code,
        "redirect_uri": _REDIRECT,
        "client_id"   : os.getenv("LINKEDIN_CLIENT_ID",""),
        "client_secret": os.getenv("LINKEDIN_CLIENT_SECRET",""),
    }, timeout=15)
    if r.status_code == 200:
        data = r.json()
        _token_cache["access_token"] = data.get("access_token","")
        _token_cache["expires_at"]   = time.time() + data.get("expires_in", 3600)
        return data
    log.warning("LinkedIn token exchange failed %s: %s", r.status_code, r.text[:200])
    return {}


def set_token(token: str, expires_in: int = 3600):
    """Store an access token (called from DB restore on startup)."""
    _token_cache["access_token"] = token
    _token_cache["expires_at"]   = time.time() + expires_in


def _get_stored_token() -> str:
    """Return valid stored token, or empty string."""
    if _token_cache["access_token"] and time.time() < _token_cache["expires_at"] - 60:
        return _token_cache["access_token"]
    # Try loading from DB
    try:
        import database as db
        tok = db.get_config("LINKEDIN_ACCESS_TOKEN")
        exp = float(db.get_config("LINKEDIN_TOKEN_EXPIRES", "0") or "0")
        if tok and time.time() < exp - 60:
            _token_cache["access_token"] = tok
            _token_cache["expires_at"]   = exp
            return tok
    except Exception:
        pass
    return ""


def _headers(token: str) -> dict:
    return {
        "Authorization"              : f"Bearer {token}",
        "X-Restli-Protocol-Version"  : "2.0.0",
        "Content-Type"               : "application/json",
    }


# ── Main search (API + Google fallback) ───────────────────────────────────────

def search_people(keywords: str, company: str = "", location: str = "",
                  limit: int = 20) -> list[dict]:
    """
    Search LinkedIn for people/companies matching keywords.
    - Tries LinkedIn API if user token is stored
    - Falls back to Google site:linkedin.com search automatically
    """
    token = _get_stored_token()
    if token:
        log.info("LinkedIn: using stored OAuth token")
        results = _api_search(token, keywords, company, location, limit)
        if results:
            return results
        log.info("LinkedIn API search returned 0 — falling back to Google")

    # Always use Google fallback (free, no token needed)
    return _google_linkedin_search(keywords, company, location, limit)


# ── LinkedIn API search (when token available) ────────────────────────────────

def _api_search(token: str, keywords: str, company: str,
                location: str, limit: int) -> list[dict]:
    leads = []
    try:
        _throttle(2, 3)
        params = {
            "q"      : "people",
            "keywords": f"{keywords} {company}".strip(),
            "count"  : min(limit, 50),
            "start"  : 0,
        }
        r = requests.get(f"{_API_BASE}/search/blended",
                         headers=_headers(token), params=params, timeout=15)
        if r.status_code in (401, 403):
            log.info("LinkedIn API: insufficient permissions — using Google fallback")
            return []
        if r.status_code != 200:
            log.warning("LinkedIn API %s", r.status_code)
            return []

        for elem in r.json().get("elements", []):
            leads += _parse_api_elem(elem, keywords)

    except Exception as e:
        log.warning("LinkedIn API search error: %s", e)
    return leads


def _parse_api_elem(elem: dict, query: str) -> list[dict]:
    first = elem.get("firstName", {})
    last  = elem.get("lastName",  {})
    fn    = next(iter(first.values()), "") if isinstance(first, dict) else str(first)
    ln    = next(iter(last.values()),  "") if isinstance(last,  dict) else str(last)

    headline = elem.get("headline", "")
    if isinstance(headline, dict):
        headline = next(iter(headline.values()), "")

    profile_id = elem.get("id","") or elem.get("publicIdentifier","")
    li_url     = f"https://www.linkedin.com/in/{profile_id}" if profile_id else ""

    return [{
        "name"        : f"{fn} {ln}".strip(),
        "first_name"  : fn,
        "last_name"   : ln,
        "email"       : "",
        "phone"       : "",
        "company"     : "",
        "title"       : str(headline)[:100],
        "linkedin_url": li_url,
        "website"     : "",
        "country"     : "",
        "source"      : "linkedin",
        "source_url"  : li_url,
        "search_query": query,
        "raw_data"    : str(elem)[:300],
    }]


# ── Google site:linkedin.com fallback ─────────────────────────────────────────

def _google_linkedin_search(keywords: str, company: str, location: str,
                             limit: int) -> list[dict]:
    """
    Search Google for LinkedIn profiles/company pages.
    Extracts name, title, company from the LinkedIn snippet.
    Then tries Hunter.io for email enrichment.
    """
    try:
        from googlesearch import search as gsearch
    except ImportError:
        log.warning("googlesearch-python not installed")
        return []

    leads       = []
    seen        = set()
    queries     = _build_li_queries(keywords, company, location)
    per_q_limit = max(5, limit // len(queries))

    log.info("LinkedIn Google fallback: %d queries for '%s'", len(queries), keywords)

    for sq in queries:
        try:
            _throttle(3, 6)
            urls = list(gsearch(sq, num_results=per_q_limit, lang="en"))
            log.info("  '%s' → %d URLs", sq, len(urls))
        except Exception as e:
            log.warning("Google search error: %s", e)
            continue

        for url in urls:
            if url in seen:
                continue
            seen.add(url)

            # LinkedIn company page
            if "/company/" in url:
                lead = _parse_company_url(url, keywords)
                if lead:
                    leads.append(lead)

            # LinkedIn person profile
            elif "/in/" in url:
                lead = _parse_profile_url(url, keywords)
                if lead:
                    leads.append(lead)

            if len(leads) >= limit:
                break

        if len(leads) >= limit:
            break

    # Enrich with Hunter.io
    leads = _enrich_with_hunter(leads)

    log.info("LinkedIn Google fallback '%s' → %d leads", keywords, len(leads))
    return leads


def _build_li_queries(keywords: str, company: str, location: str) -> list[str]:
    q = f"{keywords} {company}".strip()
    queries = [
        f'site:linkedin.com/company "{keywords}" BIM',
        f'site:linkedin.com/in "{keywords}" BIM director manager',
    ]
    if location:
        queries.append(f'site:linkedin.com/in "{keywords}" "{location}"')
        queries.append(f'site:linkedin.com/company "{keywords}" "{location}"')
    return queries[:3]


def _parse_company_url(url: str, query: str) -> dict | None:
    """Extract company info from a LinkedIn company page URL + snippet."""
    # URL like: https://www.linkedin.com/company/bim-company-name
    slug = url.rstrip("/").split("/")[-1]
    if not slug or slug in ("company",):
        return None

    company = slug.replace("-", " ").title()

    # Try to fetch company website from Hunter or the page
    domain = _slug_to_domain(slug)
    hunter_leads = []
    if domain:
        _throttle(1, 2)
        hunter_leads = hunter_scraper.domain_search(domain, limit=3)
        for hl in hunter_leads:
            hl["search_query"] = query
            hl["source"]       = "linkedin"
            hl["source_url"]   = url

    if hunter_leads:
        return hunter_leads[0]  # best Hunter result for this company

    return {
        "name"        : "",
        "first_name"  : "",
        "last_name"   : "",
        "email"       : "",
        "phone"       : "",
        "company"     : company,
        "title"       : "",
        "linkedin_url": url,
        "website"     : f"https://{domain}" if domain else "",
        "country"     : "",
        "source"      : "linkedin",
        "source_url"  : url,
        "search_query": query,
        "raw_data"    : f"LinkedIn company: {url}",
    }


def _parse_profile_url(url: str, query: str) -> dict | None:
    """Extract person info from a LinkedIn profile URL."""
    slug = url.rstrip("/").split("/")[-1]
    if not slug or slug in ("in",):
        return None

    # Slugs like "john-doe-12345abc" → split into name parts
    parts = re.sub(r'[-_]?\w{6,}$', '', slug).replace("-", " ").replace("_", " ").strip()
    name_parts = [p.title() for p in parts.split() if len(p) > 1]
    fn = name_parts[0] if name_parts else ""
    ln = " ".join(name_parts[1:]) if len(name_parts) > 1 else ""

    return {
        "name"        : f"{fn} {ln}".strip() or slug,
        "first_name"  : fn,
        "last_name"   : ln,
        "email"       : "",
        "phone"       : "",
        "company"     : "",
        "title"       : "",
        "linkedin_url": url,
        "website"     : "",
        "country"     : "",
        "source"      : "linkedin",
        "source_url"  : url,
        "search_query": query,
        "raw_data"    : f"LinkedIn profile: {url}",
    }


def _enrich_with_hunter(leads: list[dict]) -> list[dict]:
    """Try to find emails for leads that have name + company but no email."""
    enriched = []
    for lead in leads:
        if lead.get("email"):
            enriched.append(lead)
            continue
        fn      = lead.get("first_name","")
        ln      = lead.get("last_name","")
        website = lead.get("website","")
        company = lead.get("company","")

        domain = ""
        if website:
            domain = urlparse(website).netloc.replace("www.","")
        elif company:
            domain = _company_to_domain(company)

        if domain and fn and ln:
            _throttle(1, 2)
            result = hunter_scraper.email_finder(domain, fn, ln)
            if result and result.get("email"):
                lead["email"]          = result["email"]
                lead["email_type"]     = result.get("email_type","personal")
                lead["email_verified"] = result.get("email_verified", 0)

        enriched.append(lead)
    return enriched


# ── Helpers ───────────────────────────────────────────────────────────────────

def _slug_to_domain(slug: str) -> str:
    """Turn a LinkedIn company slug into a likely domain for Hunter.io."""
    clean = re.sub(r'[-_]', '', slug.lower())[:20]
    return f"{clean}.com" if len(clean) > 2 else ""


def _company_to_domain(company: str) -> str:
    name = re.sub(r'[^a-zA-Z0-9 ]', '', company.lower()).strip()
    for rm in ["inc","ltd","limited","llc","gmbh","pvt","private","plc","corp","co"]:
        name = re.sub(rf'\b{rm}\b', '', name).strip()
    words = name.split()
    if words:
        slug = words[0] if len(words[0]) > 3 else "".join(words[:2])
        return f"{slug}.com"
    return ""
