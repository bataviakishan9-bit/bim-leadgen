"""
BIM Lead Generator
Automated lead discovery platform for BIM Infra Solutions.
Finds, scores, and manages leads from Google, Hunter.io, BuildingSMART, ENR, RICS, Scholar.
"""
import os, json, logging
from datetime import datetime
from urllib.parse import urlencode
from flask import (Flask, render_template, request, redirect, url_for,
                   flash, jsonify, session)
from flask_login import (LoginManager, UserMixin, login_user, logout_user,
                         login_required, current_user)
from werkzeug.security import generate_password_hash, check_password_hash
from dotenv import load_dotenv

import database as db
from scorer import score_lead, score_tier
import team as tm
from chat_routes import register_chat_routes

load_dotenv()
logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger(__name__)

app = Flask(__name__)
app.secret_key = os.getenv("SECRET_KEY", "bim-leadgen-2025")

# ── Team / Chat integration ────────────────────────────────────────────────────
try:
    tm.init_team_tables()
except Exception as _te:
    log.warning("Team tables init: %s", _te)

register_chat_routes(app, platform="leadgen")

@app.context_processor
def inject_team():
    uid  = session.get("team_user_id")
    user = tm.get_user_by_id(uid) if uid else None
    role = session.get("team_role", "viewer")
    return dict(
        _current_user = user,
        _team_role    = role,
        _can          = lambda action: tm.can(role, action),
        _PLATFORM     = "leadgen",
        _CHANNELS     = tm.CHANNELS,
        _ROLES        = tm.ROLES,
    )

# ── Auth ──────────────────────────────────────────────────────────────────────

login_manager = LoginManager(app)
login_manager.login_view = "login"
login_manager.login_message_category = "warning"

ADMIN_USER = os.getenv("ADMIN_USERNAME", "admin")
ADMIN_PASS = os.getenv("ADMIN_PASSWORD", "admin123")
ADMIN_HASH = generate_password_hash(ADMIN_PASS)

class LGUser(UserMixin):
    id       = "1"
    username = ADMIN_USER

@login_manager.user_loader
def load_user(uid):
    if uid == "1": return LGUser()
    return None

@app.route("/login", methods=["GET","POST"])
def login():
    if current_user.is_authenticated:
        return redirect(url_for("dashboard"))
    error = None
    if request.method == "POST":
        username = request.form.get("username","").strip().lower()
        password = request.form.get("password","")
        # Try team DB first (shared auth)
        team_user = tm.authenticate(username, password)
        if team_user:
            login_user(LGUser(), remember=True)
            session["team_user_id"] = team_user["id"]
            session["team_role"]    = team_user["role"]
            # Role check — viewers can still view LeadGen
            return redirect(url_for("dashboard"))
        # Fallback to env-based admin
        if username == ADMIN_USER.lower() and check_password_hash(ADMIN_HASH, password):
            login_user(LGUser(), remember=True)
            return redirect(url_for("dashboard"))
        error = "Invalid username or password."
    return render_template("login.html", error=error)

@app.route("/logout")
def logout():
    session.pop("team_user_id", None)
    session.pop("team_role", None)
    logout_user()
    return redirect(url_for("login"))


# ── Dashboard ─────────────────────────────────────────────────────────────────

@app.route("/")
@login_required
def dashboard():
    stats      = db.get_stats()
    recent_jobs = db.get_jobs(limit=5)
    top_leads  = db.get_top_leads(limit=5)
    return render_template("dashboard.html",
                           stats=stats,
                           recent_jobs=recent_jobs,
                           top_leads=top_leads)


# ── Search / Jobs ─────────────────────────────────────────────────────────────

@app.route("/search")
@login_required
def search_page():
    linkedin_enabled = bool(
        (db.get_config("LINKEDIN_CLIENT_ID") or os.getenv("LINKEDIN_CLIENT_ID","")) and
        (db.get_config("LINKEDIN_CLIENT_SECRET") or os.getenv("LINKEDIN_CLIENT_SECRET",""))
    )
    return render_template("search.html", linkedin_enabled=linkedin_enabled)

