"""
BIM Lead Generator — Database Layer
SQLite locally, PostgreSQL on hosted (set DATABASE_URL env var).
"""
import os
import sqlite3
from datetime import datetime

DATABASE_URL = os.getenv("DATABASE_URL", "")
_USE_PG      = bool(DATABASE_URL)
_PG_OK       = None  # None=untested, True/False=cached

def _is_pg():
    return _USE_PG and _PG_OK is not False

def get_db():
    global _PG_OK
    if _USE_PG and _PG_OK is not False:
        import psycopg2, psycopg2.extras
        url = DATABASE_URL
        if "sslmode=" not in url:
            url += ("&" if "?" in url else "?") + "sslmode=require"
        try:
            conn = psycopg2.connect(url,
                                    cursor_factory=psycopg2.extras.RealDictCursor,
                                    connect_timeout=8)
            _PG_OK = True
            return conn
        except Exception as e:
            import logging
            logging.getLogger(__name__).warning("PostgreSQL unavailable (%s), using SQLite", e)
            _PG_OK = False
    DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "leadgen.db")
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn

def _q(sql):
    if _is_pg():
        return sql.replace("?", "%s")
    return sql

def _fetchall(rows):
    return [dict(r) for r in (rows or [])]

def _fetchone(row):
    return dict(row) if row else None


# ── Schema ────────────────────────────────────────────────────────────────────

