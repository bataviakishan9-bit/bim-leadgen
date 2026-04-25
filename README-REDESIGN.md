# BIM LeadGen · Blacksmith v2.0 Redesign

Premium black + heritage-gold + cyan-accent theme with the BIM ribbon logo, matching the CRM and Mobile app design language. Drop-in deployment, zero route or schema changes.

---

## 1 · What shipped

```
BIM_LeadGen/
├── static/
│   ├── css/
│   │   └── bim-v2.css                 NEW  — shared design system (copy of CRM)
│   └── img/
│       ├── bim-mark.svg               NEW  — ribbon "b" monogram (chrome gradient)
│       ├── favicon.svg                NEW  — 64×64 rounded favicon
│       └── LOGO-PREVIEW.html          NEW  — visual QA grid
├── templates_v2/                      NEW  — full premium redesign
│   ├── base.html                        layout · sidebar · topbar · cyan accent
│   ├── login.html                       hero + auth card (cyan submit)
│   ├── dashboard.html                   KPIs · Recent Jobs · Top Leads
│   ├── search.html                      query builder · source grid · presets
│   ├── jobs.html                        search job queue + auto-refresh
│   ├── job_detail.html                  run detail · leads harvest
│   ├── leads.html                       table · bulk actions · filters · pagination
│   ├── lead_detail.html                 dossier · score breakdown · notes
│   ├── sync.html                        CRM push · filter · audit trail
│   ├── settings.html                    API keys · diagnostics · score tiers
│   └── team.html                        roster · role legend · add/edit modal
└── README-REDESIGN.md                 NEW  — this file
```

**Originals in `templates/` are untouched.** Deploy is non-destructive.

---

## 2 · Design language

| Element         | Value                                                |
| --------------- | ---------------------------------------------------- |
| Background      | pure black (`--bg-0: #050506`)                       |
| Primary accent  | heritage gold (`--gold: #E8B84C`)                    |
| LeadGen accent  | cyan (`--cyan: #22D3EE`) — hero + sync + submit     |
| Display font    | Space Grotesk 600/700                                |
| Body font       | Inter 400/500                                        |
| Mono            | JetBrains Mono — eyebrows, stats, IDs                |
| Radius          | 14px cards · 10px controls · 99px chips              |
| Shadow          | layered soft-diffusion (`--sh-md`, `--sh-xl`)        |
| Logo            | chrome-gradient Möbius "b" · auto-swaps PNG if present |

The **cyan accent** is what distinguishes LeadGen visually from CRM (gold) — same family, different personality. Heading gradients mix cyan → gold in hero sections to bridge both products.

---

## 3 · Deploy

### Option A · rename (recommended once verified)

```powershell
cd C:\Users\Kishan\BIM_LeadGen
Rename-Item templates templates_v1_legacy
Rename-Item templates_v2 templates
# restart Flask
```

### Option B · override via Flask config

```python
# in app.py
app.template_folder = "templates_v2"
```

### Option C · Jinja loader (A/B testing)

Keep both folders, point `FileSystemLoader` at `templates_v2` — flip back by changing one line.

No route changes. All `url_for(...)` endpoints are preserved exactly:
- `dashboard`, `search_page`, `run_search`
- `jobs_page`, `job_detail`
- `leads_page`, `lead_detail`, `bulk_action`
- `sync_page`, `run_sync`
- `settings_page`, `linkedin_auth`
- `team_page`, `team/create`, `team/update/<id>`
- `login`, `logout`

---

## 4 · Logo drop-in

The ribbon "b" is currently an SVG recreation. Whenever you're ready to use your exact PNG:

1. Save your ribbon mark (transparent PNG, 512×512+) to:
   ```
   C:\Users\Kishan\BIM_LeadGen\static\img\bim-mark.png
   ```
2. Refresh — every `<img>` tag has `onerror="this.src='bim-mark.png'"` so the PNG takes over automatically with no code edits.
3. Favicon variant at `static/img/favicon.png` (64×64) also auto-swaps.

Preview your logo at all sizes: open `static/img/LOGO-PREVIEW.html` in a browser.

---

## 5 · What's inside each page

**login.html** — Split hero (left) + glass auth card (right). Cyan eyebrow · cyan-gradient submit button · three-column stats strip (`6 Data Sources · ∞ Queries · 1-Click Sync`). Ribbon watermark behind headline at 5% opacity.