@app.route("/search/run", methods=["POST"])
@login_required
def run_search():
    query      = request.form.get("query","").strip()
    sources    = request.form.getlist("sources") or ["google","buildingsmart"]
    country    = request.form.get("country","")
    max_results = int(request.form.get("max_results", 50))
    if not query:
        flash("Enter a search query.", "warning")
        return redirect(url_for("search_page"))
    job_id = db.create_job(query, sources, country=country, max_results=max_results)
    _scheduler.add_job(
        _run_search_job,
        args=[job_id, query, sources, country, max_results],
        id=f"search_{job_id}",
        misfire_grace_time=600,
    )
    flash(f'Search started: "{query}". Leads will appear as they come in.', "success")
    return redirect(url_for("job_detail", job_id=job_id))

@app.route("/jobs")
@login_required
def jobs_page():
    all_jobs = db.get_jobs(limit=100)
    return render_template("jobs.html", jobs=all_jobs)

@app.route("/jobs/<int:job_id>")
@login_required
def job_detail(job_id):
    job = db.get_job(job_id)
    if not job:
        flash("Job not found.", "danger")
        return redirect(url_for("jobs_page"))
    leads, _ = db.get_leads(job_id=job_id, page=1, per_page=50)
    for l in leads:
        l["tier"] = score_tier(l.get("score", 0))
    return render_template("job_detail.html", job=job, leads=leads)

@app.route("/api/jobs/<int:job_id>/status")
@login_required
def api_job_status(job_id):
    job = db.get_job(job_id)
    if not job:
        return jsonify({"error": "not found"}), 404
    return jsonify({
        "status"     : job["status"],
        "leads_found": job.get("leads_found", 0),
        "leads_new"  : job.get("leads_new", 0),
        "error"      : job.get("error_message",""),
    })


# ── Lead Review ───────────────────────────────────────────────────────────────

@app.route("/leads")
@login_required
def leads_page():
    q        = request.args.get("q","")
    status   = request.args.get("status","")
    tier     = request.args.get("tier","")
    source   = request.args.get("source","")
    job_id   = request.args.get("job_id","")
    page     = int(request.args.get("page", 1))
    per_page = 50

    # Tier → min_score mapping
    min_score = 0
    if tier == "hot":    min_score = 85
    elif tier == "high": min_score = 70
    elif tier == "medium": min_score = 55
    elif tier == "low":  min_score = 0

    lead_list, total = db.get_leads(
        status=status or None,
        min_score=min_score,
        source=source or None,
        search=q or None,
        job_id=int(job_id) if job_id else None,
        page=page,
        per_page=per_page,
    )
    for l in lead_list:
        l["review_status"] = l.get("status","pending").lower()
        l["tier"]          = score_tier(l.get("score", 0))

    total_pages = (total + per_page - 1) // per_page
    # Build query string for pagination links (without page=)
    qs_parts = {}
    if q:      qs_parts["q"]      = q
    if status: qs_parts["status"] = status
    if tier:   qs_parts["tier"]   = tier
    if source: qs_parts["source"] = source
    if job_id: qs_parts["job_id"] = job_id
    qs = urlencode(qs_parts)

    # Available sources for filter dropdown
    all_sources = db.get_sources()

    filters = {"q": q, "status": status, "tier": tier,
               "source": source, "job_id": job_id}
    return render_template("leads.html",
                           leads=lead_list,
                           total=total,
                           page=page,
                           per_page=per_page,
                           total_pages=total_pages,
                           qs=qs,
                           filters=filters,
                           sources=all_sources)

