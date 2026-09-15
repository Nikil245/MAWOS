# MAWOS

MAWOS is a multi-role university ERP research project built with React/Vite, FastAPI REST API, PostgreSQL as the source of truth, JWT authentication, Alembic migrations, Docker deployment options, and an in-app event/notification system. Role-based portals provide secure workflow automation for university users.

## Contents

- [Architecture](#architecture)
- [Implemented modules](#implemented-modules)
- [Roles](#roles)
- [Workflows](#workflows)
- [Local development](#local-development)
- [Docker](#docker)
- [Migrations](#migrations)
- [Testing and quality checks](#testing-and-quality-checks)
- [Operational notes](#operational-notes)
- [Documentation](#documentation)
- [Current limitations and future scope](#current-limitations-and-future-scope)

## Architecture

```mermaid
flowchart LR
  UI[React + Vite] -->|REST /api| API[FastAPI]
  API -->|SQLAlchemy / Psycopg| DB[(PostgreSQL source of truth)]
  API --- AUTH[JWT and role authorization]
  API --- EVENTS[In-app events and recipient-owned notifications]
  MIG[Alembic migrations] --> DB
  DOCKER[Optional Docker Compose] -. deploys .-> UI
  DOCKER -. deploys .-> API
```

The backend also supports SQLite for isolated local/demo and test use. PostgreSQL schema changes are Alembic-managed. The optional academic assistant uses permission-checked, role-scoped tools with deterministic routing, an optional hosted Groq tier for sanitized general learning, and optional local Ollama fallback; it does not mutate records through chat.

## Implemented modules

| Module | Current capability |
|---|---|
| Student portal | Personal dashboard for attendance, marks, fees, hall-ticket status, scholarships, placements, notifications, published timetable, events, and library access. |
| Faculty, attendance and marks | Faculty marks attendance only for assigned subject/sections and enters validated internal marks for authorized sheets. |
| Fees and clearance | Fee records, student payment action, collection/defaulter summaries, and fee-clearance inputs for exam eligibility; not an external payment gateway. |
| Scholarships | Faculty creates/submits department scholarships; HOD reviews the workflow; students see applicable status. |
| Exams and eligibility | Exam schedules, eligibility evaluation, and hall-ticket availability/status from institutional records. |
| Timetable | Admin configures terms, periods, holidays and rooms. HOD configures demand, generates a versioned draft, validates/reviews it, locks entries, and explicitly publishes. Student/faculty views use published schedules. |
| Placement Agent | Admin manages drives, eligibility using hard rules plus model/rules fallback, shortlists and outcomes. Student views expose eligibility/application state. Private drive PDFs are served through authenticated document links when present. |
| Notification Agent | Recipient-owned notifications, unread count, and mark-one/mark-all-read actions. |
| Campus events | Admin creates, edits, publishes, or cancels events with role/department visibility and in-app notifications. |
| Parent portal | Admin-created accounts with explicitly linked, read-only child dashboard, timetable, event, notification, and library views; temporary passwords must change at first login. |
| Library Agent | Catalogue, reservations/slips, librarian physical pickup/return confirmation, seven-day loans, ₹1/day overdue policy, recommendations, and parent summaries. |
| Admissions/Admin | Admin verifies applications, runs merit, allots seats against intake, enrols applicants, and manages parent/librarian accounts. |
| Academic assistant/orchestrator | Authenticated deterministic routing plus optional Groq/local generation for sanitized general-learning turns. |

## Roles

| Role | High-level access |
|---|---|
| Admin | Admissions, institutional configuration/analytics, placements/events, parent/librarian management, and library staff workflows. |
| Student | Own academic, fee, exam, scholarship, placement, timetable, event, notification, and library information/actions. |
| Faculty | Assigned attendance and marks, own timetable, scholarship authoring, and faculty dashboard. |
| HOD | Department dashboard, scholarship review, fee-defaulter view, and department timetable configuration/generation/validation/publication. |
| Principal | Institution dashboard and read-only timetable/department coverage. |
| Parent | Read-only data for active, explicitly linked children; no impersonation or mutation controls. |
| Librarian | Catalogue, reservations, pickups/returns, issue records, fines, and library operations. |

Parent and Librarian accounts are created by Admin; there is no public signup.

## Workflows

- Faculty attendance/marks update authorized institutional records that student and eligibility views read.
- Fees, attendance, and configured checks contribute to exam eligibility and hall-ticket status; schedules are shown to students.
- Faculty drafts/submits scholarships and HOD reviews them; students see the resulting status.
- Admin creates placement drives, evaluates eligibility, then manages shortlists and outcomes.
- Timetables follow configure → generate draft → validate/review → publish. Drafts never replace published views.
- Admin publishes/cancels campus events for configured audiences; notifications are in-app recipient records.
- Students reserve library copies and receive slips. Librarian/Admin confirms physical pickup/return; overdue fines use the configured policy.

## Local development

### Prerequisites

- Git
- Python 3.12 (the backend Docker image uses Python 3.12)
- Node.js 22 and npm (the frontend Docker build uses Node 22)
- PostgreSQL for PostgreSQL development
- Docker Engine with the Compose plugin, optionally

Linux/macOS:

```bash
git clone https://github.com/Nikil245/MAWOS.git mawos
cd mawos
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
cd frontend && npm ci && cd ..
cp .env.example .env
```

Edit the ignored `.env` and generate a unique JWT secret locally. For an existing local PostgreSQL database:

```env
MAWOS_ENV=development
MAWOS_DATABASE_MODE=external
MAWOS_DATABASE_URL=postgresql+psycopg://mawos_app:REPLACE_WITH_PASSWORD@127.0.0.1:5432/mawos
MAWOS_DOCKER_DATABASE_URL=postgresql+psycopg://mawos_app:REPLACE_WITH_PASSWORD@host.docker.internal:5432/mawos
MAWOS_JWT_SECRET=REPLACE_WITH_A_LONG_RANDOM_SECRET
MAWOS_SEED_DEMO_DATA=false
MAWOS_AI_PROVIDER=auto
GROQ_API_KEY=<YOUR_GROQ_API_KEY>
MAWOS_GROQ_BASE_URL=https://api.groq.com/openai/v1
MAWOS_GROQ_MODEL=openai/gpt-oss-20b
MAWOS_GROQ_TIMEOUT_SECONDS=30
MAWOS_GROQ_MAX_TOKENS=180
MAWOS_GROQ_MAX_INPUT_CHARS=6000
MAWOS_GROQ_HEALTH_TTL=30
MAWOS_GROQ_RETRY_COOLDOWN=5
MAWOS_AI_REQUESTS_PER_MINUTE=6
```

Create the development database/roles through your PostgreSQL administration process. Use a database-owner/migration role for DDL and a restricted application role at runtime. MAWOS does not create PostgreSQL tables, seed PostgreSQL, reset a database, or generate timetables on startup.

`MAWOS_DATABASE_MODE=external` selects an existing host PostgreSQL database. Native processes use the loopback URL (`127.0.0.1`). `MAWOS_DATABASE_MODE=docker` is only for the isolated Compose PostgreSQL service. For Docker against an external host database, the container uses the separate `host.docker.internal` URL; do not replace the native loopback URL with it.

Load configuration, migrate with migration-owner credentials, and start the API:

```bash
set -a; source .env; set +a
.venv/bin/alembic current
.venv/bin/alembic upgrade head
.venv/bin/python run.py
```

In another terminal:

```bash
cd mawos
source .venv/bin/activate
cd frontend
npm run dev
```

- Frontend: <http://127.0.0.1:5173>
- API health/status: <http://127.0.0.1:8000/>
- API docs: <http://127.0.0.1:8000/docs>

Current project documentation provides Linux/macOS shell commands; no separate Windows setup is asserted here.

## Docker

Copy/configure `.env` first. Compose never runs migrations automatically. Do not migrate an unknown/live database. Do not use `docker compose down -v` unless intentionally deleting local Docker volumes.

### A. Existing external local PostgreSQL

Set `MAWOS_DATABASE_MODE=external`, keep `MAWOS_DATABASE_URL` on `127.0.0.1`, and set `MAWOS_DOCKER_DATABASE_URL` to the `host.docker.internal` URL above.

```bash
docker compose config --quiet
docker compose build
docker compose up -d
docker compose ps
docker compose logs -f backend frontend
```

This starts frontend and backend. In `auto` mode, Groq is used only after a
successful authenticated model-list check; otherwise MAWOS tries an available
local Ollama instance and finally degrades to deterministic-only operation.
The Groq key is passed only to the backend runtime, never to the frontend build.
Groq lists the configurable default `openai/gpt-oss-20b` as a
[production model](https://console.groq.com/docs/models), served through its
[OpenAI-compatible endpoint](https://console.groq.com/docs/openai).

Start the optional local fallback explicitly:

```bash
docker compose --profile local-ai up -d
docker compose --profile local-ai exec ollama ollama pull qwen2.5:3b
```

Ollama turns default to a 90-second deadline via
`MAWOS_OLLAMA_TIMEOUT_SECONDS`; generated answers default to 160 tokens, and
`MAWOS_OLLAMA_KEEP_ALIVE_SECONDS=300` keeps the model warm between normal
requests without background polling. See `docs/DOCKER.md` for validated limits.

Provider modes are `auto` (Groq → Ollama → deterministic-only), `groq`
(Groq only), `ollama` (local only), and `disabled` (no generative calls).
Only bounded general-learning text and sanitized title/author/category catalogue
metadata can enter this layer. Private records, live stock counts, borrower data,
database access, JWTs, internal IDs, and credentials cannot. A per-user limit
applies only to generative turns; deterministic record and catalogue searches do
not consume it.

### B. Fresh Docker PostgreSQL

This is an isolated database, not a host-database copy. Set `MAWOS_DATABASE_MODE=docker`, `POSTGRES_DB`, `POSTGRES_USER`, and `POSTGRES_PASSWORD` in `.env`; retain `MAWOS_DOCKER_DATABASE_URL` because the base Compose file requires it.

```bash
docker compose -f docker-compose.yml -f docker-compose.docker-db.yml config --quiet
docker compose -f docker-compose.yml -f docker-compose.docker-db.yml build
docker compose -f docker-compose.yml -f docker-compose.docker-db.yml up -d
docker compose -f docker-compose.yml -f docker-compose.docker-db.yml ps
```

The persistent volume starts empty. Restore or initialize the intended data/schema explicitly; after backup, target verification, and approval, run migrations as a deliberate operator action. Docker-mode database URLs are private to its network.

Docker URLs: <http://localhost:3000>, <http://localhost:8000/>, and <http://localhost:8000/docs>.

## Migrations

Before a live migration, back up and verify the target database. Run Alembic as a database owner/migration role, not the runtime app role:

```bash
set -a; source .env; set +a
.venv/bin/alembic current
.venv/bin/alembic upgrade head
.venv/bin/alembic current
```

Use `head`, not an old hardcoded revision. The runtime role should not need DDL privileges. No reset or seed occurs automatically. For a known existing schema, review the baseline procedure before deliberately using `alembic stamp head`; stamping records a revision without applying DDL.

## Testing and quality checks

From the repository root:

```bash
.venv/bin/python -m pytest -q
cd frontend && npm test
cd frontend && npm run build
cd .. && .venv/bin/python -m pip check
docker compose config --quiet
git diff --check
```

PostgreSQL tests are opt-in. `MAWOS_POSTGRES_TEST_URL` must target isolated `mawos_test`, never live `mawos`:

```bash
export MAWOS_POSTGRES_TEST_URL='postgresql+psycopg://TEST_USER:TEST_PASSWORD@127.0.0.1:5432/mawos_test'
.venv/bin/python -m pytest tests/test_library_postgresql.py -q -rs
```

## Operational notes

- Admin creates Parent accounts and active child links in parent management. The temporary password is returned once and must be changed before protected use.
- Admin creates Librarian accounts in librarian management. Their temporary password is also returned once and subject to the first-login password-change gate.
- Notifications are in-app records addressed to one recipient. Users can list their own records, see unread count, and mark one/all read.
- Library reservation expiry is operator-scheduled, not a FastAPI startup job:

  ```bash
  .venv/bin/python -m backend.app.library.maintenance --batch-size 200
  docker compose run --rm --no-deps backend python -m backend.app.library.maintenance --batch-size 200
  ```

## Documentation

- [Architecture](docs/ARCHITECTURE.md)
- [Docker deployment](docs/DOCKER.md)
- [Timetable workflow](docs/TIMETABLE.md)
- [Placement workflow](docs/PLACEMENTS.md)
- [Library Agent](docs/LIBRARY.md)
- [Parent Portal](docs/PARENT_PORTAL.md)
- [Assistant notes](docs/ASSISTANT_PHASE3.md)
- [Timetable verification](docs/TIMETABLE_VERIFICATION.md)

## Current limitations and future scope

- Notifications are in-app only; email, SMS, and WhatsApp delivery are not implemented.
- No public parent registration, OTP, SMS login, or invitation flow.
- No payment gateway, QR/barcode event attendance, or external calendar sync.
- No Redis distributed event bus/cache.
- Library fines are for in-person collection, not online payment.
- The assistant is role-scoped/tool-backed; Groq and Ollama are optional, and deterministic record answers require neither.
- PostgreSQL tests need an explicit isolated `MAWOS_POSTGRES_TEST_URL`.
