# Legifrance JO Monitor

Watches the French **Journal Officiel** (JO) and emails you when a given name
appears in a newly-published edition.

Built on the **official DILA / PISTE Legifrance API** (Licence Ouverte 2.0) —
not HTML scraping. Structured data, government-sanctioned, won't get blocked or
break when the website changes.

- **Smart name matching** — accent/case-insensitive, word-order agnostic
  (`MARSAOUI Lobna` == `Lobna Marsaoui`), and flags single-token hits
  (first-name-only / surname-only) as **PARTIAL** so you can judge them.
- **Incremental** — remembers processed editions; each run only handles newly
  published ones, and is **resumable** if interrupted mid-scan.
- **Email alerts** via [Resend](https://resend.com).
- **Deployable on Render's free tier** as a small FastAPI service, triggered on
  a schedule by GitHub Actions (or cron-job.org).

---

## Architecture

```
        ┌─────────────────────── one Docker network ───────────────────────┐
        │                                                                   │
        │   app  (FastAPI + internal scheduler)         db  (PostgreSQL)    │
        │   ├─ /healthz  /status                        processed_editions  │
        │   ├─ POST /scan  (manual, token-protected)    match_log           │
        │   └─ every N min → scan:                          ▲               │
        │        PISTE API → match → Resend email           │ state         │
        │                       └──────────── reads/writes ─┘               │
        │                                              (volume: pgdata)     │
        └───────────────────────────────────────────────────────────────────┘
```

Two deployment modes, same code:

- **VPS / Docker (recommended):** `docker compose up` runs the app **and**
  PostgreSQL together. The app's **internal scheduler** triggers scans itself —
  no external cron. State persists in the `pgdata` volume.
- **Render free:** the filesystem is ephemeral and there are no free cron jobs,
  so set `SCHEDULER_ENABLED=false`, point `DATABASE_URL` at a managed Postgres
  (e.g. Neon), and let the included GitHub Actions workflow `POST /scan` daily.

Either way the scan runs in a background thread and commits state **per-edition**,
so an interrupted run just resumes on the next trigger.

---

## Project layout

```
app/
  config.py        env-driven settings (pydantic-settings)
  legifrance.py    PISTE API client + JSON helpers
  matching.py      name matching (normalize / tokenize / find / snippet)
  storage.py       state backends: PostgresStorage | FileStorage
  notifier.py      Resend email
  monitor.py       run_scan() orchestration
  main.py          FastAPI app (/, /healthz, /status, /scan)
  cli.py           local runner:  python -m app.cli
tests/             pytest suite (matching + storage)
docker-compose.yml app + PostgreSQL, for VPS deployment
Dockerfile         container image
render.yaml        Render Blueprint (alternative host)
.github/workflows/trigger.yml   scheduled trigger (Render mode)
```

---

## Local development

```bash
pip install -r requirements-dev.txt
cp .env.example .env            # then edit (Windows: copy)
python -m app.cli               # one-off scan, prints a JSON summary
pytest                          # run tests
```

With `DATABASE_URL` empty, state is kept in `state.json` / `matches.jsonl`.

Run the web service locally:

```bash
uvicorn app.main:app --reload
curl -X POST localhost:8000/scan -H "Authorization: Bearer $CRON_SECRET"
curl localhost:8000/status
```

---

## Deploy on a VPS with Docker (recommended)

Everything (app + PostgreSQL) runs in containers. Postgres data lives in a
persistent volume, and the app schedules its own scans.

### 1. Prerequisites
- A free **PISTE** Client ID/Secret — [piste.gouv.fr](https://piste.gouv.fr), subscribe an app to *Légifrance*.
- A free **Resend** API key — [resend.com](https://resend.com).
- Docker + Docker Compose on the VPS.

### 2. On the VPS
```bash
git clone https://github.com/haider0708/Journal-france-scrapper.git
cd Journal-france-scrapper

cp .env.example .env
nano .env        # fill PISTE_*, SEARCH_NAMES, RESEND_API_KEY, EMAIL_TO,
                 # and a strong POSTGRES_PASSWORD

docker compose up -d --build
docker compose logs -f app      # watch it authenticate, scan, and alert
```

That's it. The app:
- creates its DB schema automatically on first boot,
- backfills the last `LOOKBACK_EDITIONS` editions immediately (`SCAN_ON_STARTUP=true`),
- then re-scans every `SCAN_INTERVAL_MINUTES` (default 720 = twice a day),
- emails you on every match.

### 3. Check / operate
```bash
curl localhost:8000/status                 # last run, stats, recent matches
curl -X POST localhost:8000/scan \         # force a scan now
  -H "Authorization: Bearer <CRON_SECRET>"
docker compose down                        # stop (data kept in the volume)
docker compose pull && docker compose up -d --build   # update after git pull
```

> The DB is only reachable inside the Docker network. Expose port 8000 only if
> you need the API externally (put it behind a reverse proxy / firewall).
> `/status` is unauthenticated — keep it internal or add a proxy auth layer.

---

## Deploy to Render (free) — alternative

### 1. Get the prerequisites (all free)
- **PISTE** Client ID/Secret — [piste.gouv.fr](https://piste.gouv.fr), subscribe an app to *Légifrance*.
- **Resend** API key — [resend.com](https://resend.com).
- **Postgres** — create a free database at [neon.tech](https://neon.tech) and copy
  its connection string (the `postgresql://...?sslmode=require` one).

### 2. Push this repo to GitHub.

### 3. Create the service on Render
- Render → **New +** → **Blueprint** → pick this repo. It reads `render.yaml`.
- When prompted, fill the secret env vars: `PISTE_CLIENT_ID`, `PISTE_CLIENT_SECRET`,
  `SEARCH_NAMES`, `RESEND_API_KEY`, `EMAIL_TO`, `DATABASE_URL`.
- `CRON_SECRET` is auto-generated — open the service’s **Environment** tab and
  copy its value for the next step.

### 4. Schedule the trigger (GitHub Actions)
In the GitHub repo → **Settings → Secrets and variables → Actions**, add:
- `SERVICE_URL` → e.g. `https://legifrance-monitor.onrender.com`
- `CRON_SECRET` → the value Render generated

The workflow in `.github/workflows/trigger.yml` then POSTs `/scan` daily at
07:30 UTC (and you can run it manually from the **Actions** tab).

> No-code alternative: [cron-job.org](https://cron-job.org) — create a job that
> does `POST {SERVICE_URL}/scan` with header `Authorization: Bearer {CRON_SECRET}`.

### 5. Verify
- `GET {SERVICE_URL}/` → service info
- `GET {SERVICE_URL}/status` → backend stats + recent matches
- Trigger once manually; the **first run backfills** `LOOKBACK_EDITIONS` editions
  (~10–15 min). If a spin-down interrupts it, the next trigger resumes — state is
  saved per edition.

---

## Configuration reference

| Variable | Required | Default | Notes |
|---|---|---|---|
| `PISTE_CLIENT_ID` / `PISTE_CLIENT_SECRET` | ✅ | — | PISTE OAuth credentials |
| `PISTE_SANDBOX` | | `false` | use PISTE sandbox env |
| `SEARCH_NAMES` | ✅ | — | `;`-separated names |
| `LOOKBACK_EDITIONS` | | `15` | recent editions to consider |
| `REQUEST_DELAY_S` | | `0.3` | politeness delay between API calls |
| `EMAIL_ENABLED` | | `false` | set `true` to send alerts |
| `RESEND_API_KEY` | when email on | — | Resend key |
| `EMAIL_FROM` | | `onboarding@resend.dev` | verified sender or Resend sandbox |
| `EMAIL_TO` | when email on | — | `,`-separated recipients |
| `DATABASE_URL` | ✅ on Render | _(empty=file)_ | Postgres DSN; empty → local files |
| `CRON_SECRET` | ✅ for `/scan` | — | shared secret for the trigger |
| `LOG_LEVEL` | | `INFO` | |

---

## Notes
- `EMAIL_FROM=onboarding@resend.dev` works immediately but Resend only delivers
  to your own account address until you verify a domain; first emails may land in
  spam. Verify a domain in Resend for production-grade delivery.
- The API's JSON field names vary across versions; the client is written
  defensively and logs a warning with the keys it saw if it meets an unknown shape.