@app.route("/leads/<int:lead_id>", methods=["GET","POST"])
@login_required
def lead_detail(lead_id):
    lead = db.get_lead(lead_id)
    if not lead:
        flash("Lead not found.", "danger")
        return redirect(url_for("leads_page"))

    if request.method == "POST":
        action = request.form.get("action","")
        if action == "approve":
            db.update_lead(lead_id, {"status": "Approved"})
            flash("Lead approved.", "success")
            # Auto-sync to CRM
            _auto_sync_lead(lead_id, lead)
            # Notify team
            try:
                tu = tm.get_user_by_id(session.get("team_user_id",0))
                actor = tu["display_name"] if tu else "Someone"
                tm.post_system_message("leads",
                    f"✅ {actor} approved lead: {lead.get('company') or lead.get('name','?')} (Score: {lead.get('score',0)})",
                    "leadgen")
                tm.notify_all("Lead Approved",
                    f"{lead.get('company') or lead.get('name','?')} approved by {actor}",
                    type="lead", link=f"/leads/{lead_id}",
                    exclude_user=session.get("team_user_id"))
            except Exception:
                pass
        elif action == "reject":
            db.update_lead(lead_id, {"status": "Rejected"})
            flash("Lead rejected.", "info")
        elif action == "notes":
            db.update_lead(lead_id, {"notes": request.form.get("notes","")})
            flash("Notes saved.", "success")
        elif action == "enrich":
            _enrich_lead(lead_id, lead)
        elif action == "sync":
            _sync_single_lead(lead_id, lead)
        return redirect(url_for("lead_detail", lead_id=lead_id))

    # Reload after any POST
    lead = db.get_lead(lead_id)
    lead["review_status"] = lead.get("status","pending").lower()
    lead["synced_to_crm"] = bool(lead.get("synced_at"))
    lead["tier"]          = score_tier(lead.get("score", 0))

    score_breakdown = {}
    if lead.get("score_breakdown"):
        try: score_breakdown = json.loads(lead["score_breakdown"])
        except: pass

    return render_template("lead_detail.html",
                           lead=lead,
                           score_breakdown=score_breakdown)


def _enrich_lead(lead_id: int, lead: dict):
    """Try Hunter.io email finder for this lead."""
    from scrapers.hunter_scraper import email_finder, verify_email
    from urllib.parse import urlparse
    website = lead.get("website","")
    domain  = urlparse(website).netloc.replace("www.","") if website else ""
    fn, ln  = lead.get("first_name",""), lead.get("last_name","")
    if domain and fn and ln:
        result = email_finder(domain, fn, ln)
        if result and result.get("email"):
            email = result["email"]
            vfy   = verify_email(email)
            db.update_lead(lead_id, {
                "email"         : email,
                "email_type"    : result.get("email_type","personal"),
                "email_verified": 1 if vfy.get("score",0) >= 70 else 0,
            })
            updated = db.get_lead(lead_id)
            if updated:
                new_score, breakdown = score_lead(updated)
                db.update_lead(lead_id, {
                    "score"          : new_score,
                    "score_breakdown": json.dumps(breakdown),
                })
            flash(f"Email found: {email}", "success")
        else:
            flash("Hunter.io could not find an email for this lead.", "warning")
    else:
        flash("Need first name, last name, and website to use email finder.", "warning")


def _auto_sync_lead(lead_id: int, lead: dict):
    """Silently auto-sync to CRM when a lead is approved."""
    crm_url = db.get_config("CRM_URL") or os.getenv("CRM_URL","https://bim-crm.onrender.com")
    secret  = os.getenv("SYNC_SECRET","bim-sync-2025")
    try:
        import requests as _req
        r = _req.post(f"{crm_url}/api/sync-leads",
                      json={"leads": [_build_crm_lead(lead)], "secret": secret},
                      headers={"Content-Type":"application/json",
                               "X-Sync-Secret": secret},
                      timeout=10)
        if r.status_code == 200:
            db.update_lead(lead_id, {"synced_at": datetime.utcnow().isoformat()})
            log.info("Auto-synced lead %d to CRM", lead_id)
    except Exception as e:
        log.warning("Auto-sync lead %d failed: %s", lead_id, e)


