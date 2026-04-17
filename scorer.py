"""
BIM Lead Generator — Smart Scoring Engine
score_lead(lead: dict) -> (int score, dict breakdown)
"""

# ── Keyword base scores ───────────────────────────────────────────────────────
KEYWORD_SCORES = [
    ("scan to bim",          40),
    ("scan-to-bim",          40),
    ("point cloud",          38),
    ("point-cloud",          38),
    ("reality capture",      36),
    ("lidar",                35),
    ("drone survey",         35),
    ("uav survey",           35),
    ("drone mapping",        33),
    ("aerial survey",        33),
    ("bim coordination",     32),
    ("bim coordinator",      32),
    ("vdc director",         32),
    ("vdc manager",          32),
    ("virtual design",       32),
    ("bim manager",          30),
    ("bim lead",             30),
    ("bim specialist",       30),
    ("revit",                28),
    ("mep coordination",     28),
    ("mep bim",              28),
    ("digital twin",         27),
    ("4d scheduling",        26),
    ("5d bim",               26),
    ("clash detection",      25),
    ("clash coordination",   25),
    ("infrastructure bim",   25),
    ("as-built",             24),
    ("as built",             24),
    ("facility management",  16),  # only medium unless combined with bim
    ("bim",                  18),
    ("construction management", 12),
    ("architecture",         10),
    ("architect",            10),
    ("engineering",           8),
    ("surveying",            15),
    ("geospatial",           20),
    ("photogrammetry",       30),
    ("3d laser",             32),
    ("terrestrial scan",     38),
    ("gis",                  14),
    ("structural bim",       28),
    ("revit mep",            30),
    ("openBIM",              26),
    ("ifc",                  24),
    ("navisworks",           26),
]

# ── Country bonuses ───────────────────────────────────────────────────────────
COUNTRY_BONUSES = {
    # DACH
    "germany": 12, "deutschland": 12, "austria": 12, "switzerland": 12,
    "österreich": 12, "schweiz": 12,
    # UK/Ireland
    "united kingdom": 10, "uk": 10, "england": 10, "ireland": 10, "wales": 10,
    "scotland": 10,
    # Australia/NZ
    "australia": 10, "new zealand": 10,
    # Singapore/HK
    "singapore": 9, "hong kong": 9,
    # Benelux
    "netherlands": 9, "belgium": 9, "holland": 9,
    # Middle East
    "uae": 8, "united arab emirates": 8, "qatar": 8, "saudi arabia": 8,
    "kuwait": 7, "bahrain": 7,
    # North America
    "usa": 8, "united states": 8, "canada": 8,
    # France/Nordics
    "france": 7, "sweden": 7, "norway": 7, "denmark": 7, "finland": 7,
    # India
    "india": 5,
    # Other Europe
    "spain": 6, "italy": 6, "poland": 6, "czech republic": 6, "portugal": 6,
    "romania": 5, "hungary": 5,
    # Rest of world
}
DEFAULT_COUNTRY_BONUS = 3

# Special combo bonuses
COMBO_BONUSES = [
    # (keyword_needed, country_needed, extra_points, reason)
    ("scan to bim",   ["germany","austria","switzerland"],  5, "DACH+ScanToBIM"),
    ("point cloud",   ["germany","austria","switzerland"],  4, "DACH+PointCloud"),
    ("drone survey",  ["india"],                            4, "India+Drone"),
    ("scan to bim",   ["united kingdom","uk","england"],    3, "UK+ScanToBIM"),
    ("bim coordination", ["australia","new zealand"],       3, "ANZ+BIM"),
]

# ── Generic email patterns ────────────────────────────────────────────────────
GENERIC_PREFIXES = {
    "info", "contact", "hello", "admin", "support", "office", "mail",
    "enquiries", "enquiry", "query", "sales", "noreply", "no-reply",
    "team", "help", "business", "general", "services", "accounts",
}
FREE_DOMAINS = {
    "gmail.com","yahoo.com","hotmail.com","outlook.com","live.com",
    "rediffmail.com","aol.com","icloud.com","protonmail.com","yandex.com",
}

# ── Junior title patterns ─────────────────────────────────────────────────────
JUNIOR_TITLES = ["intern","student","graduate","trainee","apprentice","junior","entry level"]
SENIOR_TITLES = ["director","head of","chief","cto","ceo","founder","owner","partner",
                 "principal","president","vp ","vice president","managing"]
MID_TITLES    = ["manager","lead","specialist","coordinator","consultant","engineer"]


