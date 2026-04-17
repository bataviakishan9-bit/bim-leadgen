"""Hunter.io scraper — domain search + email finder + verify."""
import os, logging, requests
log = logging.getLogger(__name__)

HUNTER_BASE = "https://api.hunter.io/v2"


def _key():
    return os.getenv("HUNTER_API_KEY", "")


def domain_search(domain: str, limit=10) -> list[dict]:
    """Find all known contacts at a domain. Returns list of lead dicts."""
    key = _key()
    if not key or not domain:
        return []
    try:
        r = requests.get(f"{HUNTER_BASE}/domain-search",
                         params={"domain": domain, "limit": limit, "api_key": key},
                         timeout=15)
        if r.status_code != 200:
            log.warning("Hunter domain-search %s → %s", domain, r.status_code)
            return []
        data = r.json().get("data", {})
        org      = data.get("organization","") or ""
        website  = f"https://{domain}"
        country  = data.get("country","") or ""
        results  = []
        for email_obj in data.get("emails", []):
            email      = email_obj.get("value","")
            first      = email_obj.get("first_name","") or ""
            last       = email_obj.get("last_name","") or ""
            title      = email_obj.get("position","") or ""
            confidence = email_obj.get("confidence", 0)
            results.append({
                "name"       : f"{first} {last}".strip(),
                "first_name" : first,
                "last_name"  : last,
                "email"      : email,
                "title"      : title,
                "company"    : org,
                "website"    : website,
                "country"    : country,
                "source"     : "hunter",
                "source_url" : f"https://hunter.io/domain-search/{domain}",
                "email_type" : email_obj.get("type","personal"),
                "email_verified": 1 if confidence >= 80 else 0,
                "raw_data"   : str(email_obj),
            })
        log.info("Hunter domain-search %s → %d contacts", domain, len(results))
        return results
    except Exception as e:
        log.warning("Hunter domain-search error: %s", e)
        return []


def email_finder(domain: str, first: str, last: str) -> dict | None:
    """Find email for a specific person at a domain."""
    key = _key()
    if not key:
        return None
    try:
        r = requests.get(f"{HUNTER_BASE}/email-finder",
                         params={"domain": domain, "first_name": first,
                                 "last_name": last, "api_key": key},
                         timeout=15)
        if r.status_code != 200:
            return None
        data = r.json().get("data", {})
        email = data.get("email","")
        if not email:
            return None
        return {
            "email"      : email,
            "email_type" : data.get("type","personal"),
            "email_verified": 1 if data.get("score",0) >= 80 else 0,
        }
    except Exception as e:
        log.warning("Hunter email-finder error: %s", e)
        return None


def verify_email(email: str) -> dict:
    """Verify an email address. Returns dict with status + score."""
    key = _key()
    if not key:
        return {"status": "unknown", "score": 0}
    try:
        r = requests.get(f"{HUNTER_BASE}/email-verifier",
                         params={"email": email, "api_key": key},
                         timeout=15)
        if r.status_code != 200:
            return {"status": "unknown", "score": 0}
        data = r.json().get("data", {})
        return {
            "status": data.get("status","unknown"),
            "score" : data.get("score",0),
        }
    except Exception as e:
        log.warning("Hunter verify error: %s", e)
        return {"status": "unknown", "score": 0}


def search_by_query(query: str) -> list[dict]:
    """
    Derive domains from a keyword query and run domain-search on each.
    This is the main entry point called by the search job.
    """
    # We can't directly query Hunter by keyword, but we can
    # extract company names / domains from Google results and
    # then enrich them via Hunter. This function is a stub that
    # returns empty — the Google scraper feeds domains to Hunter.
    return []