def _sync_single_lead(lead_id: int, lead: dict):
    """Sync one lead to BIM CRM."""
    import requests as _req
    crm_url = db.get_config("CRM_URL") or os.getenv("CRM_URL","http://localhost:5000")
    try:
        payload = [_build_crm_lead(lead)]
        r = _req.post(f"{crm_url}/api/sync-leads",
                      json={"leads": payload},
                      headers={"Content-Type":"application/json"},
                      timeout=15)
        data = r.json()
        if data.get("imported", 0) > 0:
            db.update_lead(lead_id, {"synced_at": datetime.utcnow().isoformat()})
            flash("Lead synced to CRM.", "success")
        else:
            flash(f"CRM response: {data}", "warning")
    except Exception as e:
        flash(f"Sync failed: {e}", "danger")


@app.route("/leads/bulk-action", methods=["POST"])
@login_required
def bulk_action():
    action  = request.form.get("action","")
    ids     = request.form.getlist("lead_ids")
    if not ids:
        flash("No leads selected.", "warning")
        return redirect(url_for("leads_page"))
    count = 0
    for lid in ids:
        try:
            if action == "approve":
                db.update_lead(int(lid), {"status": "Approved"})
                count += 1
            elif action == "reject":
                db.update_lead(int(lid), {"status": "Rejected"})
                count += 1
            elif action == "sync":
                lead = db.get_lead(int(lid))
                if lead:
                    _sync_single_lead(int(lid), lead)
                    count += 1
        except Exception:
            pass
    if action != "sync":
        flash(f"{action.title()}d {count} leads.", "success")
    return redirect(url_for("leads_page"))


# ── Settings ──────────────────────────────────────────────────────────────────

@app.route("/settings", methods=["GET","POST"])
@login_required
def settings_page():
    KEYS = ["HUNTER_API_KEY","LINKEDIN_CLIENT_ID","LINKEDIN_CLIENT_SECRET","CRM_URL","CRM_PASSWORD"]
    if request.method == "POST":
        for key in KEYS:
            val = request.form.get(key,"").strip()
            if val:
                db.set_config(key, val)
                os.environ[key] = val
        flash("Settings saved.", "success")
        return redirect(url_for("settings_page"))

    config = {k: db.get_config(k) or os.getenv(k,"") for k in KEYS}
    return render_template("settings.html", config=config)


# ── CRM Sync ──────────────────────────────────────────────────────────────────

@app.route("/sync")
@login_required
def sync_page():
    crm_url      = db.get_config("CRM_URL") or os.getenv("CRM_URL","http://localhost:5000")
    approved, _  = db.get_leads(status="Approved", page=1, per_page=1000)
    pending_sync = sum(1 for l in approved if not l.get("synced_at"))
    already_synced = sum(1 for l in approved if l.get("synced_at"))
    sync_log     = db.get_sync_log(limit=50)
    return render_template("sync.html",
                           crm_url=crm_url,
                           pending_sync=pending_sync,
                           already_synced=already_synced,
                           sync_log=sync_log)

@app.route("/sync/run", methods=["POST"])
@login_required
def run_sync():
    import requests as _req
    crm_url    = db.get_config("CRM_URL") or os.getenv("CRM_URL","http://localhost:5000")
    sync_filter = request.form.get("filter","pending")

    approved, _ = db.get_leads(status="Approved", page=1, per_page=1000)
    if sync_filter == "pending":
        to_sync = [l for l in approved if not l.get("synced_at")]
    else:
        to_sync = approved

    if not to_sync:
        flash("No leads to sync.", "info")
        return redirect(url_for("sync_page"))

    payload = [_build_crm_lead(l) for l in to_sync]
    try:
        r = _req.post(f"{crm_url}/api/sync-leads",
                      json={"leads": payload},
                      headers={"Content-Type":"application/json"},
                      timeout=30)
        data     = r.json()
        imported = data.get("imported", 0)
        skipped  = data.get("skipped", 0)
        now      = datetime.utcnow().isoformat()
        for lead in to_sync:
            db.update_lead(lead["id"], {"synced_at": now})
            db.log_sync(lead["id"], "success", f"CRM imported={imported}")
        flash(f"Synced: {imported} imported, {skipped} skipped.", "success")
    except Exception as e:
        flash(f"Sync failed: {e}", "danger")

    return redirect(url_for("sync_page"))