**dashboard.html** — 4 KPI cards (Total / Pending / Approved / Hot) with left-accent borders · 3 secondary metric cards (Emails · Synced · Jobs Running with pulse) · two-column table section (Recent Jobs + Top Leads) · Active Sources strip at bottom · auto-refreshes every 10s while jobs are running.

**search.html** — Large radar-prefixed query input · Country + Max Results selects · source grid of 6 labelled chips (Google, BuildingSMART, RICS, ENR, Scholar, LinkedIn) using `:has(input:checked)` highlight · Quick Queries pill presets · Search Craft tips sidebar.

**jobs.html** — Job queue table with pulsing RUNNING chip animation · auto-refresh every 5s if any job is running · empty state with radar icon.

**job_detail.html** — Parameter list (query, sources, country, start/finish) · big leads-generated orb · leads harvest table · error alert if `job.error_msg` · auto-refresh while running.

**leads.html** — Bulk action toolbar (Approve · Reject · Sync) reveals on selection · filter card (q, status, tier, source) · table with email-verified badge, email-type chip, score tier chip (danger/warn/gold/cyan at 85/70/55), synced checkmark · paginated with page-window navigation.

**lead_detail.html** — Dossier card (full contact tree) · large score orb in header · score breakdown with hits/bonuses/penalties · review actions bar (Approve/Reject/Enrich/Sync) · notes journal · raw data viewer.

**sync.html** — CRM URL status row (green/rose) · pending sync count in large gold display · sync filter select · audit trail table with success/failed chips · 5-step "How Sync Works" sidebar with gold circle badges.

**settings.html** — API key form (Hunter.io · LinkedIn · CRM URL · CRM Password) · API diagnostics panel with live status chips · score tiers reference with weight rules · "Connect LinkedIn" button when client ID is set.

**team.html** — Roster table with avatar orbs · role legend chips in glass header · admin-only add/edit modal with avatar color picker (10 colors) · role-based access badges · live fetch-based create/update.

---

## 6 · Preserved context variables

Every template honors the exact Jinja contract of the originals — no view function changes needed:

- `_current_user.display_name`, `.username`, `.role`, `.avatar_color`
- `_team_role` (for admin-only nav/actions)
- `_ROLES` dict with `.color` and `.label`
- `stats.total`, `.pending`, `.approved`, `.hot`, `.with_email`, `.synced`, `.jobs_running`
- `leads`, `jobs`, `recent_jobs`, `top_leads`
- `filters.q`, `.status`, `.tier`, `.source`, `.job_id`
- `page`, `per_page`, `total`, `total_pages`, `qs`
- `lead.*` — id, company, name, title, email, email_verified, email_type, phone, website, linkedin_url, country, source, source_url, search_query, score, review_status, synced_to_crm, notes, raw_data, created_at
- `job.*` — id, query, sources, status, leads_found, error_msg, country, created_at, finished_at
- `score_breakdown` — hits[], bonuses[], penalties[], kw_total, country_bonus, combo_total, contact_bonus
- `config.*` — HUNTER_API_KEY, LINKEDIN_CLIENT_ID, LINKEDIN_CLIENT_SECRET, CRM_URL, CRM_PASSWORD
- `crm_url`, `pending_sync`, `already_synced`, `sync_log`
- `linkedin_enabled`, `sources`, `users`, `error`

---

## 7 · Rollback

```powershell
cd C:\Users\Kishan\BIM_LeadGen
# If you renamed:
Rename-Item templates templates_v2
Rename-Item templates_v1_legacy templates
# If you used Option B/C: revert the app.py / loader change.
```

New assets (`static/css/bim-v2.css`, `static/img/bim-mark.svg`, `favicon.svg`) are additive and safe to leave in place.

---

## 8 · Three products · one brand

| Product         | Accent      | Location                         |
| --------------- | ----------- | -------------------------------- |
| BIM CRM         | Gold        | `C:\Users\Kishan\BIM_CRM`        |
| BIM LeadGen     | Cyan + Gold | `C:\Users\Kishan\BIM_LeadGen`    |
| BIM Mobile      | Gold        | `C:\Users\Kishan\BIM_Mobile`    |

Same ribbon logo · same typography · same black stage · distinct accent per product. Opening any of the three feels like walking into the same building.
