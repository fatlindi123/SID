# SID

SID helps teams explore web applications, save evidence about their pages and interactions, and use that evidence for analysis, test cases, workflow intelligence, a knowledge graph, and immutable reports.

## Project layout

- `src/` — React 19 frontend, API clients, and workspace components.
- `backend/app/api/v1/` — FastAPI endpoints.
- `backend/app/core/` — settings, URL security, logging, and error handling.
- `backend/app/db/`, `models/`, and `schemas/` — SQLAlchemy database layer and API contracts.
- `backend/app/services/` — browser exploration, safe egress, background workers, analysis, graph, workflow, project, test execution, and report services.
- `backend/alembic/versions/` — additive database migrations; current head is `0014_remove_google_identity`.
- `backend/tests/` — API, service, migration, and controlled local Chromium tests.
- `make/` — Figma Make scripts and site configuration. The React source remains in `src/`.

## Requirements

- Python 3.11 or newer and PostgreSQL 15 or newer.
- Node.js 22 and pnpm 10 (see `.mise.toml`).
- Playwright Chromium for browser exploration, test execution, interaction fixtures, and PDF export.

## Local setup

Create a PostgreSQL database and a role with the required table and migration permissions. Copy `backend/.env.example` to `backend/.env` and set a valid `DATABASE_URL`. Keep real credentials and AI keys only in local environment configuration.

In PowerShell, install the backend and browser dependencies and apply migrations:

```powershell
cd backend
py -3.11 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
$env:PLAYWRIGHT_BROWSERS_PATH = ".cache/playwright"
.\.venv\Scripts\python.exe -m playwright install chromium
.\.venv\Scripts\alembic.exe upgrade head
```

Start the API in one terminal. The application starts the exploration, AI analysis, and test execution workers inside this single API process:

```powershell
cd backend
$env:PLAYWRIGHT_BROWSERS_PATH = ".cache/playwright"
.\.venv\Scripts\uvicorn.exe app.main:app --host 127.0.0.1 --port 8000
```

In a second terminal, install the frontend dependencies and start Vite:

```powershell
pnpm.cmd install --frozen-lockfile
pnpm.cmd run dev
```

The local API health endpoint is `http://127.0.0.1:8000/api/v1/health`; interactive API documentation is at `/docs`. Vite normally serves the app at `http://localhost:8443` in the Figma Make setup. Configure those local origins in `backend/.env` with `CORS_ORIGINS` as a JSON array; an empty list is the backend default.

For local frontend builds, the API defaults to `http://127.0.0.1:8000/api/v1`. Production builds require an explicit HTTPS `VITE_API_BASE_URL`; use the root `.env.example` as a template, replace its example host, and build with that value available to Vite. Never ship a build that targets localhost.

## Database and browser setup

Run `.\.venv\Scripts\alembic.exe current` and `.\.venv\Scripts\alembic.exe history` from `backend/` to inspect the database revision and migration chain. Apply schema changes with `.\.venv\Scripts\alembic.exe upgrade head` before starting the application. The app does not apply migrations automatically. Back up PostgreSQL with `pg_dump -Fc`; rehearse restores into a separate, empty database before using a backup for recovery. Do not use a destructive restore against the active database.

The configured database role may lack `REFERENCES` permission for legacy tables. In those areas, services validate project, exploration, page, and evidence associations in application code. Review `backend/README.md` for the API contracts and exact safeguards.

The browser engine accepts public HTTP/HTTPS targets only. It validates DNS results and sends browser traffic through a loopback egress proxy that pins each connection to a validated public IP. Browser work is bounded by configured page, depth, timeout, and interaction limits. Chromium is installed separately from Python packages; on Linux deployments, install Playwright's system libraries as well as the browser binary. PDF rendering uses the same Playwright Chromium installation and only renders sanitized saved report content locally.

## Implemented workspaces

The dashboard reads saved data for exploration results, AI analysis, persistent test cases and run history, safe interaction observations, the evidence-backed knowledge graph, projects, workflow intelligence, and reports. Settings persist the default landing workspace and page size in the current browser profile and show read-only backend service status. Reports have immutable snapshots with JSON, print, and PDF views. Suggested workflows remain distinguished from observed evidence; scenarios and suggestions are not represented as executed unless a persisted run says so.

The AI provider is optional and configured using `AI_API_BASE_URL`, `AI_MODEL`, and `AI_API_KEY`. The key is loaded on the backend only. AI analysis is not required for browsing saved explorations, projects, graphs, workflows, or reports.

## Tests and build

From the repository root:

```powershell
pnpm.cmd exec tsc --noEmit
$env:VITE_API_BASE_URL = "https://api.example.com/api/v1"
pnpm.cmd run build
```

From `backend/`:

```powershell
.\.venv\Scripts\python.exe -m pytest --basetemp=.pytest-tmp
```

The backend browser tests use controlled local fixtures and do not need access to external target sites. Database migration tests use disposable SQLite databases. See `backend/README.md` for deployment browser-library installation and operational configuration details.

## Security boundaries and release status

The workspace requires a signed-in account. Registration accepts a syntactically valid email as the account identifier and a password of at least seven characters; it does not verify mailbox ownership and signs the account in immediately. Password recovery requires configured SMTP delivery. Sessions are stored server-side and use HttpOnly cookies; workspace records are scoped to the signed-in account. The application has process-local throttling for authentication attempts, but no deployment-wide API rate limiter. CORS is not authentication. Keep the API behind a trusted network boundary and TLS termination, and use a shared abuse-control layer before scaling beyond one API process. Production CORS configuration accepts only explicit HTTPS origins and rejects localhost, loopback, and wildcard origins.

Run Alembic through revision `0014_remove_google_identity` before starting the updated backend. Sign-in uses verified email/password accounts; Google sign-in is not available. Email registration and password recovery require working `SMTP_HOST` and `SMTP_FROM_EMAIL` settings. If SMTP is absent, the UI reports these actions as unavailable and does not create unverified password accounts. New accounts see the welcome experience after their first successful sign-in; completion is persisted by the backend. Existing workspace data is assigned to the first successfully verified account during sign-up; subsequent accounts cannot see that account's records.

The browser workers run in-process with the API and this initial design is for one API process. Pending work is recovered from PostgreSQL on restart; an interrupted browser test run is failed rather than replayed. There are no Docker or CI release configurations in this repository. Production readiness therefore requires an externally managed deployment, persistent PostgreSQL backups, a single worker-bearing API instance (or a future shared queue design), Playwright Chromium and operating-system libraries, and monitoring.