def _build_crm_lead(l: dict) -> dict:
    """Map LeadGen lead dict to BIM CRM lead dict."""
    score     = l.get("score", 0)
    likelihood = "High" if score >= 80 else "Medium" if score >= 55 else "Low"
    return {
        "first_name"            : l.get("first_name","") or (l.get("name","") or "").split()[0] if l.get("name") else "",
        "last_name"             : l.get("last_name","") or " ".join((l.get("name","") or "").split()[1:]),
        "email"                 : l.get("email",""),
        "company"               : l.get("company",""),
        "title"                 : l.get("title",""),
        "phone"                 : l.get("phone",""),
        "website"               : l.get("website",""),
        "city"                  : l.get("city",""),
        "country"               : l.get("country","") or "Unknown",
        "status"                : "New",
        "priority_score"        : score,
        "outsourcing_likelihood": likelihood,
        "pitch_angle"           : f"LeadGen auto-found | Score: {score}/100 | Source: {l.get('source','')}",
        "linkedin_url"          : l.get("linkedin_url",""),
        "description"           : (
            f"Source: {l.get('source','')} | "
            f"Query: {l.get('search_query','')} | "
            f"LeadGen score: {score}/100"
        ),
    }


# ── Team Management ───────────────────────────────────────────────────────────

@app.route("/team")
@login_required
def team_page():
    if session.get("team_role") != "admin":
        flash("Admin access required.", "danger")
        return redirect(url_for("dashboard"))
    users = tm.get_all_users()
    return render_template("team.html", users=users, roles=tm.ROLES,
                           current_role=session.get("team_role","viewer"))

@app.route("/team/create", methods=["POST"])
@login_required
def team_create_user():
    if session.get("team_role") != "admin":
        return jsonify({"error": "forbidden"}), 403
    data = request.get_json() or {}
    try:
        new_id = tm.create_user(
            data.get("username","").strip().lower(),
            data.get("display_name","").strip(),
            data.get("password","").strip(),
            data.get("role","viewer"),
            data.get("email","").strip(),
            data.get("avatar_color","#888"),
        )
        tm.post_system_message("general",
            f"👋 {data.get('display_name','')} joined as {data.get('role','viewer')}.", "leadgen")
        return jsonify({"ok": True, "id": new_id})
    except Exception as e:
        return jsonify({"error": str(e)}), 400

@app.route("/team/update/<int:user_id>", methods=["POST"])
@login_required
def team_update_user(user_id):
    if session.get("team_role") != "admin":
        return jsonify({"error": "forbidden"}), 403
    data    = request.get_json() or {}
    allowed = ["display_name","role","email","avatar_color","is_active"]
    fields  = {k: v for k, v in data.items() if k in allowed}
    if "password" in data and data["password"]:
        fields["password"] = data["password"]
    if fields:
        tm.update_user(user_id, fields)
    return jsonify({"ok": True})

@app.route("/team/users.json")
@login_required
def team_users_json():
    return jsonify({"users": tm.get_all_users()})


# ── LinkedIn OAuth ────────────────────────────────────────────────────────────

@app.route("/linkedin/auth")
@login_required
def linkedin_auth():
    """Redirect user to LinkedIn OAuth consent screen."""
    from scrapers.linkedin_scraper import get_auth_url
    return redirect(get_auth_url())