def score_lead(lead: dict) -> tuple:
    """
    Returns (score: int 0-100, breakdown: dict)
    """
    # Build searchable text blob
    text_parts = [
        lead.get("title","") or "",
        lead.get("industry","") or "",
        lead.get("company","") or "",
        lead.get("search_query","") or "",
        lead.get("raw_data","") or "",
        lead.get("name","") or "",
        lead.get("notes","") or "",
    ]
    blob = " ".join(text_parts).lower()

    country_raw = (lead.get("country","") or "").lower().strip()
    email       = (lead.get("email","") or "").lower().strip()
    title       = (lead.get("title","") or "").lower().strip()

    hits      = []   # list of (keyword, points)
    bonuses   = []
    penalties = []

    # ── 1. Keyword hits ───────────────────────────────────────────────────────
    kw_total = 0
    for kw, pts in KEYWORD_SCORES:
        if kw.lower() in blob:
            hits.append({"kw": kw, "pts": pts})
            kw_total += pts

    kw_total = min(kw_total, 70)  # cap before bonuses

    # ── 2. Country bonus ──────────────────────────────────────────────────────
    country_bonus = DEFAULT_COUNTRY_BONUS
    for country_key, pts in COUNTRY_BONUSES.items():
        if country_key in country_raw:
            country_bonus = pts
            bonuses.append({"reason": f"Country:{country_raw}", "pts": pts})
            break

    # ── 3. Combo bonuses ──────────────────────────────────────────────────────
    combo_total = 0
    for kw_needed, countries_needed, extra, reason in COMBO_BONUSES:
        if kw_needed in blob:
            for cn in countries_needed:
                if cn in country_raw:
                    combo_total += extra
                    bonuses.append({"reason": reason, "pts": extra})
                    break

    # ── 4. Contact quality bonuses ────────────────────────────────────────────
    contact_bonus = 0

    if email:
        prefix = email.split("@")[0] if "@" in email else email
        domain = email.split("@")[1] if "@" in email else ""

        if domain in FREE_DOMAINS:
            penalties.append({"reason": "Free email domain", "pts": -15})
            contact_bonus -= 15
        elif prefix not in GENERIC_PREFIXES:
            bonuses.append({"reason": "Named personal email", "pts": 8})
            contact_bonus += 8
        else:
            penalties.append({"reason": "Generic email prefix", "pts": -10})
            contact_bonus -= 10

        if lead.get("email_verified"):
            bonuses.append({"reason": "Email verified by Hunter", "pts": 6})
            contact_bonus += 6
    else:
        penalties.append({"reason": "No email", "pts": -8})
        contact_bonus -= 8

    if lead.get("phone",""):
        bonuses.append({"reason": "Has phone", "pts": 5})
        contact_bonus += 5

    if lead.get("linkedin_url",""):
        bonuses.append({"reason": "Has LinkedIn", "pts": 4})
        contact_bonus += 4

    if lead.get("website",""):
        bonuses.append({"reason": "Has website", "pts": 2})
        contact_bonus += 2

    # ── 5. Title quality ──────────────────────────────────────────────────────
    if any(t in title for t in JUNIOR_TITLES):
        penalties.append({"reason": "Junior title", "pts": -10})
        contact_bonus -= 10
    elif any(t in title for t in SENIOR_TITLES):
        bonuses.append({"reason": "Senior title", "pts": 4})
        contact_bonus += 4
    elif any(t in title for t in MID_TITLES):
        bonuses.append({"reason": "Mid-level title", "pts": 2})
        contact_bonus += 2

    # ── 6. Academic penalty ───────────────────────────────────────────────────
    if any(w in blob for w in ["university","college","institute of technology","iit ","iim "]):
        penalties.append({"reason": "Academic institution", "pts": -5})
        contact_bonus -= 5

    # ── Final score ───────────────────────────────────────────────────────────
    raw = kw_total + country_bonus + combo_total + contact_bonus
    score = max(0, min(100, raw))

    breakdown = {
        "kw_total":     kw_total,
        "country_bonus": country_bonus,
        "combo_total":  combo_total,
        "contact_bonus": contact_bonus,
        "raw":          raw,
        "final":        score,
        "hits":         hits,
        "bonuses":      bonuses,
        "penalties":    penalties,
    }

    return score, breakdown


def score_tier(score: int) -> dict:
    if score >= 85:
        return {"label": "Hot Lead",      "cls": "score-hot",    "color": "#ff8c42"}
    elif score >= 70:
        return {"label": "High Priority", "cls": "score-high",   "color": "#D4A017"}
    elif score >= 55:
        return {"label": "Medium",        "cls": "score-medium", "color": "#64b5f6"}
    elif score >= 40:
        return {"label": "Low",           "cls": "score-low",    "color": "#888"}
    else:
        return {"label": "Poor",          "cls": "score-poor",   "color": "#555"}