def init_db():
    conn = get_db()
    c    = conn.cursor()
    pk   = "SERIAL PRIMARY KEY" if _is_pg() else "INTEGER PRIMARY KEY AUTOINCREMENT"

    c.execute(f"""
        CREATE TABLE IF NOT EXISTS raw_leads (
            id                    {pk},
            name                  TEXT,
            first_name            TEXT,
            last_name             TEXT,
            email                 TEXT,
            phone                 TEXT,
            company               TEXT NOT NULL DEFAULT '',
            title                 TEXT,
            website               TEXT,
            city                  TEXT,
            country               TEXT,
            industry              TEXT,
            linkedin_url          TEXT,
            source                TEXT NOT NULL DEFAULT 'manual',
            source_url            TEXT,
            search_query          TEXT,
            raw_data              TEXT,
            score                 INTEGER DEFAULT 0,
            score_breakdown       TEXT,
            status                TEXT DEFAULT 'New',
            notes                 TEXT,
            rejected_reason       TEXT,
            email_type            TEXT,
            email_verified        INTEGER DEFAULT 0,
            job_id                INTEGER,
            crm_lead_id           INTEGER,
            synced_at             TIMESTAMP,
            is_bounced_from_crm   INTEGER DEFAULT 0,
            crm_bounce_reason     TEXT,
            company_research_done INTEGER DEFAULT 0,
            found_at              TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at            TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    c.execute(f"""
        CREATE TABLE IF NOT EXISTS search_jobs (
            id            {pk},
            query         TEXT NOT NULL,
            sources       TEXT,
            status        TEXT DEFAULT 'queued',
            leads_found   INTEGER DEFAULT 0,
            leads_new     INTEGER DEFAULT 0,
            error_message TEXT,
            started_at    TIMESTAMP,
            finished_at   TIMESTAMP,
            created_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    c.execute(f"""
        CREATE TABLE IF NOT EXISTS sync_log (
            id           {pk},
            raw_lead_id  INTEGER NOT NULL,
            crm_lead_id  INTEGER,
            action       TEXT,
            error_message TEXT,
            synced_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    c.execute(f"""
        CREATE TABLE IF NOT EXISTS bounce_queue (
            id                   {pk},
            crm_lead_id          INTEGER NOT NULL,
            company              TEXT NOT NULL,
            website              TEXT,
            original_email       TEXT,
            bounce_reason        TEXT,
            status               TEXT DEFAULT 'pending',
            replacement_lead_id  INTEGER,
            received_at          TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            resolved_at          TIMESTAMP
        )
    """)

    c.execute("""
        CREATE TABLE IF NOT EXISTS app_config (
            key        TEXT PRIMARY KEY,
            value      TEXT,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    conn.commit()
    conn.close()


# ── Config ────────────────────────────────────────────────────────────────────

def get_config(key, default=""):
    try:
        conn = get_db(); c = conn.cursor()
        c.execute(_q("SELECT value FROM app_config WHERE key=?"), (key,))
        row = c.fetchone(); conn.close()
        if row:
            v = row["value"] if _is_pg() else row[0]
            return v if v is not None else default
    except Exception:
        pass
    return default

def set_config(key, value):
    conn = get_db(); c = conn.cursor()
    now = datetime.utcnow().isoformat()
    if _is_pg():
        c.execute("INSERT INTO app_config (key,value,updated_at) VALUES (%s,%s,%s) "
                  "ON CONFLICT (key) DO UPDATE SET value=EXCLUDED.value, updated_at=EXCLUDED.updated_at",
                  (key, value, now))
    else:
        c.execute("INSERT OR REPLACE INTO app_config (key,value,updated_at) VALUES (?,?,?)",
                  (key, value, now))
    conn.commit(); conn.close()


# ── Raw Leads ─────────────────────────────────────────────────────────────────

def insert_lead(lead: dict) -> int:
    conn = get_db(); c = conn.cursor()
    now = datetime.utcnow().isoformat()
    if _is_pg():
        c.execute("""
            INSERT INTO raw_leads (name,first_name,last_name,email,phone,company,title,
                website,city,country,industry,linkedin_url,source,source_url,search_query,
                raw_data,score,score_breakdown,status,email_type,email_verified,
                is_bounced_from_crm,crm_bounce_reason,job_id,found_at,updated_at)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
            RETURNING id
        """, (
            lead.get("name",""), lead.get("first_name",""), lead.get("last_name",""),
            lead.get("email",""), lead.get("phone",""), lead.get("company",""),
            lead.get("title",""), lead.get("website",""), lead.get("city",""),
            lead.get("country",""), lead.get("industry",""), lead.get("linkedin_url",""),
            lead.get("source","manual"), lead.get("source_url",""), lead.get("search_query",""),
            lead.get("raw_data",""), lead.get("score",0), lead.get("score_breakdown","{}"),
            lead.get("status","New"), lead.get("email_type","unknown"),
            int(lead.get("email_verified",0)), int(lead.get("is_bounced_from_crm",0)),
            lead.get("crm_bounce_reason",""), lead.get("job_id"), now, now,
        ))
        row = c.fetchone(); new_id = row["id"] if row else None
    else:
        c.execute("""
            INSERT INTO raw_leads (name,first_name,last_name,email,phone,company,title,
                website,city,country,industry,linkedin_url,source,source_url,search_query,
                raw_data,score,score_breakdown,status,email_type,email_verified,
                is_bounced_from_crm,crm_bounce_reason,job_id,found_at,updated_at)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """, (
            lead.get("name",""), lead.get("first_name",""), lead.get("last_name",""),
            lead.get("email",""), lead.get("phone",""), lead.get("company",""),
            lead.get("title",""), lead.get("website",""), lead.get("city",""),
            lead.get("country",""), lead.get("industry",""), lead.get("linkedin_url",""),
            lead.get("source","manual"), lead.get("source_url",""), lead.get("search_query",""),
            lead.get("raw_data",""), lead.get("score",0), lead.get("score_breakdown","{}"),
            lead.get("status","New"), lead.get("email_type","unknown"),
            int(lead.get("email_verified",0)), int(lead.get("is_bounced_from_crm",0)),
            lead.get("crm_bounce_reason",""), lead.get("job_id"), now, now,
        ))
        new_id = c.lastrowid
    conn.commit(); conn.close()
    return new_id

def get_leads(status=None, min_score=0, country=None, source=None,
              search=None, job_id=None, page=1, per_page=50):
    conn = get_db(); c = conn.cursor()
    where = ["score >= " + str(min_score)]
    params = []
    if status:
        where.append(_q("status=?")); params.append(status)
    if country:
        where.append(_q("country=?")); params.append(country)
    if source:
        where.append(_q("source=?")); params.append(source)
    if job_id:
        where.append(_q("job_id=?")); params.append(job_id)
    if search:
        where.append(_q("(name LIKE ? OR email LIKE ? OR company LIKE ?)"))
        s = f"%{search}%"; params += [s, s, s]
    sql = "SELECT * FROM raw_leads WHERE " + " AND ".join(where)
    sql += " ORDER BY score DESC, found_at DESC"
    count_params = list(params)  # snapshot before adding limit/offset
    sql += _q(" LIMIT ? OFFSET ?")
    params += [per_page, (page - 1) * per_page]
    c.execute(sql, params)
    leads = _fetchall(c.fetchall())
    # total count
    c.execute("SELECT COUNT(*) FROM raw_leads WHERE " + " AND ".join(where), count_params)
    row = c.fetchone()
    total = (row["count"] if _is_pg() else row[0]) if row else 0
    conn.close()
    return leads, total

def get_lead(lead_id):
    conn = get_db(); c = conn.cursor()
    c.execute(_q("SELECT * FROM raw_leads WHERE id=?"), (lead_id,))
    row = _fetchone(c.fetchone()); conn.close()
    return row

def update_lead(lead_id, fields: dict):
    if not fields: return
    conn = get_db(); c = conn.cursor()
    fields["updated_at"] = datetime.utcnow().isoformat()
    sets = ", ".join(_q(f"{k}=?") for k in fields)
    vals = list(fields.values()) + [lead_id]
    c.execute(_q(f"UPDATE raw_leads SET {sets} WHERE id=?"), vals)
    conn.commit(); conn.close()

def is_duplicate(email, company, name):
    """Return True if this lead already exists in the DB."""
    conn = get_db(); c = conn.cursor()
    if email and email.strip():
        c.execute(_q("SELECT id FROM raw_leads WHERE LOWER(email)=LOWER(?) AND email!=''"),
                  (email.strip(),))
        if c.fetchone():
            conn.close(); return True
    # Company + name fuzzy check
    if company and name:
        norm_co   = _normalize(company)
        norm_name = _normalize(name)
        c.execute("SELECT id, company, name FROM raw_leads WHERE company != ''")
        for row in c.fetchall():
            r = dict(row)
            if _normalize(r.get("company","")) == norm_co and \
               _normalize(r.get("name","")) == norm_name:
                conn.close(); return True
    conn.close()
    return False

def _normalize(s):
    import re
    s = s.lower().strip()
    for sfx in [" ltd"," limited"," inc"," llc"," gmbh"," pvt"," private"," plc"," corp"," co."]:
        s = s.replace(sfx, "")
    return re.sub(r'\s+', ' ', s).strip()

def get_stats():
    conn = get_db(); c = conn.cursor()
    stats = {}

    def _count(sql, params=()):
        c.execute(sql, params); r = c.fetchone()
        return (r["count"] if _is_pg() else r[0]) if r else 0

    stats["total"]    = _count("SELECT COUNT(*) FROM raw_leads")
    stats["pending"]  = _count(_q("SELECT COUNT(*) FROM raw_leads WHERE status=?"), ("New",))
    stats["approved"] = _count(_q("SELECT COUNT(*) FROM raw_leads WHERE status=?"), ("Approved",))
    stats["rejected"] = _count(_q("SELECT COUNT(*) FROM raw_leads WHERE status=?"), ("Rejected",))
    stats["synced"]   = _count(_q("SELECT COUNT(*) FROM raw_leads WHERE status=?"), ("Synced",))
    stats["hot"]      = _count("SELECT COUNT(*) FROM raw_leads WHERE score>=85")
    stats["with_email"] = _count("SELECT COUNT(*) FROM raw_leads WHERE email IS NOT NULL AND email!=''")
    stats["jobs_running"] = _count(_q("SELECT COUNT(*) FROM search_jobs WHERE status=?"), ("running",))
    conn.close()
    return stats


def get_top_leads(limit=5):
    conn = get_db(); c = conn.cursor()
    c.execute(_q("SELECT * FROM raw_leads ORDER BY score DESC, found_at DESC LIMIT ?"), (limit,))
    rows = _fetchall(c.fetchall()); conn.close()
    return rows


def get_sources():
    conn = get_db(); c = conn.cursor()
    c.execute("SELECT DISTINCT source FROM raw_leads WHERE source IS NOT NULL ORDER BY source")
    rows = c.fetchall(); conn.close()
    return [r["source"] if _is_pg() else r[0] for r in rows if r]


def get_sync_log(limit=50):
    conn = get_db(); c = conn.cursor()
    c.execute(_q("SELECT * FROM sync_log ORDER BY synced_at DESC LIMIT ?"), (limit,))
    rows = _fetchall(c.fetchall()); conn.close()
    return rows


def log_sync(lead_id: int, status: str, message: str = ""):
    conn = get_db(); c = conn.cursor()
    now = datetime.utcnow().isoformat()
    if _is_pg():
        c.execute("INSERT INTO sync_log (raw_lead_id, action, error_message, synced_at) VALUES (%s,%s,%s,%s)",
                  (lead_id, status, message, now))
    else:
        c.execute("INSERT INTO sync_log (raw_lead_id, action, error_message, synced_at) VALUES (?,?,?,?)",
                  (lead_id, status, message, now))
    conn.commit(); conn.close()


# ── Search Jobs ───────────────────────────────────────────────────────────────

def create_job(query, sources, country="", max_results=50):
    import json
    conn = get_db(); c = conn.cursor()
    now = datetime.utcnow().isoformat()
    src_json = json.dumps(sources) if isinstance(sources, list) else sources
    if _is_pg():
        c.execute("INSERT INTO search_jobs (query,sources,status,created_at) "
                  "VALUES (%s,%s,'queued',%s) RETURNING id",
                  (query, src_json, now))
        job_id = c.fetchone()["id"]
    else:
        c.execute("INSERT INTO search_jobs (query,sources,status,created_at) VALUES (?,?,'queued',?)",
                  (query, src_json, now))
        job_id = c.lastrowid
    conn.commit(); conn.close()
    return job_id

def update_job(job_id, **kwargs):
    if not kwargs: return
    conn = get_db(); c = conn.cursor()
    sets = ", ".join(_q(f"{k}=?") for k in kwargs)
    vals = list(kwargs.values()) + [job_id]
    c.execute(_q(f"UPDATE search_jobs SET {sets} WHERE id=?"), vals)
    conn.commit(); conn.close()

def get_jobs(limit=50):
    conn = get_db(); c = conn.cursor()
    c.execute(_q("SELECT * FROM search_jobs ORDER BY created_at DESC LIMIT ?"), (limit,))
    rows = _fetchall(c.fetchall()); conn.close()
    return rows

def get_job(job_id):
    conn = get_db(); c = conn.cursor()
    c.execute(_q("SELECT * FROM search_jobs WHERE id=?"), (job_id,))
    row = _fetchone(c.fetchone()); conn.close()
    return row
