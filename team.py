"""
Shared team module — used by both BIM CRM and BIM LeadGen.
Handles: users, roles, chat messages, notifications.
All data stored in shared PostgreSQL (DATABASE_URL).
"""
import os, hashlib, secrets, logging
from datetime import datetime

log = logging.getLogger(__name__)

# ── Role definitions ───────────────────────────────────────────────────────────
ROLES = {
    "admin"  : {"label": "Admin",   "color": "#D4A017", "level": 3},
    "manager": {"label": "Manager", "color": "#64b5f6", "level": 2},
    "viewer" : {"label": "Viewer",  "color": "#888",    "level": 1},
}

_ALL_PERMS = ["view", "create", "edit", "delete", "approve", "reject",
              "sync", "search", "mail"]

ROLE_PERMISSIONS = {
    # Kishan only — full access + team/settings management
    "admin"  : _ALL_PERMS + ["manage_team", "manage_settings"],
    # All partners — full access to everything except team/settings mgmt
    "manager": _ALL_PERMS,
    "viewer" : ["view"],
}

def can(role: str, action: str) -> bool:
    return action in ROLE_PERMISSIONS.get(role, [])


# ── DB connection ──────────────────────────────────────────────────────────────
_PG_AVAILABLE = None  # None = untested, True/False = cached result

def _get_db():
    global _PG_AVAILABLE
    DATABASE_URL = os.getenv("DATABASE_URL", "")
    if DATABASE_URL and _PG_AVAILABLE is not False:
        import psycopg2, psycopg2.extras
        url = DATABASE_URL
        if "sslmode=" not in url:
            url += ("&" if "?" in url else "?") + "sslmode=require"
        try:
            conn = psycopg2.connect(url,
                                    cursor_factory=psycopg2.extras.RealDictCursor,
                                    connect_timeout=8)
            _PG_AVAILABLE = True
            return conn, True
        except Exception as e:
            log.warning("Team DB PostgreSQL unavailable (%s), using SQLite", e)
            _PG_AVAILABLE = False
    # SQLite fallback (local dev OR when PostgreSQL unreachable)
    import sqlite3
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "team.db")
    conn = sqlite3.connect(path, timeout=30)
    conn.row_factory = sqlite3.Row
    return conn, False

def _q(sql, pg):
    return sql if pg else sql.replace("%s", "?")

def _one(row):
    return dict(row) if row else None

def _all(rows):
    return [dict(r) for r in (rows or [])]


