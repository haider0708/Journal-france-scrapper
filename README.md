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
 GitHub Actions (cron)            Render free web service              External Postgres (Neon)
 ───────────────────              ───────────────────────              ────────────────────────
  daily POST /scan  ───────────►  FastAPI                                processed_editions
  (Bearer CRON_SECRET)            ├─ /healthz  /status                   match_log
                                  └─ /scan → background thread:
                                       PISTE API → match → Resend email
                                                  │
                                                  └── reads/writes state ──►
```

**Why this shape?** Render's free tier has an *ephemeral filesystem* (local files
vanish on every restart/redeploy/spin-down) and *no free cron jobs or background
workers*. So state lives in an external Postgres, and an external scheduler pings
an HTTP endpoint. The endpoint returns immediately (`202`) and scans in a
background thread, committing state per-edition so a spin-down mid-scan just
resumes on the next trigger.

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
render.yaml        Render Blueprint (Infrastructure as Code)
.github/workflows/trigger.yml   scheduled trigger
Dockerfile         optional container build
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

## Deploy to Render (free)

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