@app.route("/linkedin/callback")
@login_required
def linkedin_callback():
    """Handle LinkedIn OAuth callback, store token in DB."""
    code  = request.args.get("code","")
    error = request.args.get("error","")
    if error:
        flash(f"LinkedIn auth failed: {error}", "danger")
        return redirect(url_for("settings_page"))
    if not code:
        flash("No authorization code received from LinkedIn.", "danger")
        return redirect(url_for("settings_page"))

    from scrapers.linkedin_scraper import exchange_code
    import time
    data = exchange_code(code)
    if data.get("access_token"):
        token      = data["access_token"]
        expires_in = data.get("expires_in", 3600)
        expires_at = time.time() + expires_in
        db.set_config("LINKEDIN_ACCESS_TOKEN", token)
        db.set_config("LINKEDIN_TOKEN_EXPIRES", str(expires_at))
        flash("LinkedIn connected successfully! Token stored.", "success")
    else:
        flash("LinkedIn token exchange failed. Check client ID/secret.", "danger")
    return redirect(url_for("settings_page"))


# ── API ────────────────────────────────────────────────────────────────────────

@app.route("/api/stats")
@login_required
def api_stats():
    return jsonify(db.get_stats())


# ── Background Search Job ─────────────────────────────────────────────────────

def _run_search_job(job_id: int, query: str, sources: list,
                    country: str = "", max_results: int = 50):
    with app.app_context():
        db.update_job(job_id, status="running",
                      started_at=datetime.utcnow().isoformat())
        all_raw = []
        try:
            if "google" in sources:
                from scrapers.google_scraper import search as gsearch
                all_raw += gsearch(query, max_results=max_results)

            if "buildingsmart" in sources:
                from scrapers.directory_scraper import scrape_buildingsmart
                all_raw += scrape_buildingsmart(query)

            if "enr" in sources:
                from scrapers.directory_scraper import scrape_enr
                all_raw += scrape_enr(query)

            if "rics" in sources:
                from scrapers.directory_scraper import scrape_rics
                all_raw += scrape_rics(query, country=country)

            if "scholar" in sources:
                from scrapers.directory_scraper import scrape_scholar
                all_raw += scrape_scholar(query)

            if "linkedin" in sources:
                from scrapers.linkedin_scraper import search_people
                all_raw += search_people(query, location=country, limit=max_results)

            new_count = 0
            for raw in all_raw:
                email   = raw.get("email","")
                company = raw.get("company","")
                name    = raw.get("name","")
                if db.is_duplicate(email, company, name):
                    continue
                s, breakdown = score_lead(raw)
                raw["score"]           = s
                raw["score_breakdown"] = json.dumps(breakdown)
                raw["search_query"]    = query
                raw["job_id"]          = job_id
                db.insert_lead(raw)
                new_count += 1

            db.update_job(job_id,
                          status="done",
                          leads_found=len(all_raw),
                          leads_new=new_count,
                          finished_at=datetime.utcnow().isoformat())
            log.info("Job %d done: %d found, %d new", job_id, len(all_raw), new_count)

        except Exception as exc:
            db.update_job(job_id, status="error",
                          error_msg=str(exc)[:500],
                          finished_at=datetime.utcnow().isoformat())
            log.error("Job %d failed: %s", job_id, exc)


# ── Scheduler ─────────────────────────────────────────────────────────────────

from apscheduler.schedulers.background import BackgroundScheduler
_scheduler = BackgroundScheduler(daemon=True)
_scheduler.start()
log.info("APScheduler started")


# ── Init ──────────────────────────────────────────────────────────────────────

db.init_db()
log.info("Database initialised")


# ── Entry point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    port = int(os.getenv("LG_PORT", 5001))
    print(f"\n{'='*50}")
    print("  BIM Lead Generator")
    print(f"  Open: http://localhost:{port}")
    print(f"  Login: {ADMIN_USER} / {ADMIN_PASS}")
    print(f"{'='*50}\n")
    app.run(debug=True, host="0.0.0.0", port=port)
