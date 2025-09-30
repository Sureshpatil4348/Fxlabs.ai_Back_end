### Multi‑tenant plan: FXLabs (India) and HexTech (Dubai)

Goal: Run two independent but feature‑identical sites with strict isolation:
- **FXLabs (India)**: own Supabase project, users, alerts; daily brief at 09:00 IST (`Asia/Kolkata`). All alert emails for FXLabs display times in IST. If ZoneInfo is unavailable on host, a fixed +05:30 fallback is used.
- **HexTech (Dubai)**: own Supabase project, users, alerts; daily brief at 09:00 Dubai time (`Asia/Dubai`)

No code changes done yet. This document maps the system to tenancy touchpoints and proposes the safest approach.

---

### Recommendation
- **Two deployments (hard multi‑tenancy, recommended now)**
  - Deploy the same codebase twice with separate environment variables, domains, Supabase projects, and SendGrid senders.
  - Pros: maximum isolation, zero cross‑tenant risk, minimal refactor, fastest time‑to‑ship.
  - Cons: duplicated ops (two processes), later brand theming still needed for email HTML.

---

### Tenancy touchpoints in current code

- Supabase credentials (now tenant-aware via `app/tenancy.py` and `app/config.py`):
  - `app/tenancy.py`: central tenant resolver; entry scripts set tenant; per-tenant env overrides supported
  - `app/config.py`: exposes `SUPABASE_URL`, `SUPABASE_SERVICE_KEY` from tenant config
  - All services now import from `app.config` instead of reading env directly
  - Daily/news schedulers fetch users from `auth.admin` using the service key

- Schedulers (single instance today, no tenant context):
  - `server.py` creates: `news.news_scheduler()`, `news.news_reminder_scheduler()`, `daily_mail_scheduler()`
  - `app/daily_mail_service.py` uses `DAILY_TZ_NAME` + `DAILY_SEND_LOCAL_TIME` (global)

- Email branding (FXLabs by default; HexTech placeholders pending):
  - `app/email_service.py` still renders FXLabs brand in templates. HexTech branding swap is a TODO when HexTech goes live.
  - `FROM_EMAIL`/`FROM_NAME` are tenant‑aware via `app/config.py`.

- CORS / API token (global today):
  - `ALLOWED_ORIGINS`, `API_TOKEN` are single values; would be per tenant in soft multi‑tenancy

---

### Option A — Two deployments (hard multi‑tenancy)

Run two instances of the backend with separate env files and domains.

- Domains & ingress
  - FXLabs: `api.fxlabs.ai` (already configured in `config.yml`)
  - HexTech: add a second hostname (e.g., `api.hextech.ae`) to Cloudflare Tunnel or a separate tunnel file

- Environment per deployment
  - Supabase: `SUPABASE_URL`, `SUPABASE_SERVICE_KEY` point to each tenant’s project
  - Email: `SENDGRID_API_KEY`, `FROM_EMAIL`, `FROM_NAME` set to brand‑aligned sender
  - Scheduling: `DAILY_TZ_NAME=Asia/Kolkata` (FXLabs), `DAILY_TZ_NAME=Asia/Dubai` (HexTech); both `DAILY_SEND_LOCAL_TIME=09:00`
  - API/CORS: distinct `API_TOKEN`, `ALLOWED_ORIGINS` filtered to the tenant’s frontend(s)
  - Branding (temporary): keep FXLabs HTML as‑is for FXLabs; for HexTech, when we code it, swap brand strings/logo via env

- Supabase schema
  - Apply the same SQL schemas to the HexTech project:
    - `supabase_rsi_tracker_alerts_schema.sql`
    - `supabase_rsi_correlation_tracker_alerts_schema.sql`
    - `supabase_heatmap_tracker_alerts_schema.sql`
    - `supabase_heatmap_indicator_tracker_alerts_schema.sql`
  - Auth: users live in each Supabase project’s Auth; daily/news emails query that tenant’s `auth.admin` API

- Pros/cons
  - Pros: simplest, safest isolation; no risk of cross‑tenant emails/alerts; no refactor needed to schedulers/caches immediately
  - Cons: duplicate operational footprint; HexTech branding in email HTML still needs a later code change

#### Single VPS topology (same venv)
- One repo and one venv are fine. Run two `server.py` processes on different ports; isolation comes from per‑process environment.
- Example ports:
  - FXLabs → `:8000`
  - HexTech → `:8001`

#### Minimal env files (examples)
```env
# .env.fxlabs
API_TOKEN=fxlabs_api_token
ALLOWED_ORIGINS=https://app.fxlabs.ai
SUPABASE_URL=https://<fxlabs>.supabase.co
SUPABASE_SERVICE_KEY=eyJ...
SENDGRID_API_KEY=SG....
FROM_EMAIL=alerts@fxlabs.ai
FROM_NAME=FX Labs Alerts
DAILY_TZ_NAME=Asia/Kolkata
DAILY_SEND_LOCAL_TIME=09:00
HOST=127.0.0.1
PORT=8000
```

```env
# .env.hextech
API_TOKEN=hextech_api_token
ALLOWED_ORIGINS=https://app.hextech.ae
SUPABASE_URL=https://<hextech>.supabase.co
SUPABASE_SERVICE_KEY=eyJ...
SENDGRID_API_KEY=SG....
FROM_EMAIL=alerts@hextech.ae
FROM_NAME=HexTech Alerts
DAILY_TZ_NAME=Asia/Dubai
DAILY_SEND_LOCAL_TIME=09:00
HOST=127.0.0.1
PORT=8001
```