# ── Schema init ────────────────────────────────────────────────────────────────
def init_team_tables():
    conn, pg = _get_db()
    c = conn.cursor()
    pk = "SERIAL PRIMARY KEY" if pg else "INTEGER PRIMARY KEY AUTOINCREMENT"

    c.execute(f"""
        CREATE TABLE IF NOT EXISTS team_users (
            id           {pk},
            username     TEXT UNIQUE NOT NULL,
            display_name TEXT NOT NULL,
            password_hash TEXT NOT NULL,
            role         TEXT DEFAULT 'viewer',
            email        TEXT DEFAULT '',
            avatar_color TEXT DEFAULT '#D4A017',
            is_active    INTEGER DEFAULT 1,
            last_seen    TIMESTAMP,
            created_at   TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    c.execute(f"""
        CREATE TABLE IF NOT EXISTS chat_messages (
            id           {pk},
            user_id      INTEGER NOT NULL,
            username     TEXT NOT NULL,
            display_name TEXT NOT NULL,
            channel      TEXT NOT NULL DEFAULT 'general',
            message      TEXT NOT NULL,
            platform     TEXT DEFAULT 'general',
            reply_to     INTEGER,
            created_at   TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    c.execute(f"""
        CREATE TABLE IF NOT EXISTS notifications (
            id         {pk},
            user_id    INTEGER NOT NULL,
            title      TEXT NOT NULL,
            message    TEXT NOT NULL,
            type       TEXT DEFAULT 'info',
            read       INTEGER DEFAULT 0,
            link       TEXT DEFAULT '',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    # Seed default users if table is empty
    c.execute("SELECT COUNT(*) FROM team_users")
    row = c.fetchone()
    count = row["count"] if pg else row[0]
    if count == 0:
        _seed_users(c, pg)

    conn.commit()
    conn.close()
    log.info("Team tables initialised")


def _seed_users(c, pg):
    users = [
        ("kishan",   "Kishan Batavia", "Bim@2025",  "admin",   "#D4A017"),
        ("hirakraj", "Hirakraj",       "Bim@2025",  "manager", "#64b5f6"),
        ("tirth",    "Tirth",          "Bim@2025",  "manager", "#81c784"),
        ("jenish",   "Jenish",         "Bim@2025",  "manager", "#ce93d8"),
    ]
    for uname, display, password, role, color in users:
        ph = _hash_password(password)
        now = datetime.utcnow().isoformat()
        if pg:
            c.execute("""INSERT INTO team_users
                (username,display_name,password_hash,role,avatar_color,created_at)
                VALUES (%s,%s,%s,%s,%s,%s) ON CONFLICT (username) DO NOTHING""",
                (uname, display, ph, role, color, now))
        else:
            c.execute("""INSERT OR IGNORE INTO team_users
                (username,display_name,password_hash,role,avatar_color,created_at)
                VALUES (?,?,?,?,?,?)""",
                (uname, display, ph, role, color, now))
    log.info("Seeded 4 default team users")


# ── Password ───────────────────────────────────────────────────────────────────
def _hash_password(password: str) -> str:
    salt = secrets.token_hex(16)
    h    = hashlib.sha256((salt + password).encode()).hexdigest()
    return f"{salt}:{h}"

def check_password(stored: str, password: str) -> bool:
    try:
        salt, h = stored.split(":", 1)
        return hashlib.sha256((salt + password).encode()).hexdigest() == h
    except Exception:
        return False


# ── Users ──────────────────────────────────────────────────────────────────────
def get_user_by_username(username: str):
    conn, pg = _get_db()
    c = conn.cursor()
    c.execute(_q("SELECT * FROM team_users WHERE username=%s AND is_active=1", pg),
              (username.lower().strip(),))
    row = _one(c.fetchone()); conn.close()
    return row

def get_user_by_id(user_id: int):
    conn, pg = _get_db()
    c = conn.cursor()
    c.execute(_q("SELECT * FROM team_users WHERE id=%s", pg), (user_id,))
    row = _one(c.fetchone()); conn.close()
    return row

def get_all_users():
    conn, pg = _get_db()
    c = conn.cursor()
    c.execute("SELECT * FROM team_users ORDER BY role DESC, display_name")
    rows = _all(c.fetchall()); conn.close()
    return rows

def create_user(username: str, display_name: str, password: str,
                role: str = "viewer", email: str = "", color: str = "#888"):
    conn, pg = _get_db()
    c = conn.cursor()
    ph  = _hash_password(password)
    now = datetime.utcnow().isoformat()
    try:
        if pg:
            c.execute("""INSERT INTO team_users
                (username,display_name,password_hash,role,email,avatar_color,created_at)
                VALUES (%s,%s,%s,%s,%s,%s,%s) RETURNING id""",
                (username.lower(), display_name, ph, role, email, color, now))
            new_id = c.fetchone()["id"]
        else:
            c.execute("""INSERT INTO team_users
                (username,display_name,password_hash,role,email,avatar_color,created_at)
                VALUES (?,?,?,?,?,?,?)""",
                (username.lower(), display_name, ph, role, email, color, now))
            new_id = c.lastrowid
        conn.commit(); conn.close()
        return new_id
    except Exception as e:
        conn.close()
        raise e

def update_user(user_id: int, fields: dict):
    if not fields: return
    conn, pg = _get_db()
    c = conn.cursor()
    if "password" in fields:
        fields["password_hash"] = _hash_password(fields.pop("password"))
    ph = "%" if pg else "?"
    sets = ", ".join(f"{k}={ph}s" if pg else f"{k}=?" for k in fields)
    vals = list(fields.values()) + [user_id]
    c.execute(_q(f"UPDATE team_users SET {sets} WHERE id=%s", pg), vals)
    conn.commit(); conn.close()

def update_last_seen(user_id: int):
    conn, pg = _get_db()
    c = conn.cursor()
    c.execute(_q("UPDATE team_users SET last_seen=%s WHERE id=%s", pg),
              (datetime.utcnow().isoformat(), user_id))
    conn.commit(); conn.close()

def authenticate(username: str, password: str):
    """Returns user dict if valid, None otherwise."""
    user = get_user_by_username(username)
    if user and check_password(user["password_hash"], password):
        update_last_seen(user["id"])
        return user
    return None


# ── Chat ───────────────────────────────────────────────────────────────────────
CHANNELS = [
    {"id": "general", "name": "General",  "icon": "bi-chat-dots",    "desc": "Team-wide chat"},
    {"id": "crm",     "name": "CRM",      "icon": "bi-people",       "desc": "CRM platform"},
    {"id": "leadgen", "name": "LeadGen",  "icon": "bi-search",       "desc": "Lead generation"},
    {"id": "leads",   "name": "Leads",    "icon": "bi-person-check", "desc": "Lead discussions"},
    {"id": "alerts",  "name": "Alerts",   "icon": "bi-bell",         "desc": "System alerts"},
]

def send_message(user_id: int, username: str, display_name: str,
                 channel: str, message: str, platform: str = "general",
                 reply_to: int = None) -> int:
    conn, pg = _get_db()
    c = conn.cursor()
    now = datetime.utcnow().isoformat()
    if pg:
        c.execute("""INSERT INTO chat_messages
            (user_id,username,display_name,channel,message,platform,reply_to,created_at)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id""",
            (user_id, username, display_name, channel,
             message[:2000], platform, reply_to, now))
        new_id = c.fetchone()["id"]
    else:
        c.execute("""INSERT INTO chat_messages
            (user_id,username,display_name,channel,message,platform,reply_to,created_at)
            VALUES (?,?,?,?,?,?,?,?)""",
            (user_id, username, display_name, channel,
             message[:2000], platform, reply_to, now))
        new_id = c.lastrowid
    conn.commit(); conn.close()
    return new_id

def get_messages(channel: str = "general", since_id: int = 0, limit: int = 50):
    conn, pg = _get_db()
    c = conn.cursor()
    c.execute(_q("""SELECT * FROM chat_messages
        WHERE channel=%s AND id>%s
        ORDER BY created_at DESC LIMIT %s""", pg),
        (channel, since_id, limit))
    rows = list(reversed(_all(c.fetchall()))); conn.close()
    return rows

def get_unread_count(user_id: int, last_seen_ids: dict) -> int:
    """Count messages since last seen per channel."""
    conn, pg = _get_db()
    c = conn.cursor()
    total = 0
    for channel, last_id in last_seen_ids.items():
        c.execute(_q("""SELECT COUNT(*) FROM chat_messages
            WHERE channel=%s AND id>%s AND user_id!=%s""", pg),
            (channel, last_id, user_id))
        row = c.fetchone()
        total += (row["count"] if pg else row[0]) if row else 0
    conn.close()
    return total

def get_latest_message_id() -> int:
    conn, pg = _get_db()
    c = conn.cursor()
    c.execute("SELECT MAX(id) FROM chat_messages")
    row = c.fetchone(); conn.close()
    if pg:
        v = row["max"] if row else 0
    else:
        v = row[0] if row else 0
    return v or 0

def post_system_message(channel: str, message: str, platform: str = "general"):
    """Post an automated system message (lead synced, new user, etc.)"""
    send_message(0, "system", "System", channel, message, platform)


# ── Notifications ──────────────────────────────────────────────────────────────
def notify(user_id: int, title: str, message: str,
           type: str = "info", link: str = ""):
    conn, pg = _get_db()
    c = conn.cursor()
    now = datetime.utcnow().isoformat()
    if pg:
        c.execute("""INSERT INTO notifications
            (user_id,title,message,type,link,created_at)
            VALUES (%s,%s,%s,%s,%s,%s)""",
            (user_id, title, message, type, link, now))
    else:
        c.execute("""INSERT INTO notifications
            (user_id,title,message,type,link,created_at)
            VALUES (?,?,?,?,?,?)""",
            (user_id, title, message, type, link, now))
    conn.commit(); conn.close()

def notify_all(title: str, message: str, type: str = "info",
               link: str = "", exclude_user: int = None):
    users = get_all_users()
    for u in users:
        if u["id"] != exclude_user:
            notify(u["id"], title, message, type, link)

def get_notifications(user_id: int, limit: int = 20):
    conn, pg = _get_db()
    c = conn.cursor()
    c.execute(_q("""SELECT * FROM notifications WHERE user_id=%s
        ORDER BY created_at DESC LIMIT %s""", pg), (user_id, limit))
    rows = _all(c.fetchall()); conn.close()
    return rows

def mark_notifications_read(user_id: int):
    conn, pg = _get_db()
    c = conn.cursor()
    c.execute(_q("UPDATE notifications SET read=1 WHERE user_id=%s", pg), (user_id,))
    conn.commit(); conn.close()

def get_unread_notifications(user_id: int) -> int:
    conn, pg = _get_db()
    c = conn.cursor()
    c.execute(_q("SELECT COUNT(*) FROM notifications WHERE user_id=%s AND read=0", pg),
              (user_id,))
    row = c.fetchone(); conn.close()
    return (row["count"] if pg else row[0]) if row else 0