#### Run commands (two processes)
```bash
# Terminal 1 (FXLabs)
python fxlabs-server.py

# Terminal 2 (HexTech)
python hextech-server.py
```

#### Cloudflare Tunnel (reverse proxy) example
Update `config.yml` ingress to route both hostnames:
```yaml
ingress:
  - hostname: api.fxlabs.ai
    service: http://127.0.0.1:8000
  - hostname: api.hextech.ae
    service: http://127.0.0.1:8001
  - service: http_status:404
```

#### Optional: systemd units
```ini
# /etc/systemd/system/fxlabs.service
[Unit]
Description=FXLabs Backend
After=network.target

[Service]
WorkingDirectory=/path/to/Fxlabs.ai_Back_end
EnvironmentFile=/path/to/.env.fxlabs
ExecStart=/path/to/.venv/bin/python server.py
Restart=always

[Install]
WantedBy=multi-user.target
```

```ini
# /etc/systemd/system/hextech.service
[Unit]
Description=HexTech Backend
After=network.target

[Service]
WorkingDirectory=/path/to/Fxlabs.ai_Back_end
EnvironmentFile=/path/to/.env.hextech
ExecStart=/path/to/.venv/bin/python server.py
Restart=always

[Install]
WantedBy=multi-user.target
```

#### Operational checks
- Health: `curl https://api.fxlabs.ai/health` and `https://api.hextech.ae/health`
- News cache and reminders running (logs): per‑tenant schedulers should log independently
- Daily brief timing: 09:00 IST vs 09:00 Asia/Dubai, verify subject/body headers show correct TZ labels
- Alert triggers: verify rows insert into each tenant’s Supabase trigger tables (no cross‑pollination)

#### Resource notes
- Two processes duplicate MT5, caches, schedulers. Monitor CPU/RAM. If MT5 client concurrency is limited by the terminal, prefer staggered startup or consider a shared MT5 gateway later.

#### Backup & recovery
- Supabase: enable point‑in‑time or scheduled backups per project
- `.env.*`: store in a secrets manager; avoid committing keys
- SendGrid: separate sender identities and API keys per brand; rotate independently

---

### Configuration checklists

- FXLabs (deployment/env)
  - `SUPABASE_URL`, `SUPABASE_SERVICE_KEY` → FXLabs project
  - `SENDGRID_API_KEY`, `FROM_EMAIL=alerts@fxlabs.ai`, `FROM_NAME="FX Labs Alerts"`
  - `DAILY_TZ_NAME=Asia/Kolkata`, `DAILY_SEND_LOCAL_TIME=09:00`
  - `API_TOKEN=<fxlabs>`, `ALLOWED_ORIGINS=https://app.fxlabs.ai`

- HexTech (deployment/env)
  - `SUPABASE_URL`, `SUPABASE_SERVICE_KEY` → HexTech project
  - `SENDGRID_API_KEY`, `FROM_EMAIL=alerts@hextech.ae`, `FROM_NAME="HexTech Alerts"`
  - `DAILY_TZ_NAME=Asia/Dubai`, `DAILY_SEND_LOCAL_TIME=09:00`
  - `API_TOKEN=<hextech>`, `ALLOWED_ORIGINS=https://app.hextech.ae`

---

### Security & isolation guardrails

- **Keys never shared**: distinct Supabase service keys and SendGrid keys per tenant; store in separate envs/secret scopes
- **Per‑tenant boundaries**: no cross‑tenant caching; no shared user_id spaces (prefix or scope by tenant internally)
- **CORS lockdown**: only allow the brand’s frontend origins
- **Auth tokens**: separate `API_TOKEN` per tenant; do not reuse
- **Brand‑safe sending**: send from a domain authenticated for each brand (SPF/DKIM/DMARC); avoid cross‑brand headers
- **Logging**: always include `tenant`, mask secrets; never log full tokens/keys

---

### Supabase schemas and Auth

- Use the existing schema files to provision each project; schema must be kept identical across tenants
- Daily brief and news reminders fetch recipients from each tenant’s `auth.admin` API (service key scope)
- Alert triggers are inserted into the tenant’s own tables; dashboards query only their project

---

### Rollout plan

1) Create HexTech Supabase project; apply the four `supabase_*.sql` schema files
2) Configure SendGrid (domain auth) for `hextech.ae`; set API key and sender
3) Add HexTech ingress/domain; spin up second deployment with HexTech env
4) Validate: health, WebSocket, alerts eval, daily/news emails at the right local time
5) (Optional) Implement branding parameterization in `app/email_service.py` and header assets
6) (Optional) Move to single‑process soft multi‑tenancy if desired; complete refactors listed above

---

### Testing checklist

- Timezones: compute next run for `Asia/Kolkata` and `Asia/Dubai` → 09:00 local
- Brand headers: email HTML shows correct brand name, color, logo, and TZ label
- Isolation: verify FXLabs users never receive HexTech emails and vice‑versa
- Alerts: triggers log to the correct Supabase tables per tenant
- CORS/auth: each frontend can call only its tenant API with its `API_TOKEN`

---

### Open questions

- Confirm HexTech domain(s) to allow in CORS (e.g., `https://app.hextech.ae`)
- Do we want a single SendGrid account with subusers per brand, or separate accounts?
- Any brand‑specific feature deltas (subject prefixes, footer disclaimers, logo)?
- Data residency or compliance differences between India and UAE to enforce at the database level?


