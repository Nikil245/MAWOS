# MAWOS University ERP

MAWOS is a role-based University ERP for academic and operational workflows. It uses a React/Vite frontend, FastAPI REST API, PostgreSQL as the source of truth, JWT authorization, Docker, Alembic migrations, and a multi-agent backend. SQLite is also supported for isolated local development and tests.

## What it solves

MAWOS brings together academic administration, published timetables, faculty absence and coverage, scholarships, placement drives, library operations, admissions, in-app notifications, and role-specific dashboards. Workflows are explicit and role-gated rather than simulated client-side state.

## Key features by role

| Role | Implemented capabilities |
|---|---|
| Student | Own attendance, marks, fees, hall-ticket status, scholarships, placements, timetable, events, notifications, and library access. |
| Faculty | Authorized attendance and marks entry, own timetable and availability, scholarship authoring, absence requests, and substitute-coverage responses. |
| HOD | Department dashboard, scholarship review, fee-defaulter view, timetable configuration, draft generation, validation/review, locking, publication, and coverage review. |
| Admin / Registrar | Admissions, institutional configuration, terms/periods/rooms, placements, events, parent/librarian account management, and library staff workflows. |
| Librarian | Catalogue, reservations, physical pickup/return confirmation, issue records, fines, and library operations. |
| Parent | Read-only views of an active, explicitly linked child’s dashboard, timetable, events, notifications, and library information. |
| Principal | Institution dashboard and read-only timetable and department coverage views. |

Timetables use versioned drafts with generation, validation/review, explicit publication, dated reschedules, and audit/operation history. Faculty coverage is a separate dated workflow: an accepted substitute assignment appears only on its occurrence date, not as a recurring weekly class.

## System architecture

~~~mermaid
flowchart LR
  UI[React + Vite frontend] -->|REST /api| API[FastAPI API]
  API --> AUTH[JWT authentication and RBAC]
  AUTH --> DOM[Role-scoped domain services and agents]
  DOM --> DB[(PostgreSQL via SQLAlchemy/Psycopg)]
  DOM --> BUS[In-app event bus and workflow/audit history]
  BUS --> N[Recipient-owned notifications]
  DOM -. eligible sanitized general query .-> AI[Optional Groq or Ollama]
  MIG[Alembic migrations] --> DB
~~~

The backend has REST domain services for timetable, coverage, placement, library, events, and parent workflows. The optional AI provider is not required for deterministic record queries and is only consulted for permitted, bounded generative turns.

## How the multi-agent system works

Four registered components meet the project’s autonomous-agent criterion: they own policy/state beyond a single request and can react to events or scheduled scans.

| Agent | Responsibility |
|---|---|
| Orchestrator | Classifies assistant requests, applies scope, selects approved read tools, and controls optional LLM escalation. |
| Attendance | Validates attendance intake, recalculates summaries, detects shortages/streaks, and runs proactive scans. |
| Eligibility | Evaluates hall-ticket eligibility and scholarship assessments from attendance and fee events. |
| Timetable | Provides the legacy proposal-only solver; production timetable writes use the versioned timetable operations service. |

The registry also exposes tool-backed domain components: Academic, Admission, Finance, Placement, Library, and Notification. They provide domain behavior and event subscriptions where applicable, but are not represented as autonomous agents in the project’s agent count.

Agents do not bypass API authorization and do not execute arbitrary SQL. Event flow is: API request → authorization and scope checks → deterministic domain service/agent → event/workflow record → response or recipient-owned notification.

## Orchestrator architecture

The orchestrator classifies intent with the deterministic lexicon first, applies authenticated role and department scope, and executes an allowlisted deterministic tool/service where possible. Only low-confidence, eligible cases may use the optional provider path. Provider failure degrades to the deterministic result. The orchestrator does not expose secrets, tokens, database URLs, private records, or unauthorized data.

~~~mermaid
sequenceDiagram
  participant U as Authenticated user
  participant O as Orchestrator
  participant A as Authorization and scope checks
  participant T as Allowlisted tool or domain agent
  participant R as Safe response renderer
  U->>O: Ask a question
  O->>A: Identify intent and validate role/scope
  A-->>O: Permitted tool set or denial
  O->>T: Deterministic scoped read first
  T-->>O: Authorized evidence
  O->>R: Render safe role-scoped answer
  R-->>U: Response without secrets or unauthorized data
~~~

## Timetable and coverage workflows

### Draft generation to publication

1. An authorized HOD configures timetable inputs for a department and term.
2. MAWOS generates a versioned draft and validates/reviews the proposal.
3. Authorized users may lock eligible entries and explicitly publish a valid draft.
4. Students and faculty read the published version; drafts do not replace it.

### Faculty reschedule

1. Faculty selects one of their published occurrences and requests a replacement-slot preview.
2. The dated change is validated and requires authorized HOD review/confirmation.
3. The confirmed change is retained as a dated occurrence change and audit event.

### Faculty absence and substitute coverage

1. Faculty creates and submits a dated absence request.
2. An authorized HOD reviews it; coverage requests are created for affected published occurrences.
3. The HOD reviews eligible candidates and proposes one substitute for a specific occurrence.
4. The substitute accepts or declines. Acceptance creates a one-time substitute class for that exact date.

A reschedule moves or changes a published occurrence through a dated timetable change. Coverage keeps the original occurrence and assigns substitute faculty for it; it is not a recurring assignment.

## Technology stack

- Frontend: React, Vite, React Router, Tailwind CSS, Lucide, Recharts, Vitest, Testing Library
- Backend: Python, FastAPI, Pydantic, Uvicorn, SQLAlchemy, Psycopg 3, Alembic, PyJWT
- Data/rules: PostgreSQL; optional SQLite for local/test use; scikit-learn, pandas, numpy, joblib
- Operations: Docker Compose, Nginx unprivileged frontend image, health checks
- Optional AI: Groq-compatible hosted provider or local Ollama, with deterministic fallback

## Prerequisites

- Git
- Python 3.12
- Node.js 22 and npm
- PostgreSQL for PostgreSQL development
- Docker Engine with the Compose plugin for containerized development

## Local setup

### 1. Clone and install

~~~bash
git clone https://github.com/Nikil245/MAWOS.git mawos
cd mawos
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
cd frontend && npm ci && cd ..
cp .env.example .env
~~~

Edit the ignored <code>.env</code>; do not commit it. Use a database-owner/migration account for schema changes and a restricted application account at runtime.

### 2. Configure PostgreSQL safely

<code>MAWOS_DATABASE_MODE</code> is required and must be <code>external</code> or <code>docker</code>.

- <code>external</code> uses an existing host PostgreSQL database. Native commands use loopback <code>MAWOS_DATABASE_URL</code>; Docker backend containers use <code>MAWOS_DOCKER_DATABASE_URL</code> with <code>host.docker.internal</code>.
- <code>docker</code> is only for the isolated PostgreSQL service in [docker-compose.docker-db.yml](docker-compose.docker-db.yml). It uses a separate named volume and does not copy, reset, seed, or migrate a database automatically.

~~~env
MAWOS_ENV=development
MAWOS_DATABASE_MODE=external
MAWOS_DATABASE_URL=postgresql+psycopg://APP_USER:APP_PASSWORD@127.0.0.1:5432/mawos
MAWOS_DOCKER_DATABASE_URL=postgresql+psycopg://APP_USER:APP_PASSWORD@host.docker.internal:5432/mawos
MAWOS_MIGRATION_DATABASE_URL=postgresql+psycopg://MIGRATION_OWNER:MIGRATION_PASSWORD@127.0.0.1:5432/mawos
MAWOS_JWT_SECRET=GENERATE_A_LONG_RANDOM_SECRET
MAWOS_SEED_DEMO_DATA=false
~~~

### 3. Run reviewed migrations

<code>MAWOS_MIGRATION_DATABASE_URL</code> must use <code>postgresql+psycopg://</code>. Do not migrate an unknown or live database without reviewing the plan and taking a backup.

~~~bash
set -a; source .env; set +a
.venv/bin/alembic current
.venv/bin/alembic upgrade head
~~~

FastAPI uses <code>MAWOS_DATABASE_URL</code>; the Docker entrypoint removes the migration URL before starting Uvicorn.

### 4. Start without Docker

~~~bash
.venv/bin/python run.py
~~~

In another terminal:

~~~bash
cd mawos
source .venv/bin/activate
cd frontend
npm run dev
~~~

- Frontend: <http://127.0.0.1:5173>
- API status: <http://127.0.0.1:8000/>
- API documentation: <http://127.0.0.1:8000/docs>

### 5. Start with Docker Compose

For an existing host database in external mode:

~~~bash
docker compose config --quiet
docker compose build
docker compose up -d
docker compose ps
docker compose logs -f backend frontend
~~~

Compose defaults <code>MAWOS_RUN_MIGRATIONS</code> to <code>false</code>; run reviewed migrations separately with the owner-capable migration URL.

For a fresh isolated Docker PostgreSQL target, configure Docker database variables, then use the override:

~~~bash
docker compose -f docker-compose.yml -f docker-compose.docker-db.yml config --quiet
docker compose -f docker-compose.yml -f docker-compose.docker-db.yml build
docker compose -f docker-compose.yml -f docker-compose.docker-db.yml up -d postgres
docker compose -f docker-compose.yml -f docker-compose.docker-db.yml up -d backend frontend
docker compose -f docker-compose.yml -f docker-compose.docker-db.yml ps
~~~

Docker Compose exposes the frontend at <http://localhost:3000>, API status at <http://localhost:8000/>, and API documentation at <http://localhost:8000/docs>. Stop services with <code>docker compose down</code>; do not use <code>docker compose down -v</code> unless intentionally deleting local volumes.

## Environment variables

Copy [.env.example](.env.example) and replace placeholders in ignored <code>.env</code>. The template lists all optional provider and library-policy settings.

| Variable | Purpose | Placeholder example |
|---|---|---|
| <code>MAWOS_ENV</code> | Required deployment mode. | <code>development</code> |
| <code>MAWOS_DATABASE_MODE</code> | Required database target selection. | <code>external</code> |
| <code>MAWOS_DATABASE_URL</code> | Native/runtime database URL. | <code>postgresql+psycopg://APP_USER:APP_PASSWORD@HOST:5432/mawos</code> |
| <code>MAWOS_DOCKER_DATABASE_URL</code> | Database URL passed to backend containers in external mode. | <code>postgresql+psycopg://APP_USER:APP_PASSWORD@host.docker.internal:5432/mawos</code> |
| <code>MAWOS_MIGRATION_DATABASE_URL</code> | Alembic-only owner-capable database URL. | <code>postgresql+psycopg://MIGRATION_OWNER:MIGRATION_PASSWORD@HOST:5432/mawos</code> |
| <code>MAWOS_RUN_MIGRATIONS</code> | Docker entrypoint migration policy. | <code>false</code> |
| <code>MAWOS_JWT_SECRET</code> | JWT signing secret. | <code>GENERATE_A_LONG_RANDOM_SECRET</code> |
| <code>MAWOS_CORS_ORIGINS</code> | Explicit browser origins allowed to call the API. | <code>https://frontend.example.edu</code> |
| <code>MAWOS_SEED_DEMO_DATA</code> | Enables SQLite demo seeding outside production only. | <code>false</code> |
| <code>VITE_API_BASE_URL</code> | API base embedded into the frontend build. | <code>/api</code> |
| <code>MAWOS_AI_PROVIDER</code> | Optional provider policy: auto, groq, ollama, disabled. | <code>disabled</code> |
| <code>GROQ_API_KEY</code> | Optional server-only hosted-provider key. | <code>YOUR_PROVIDER_KEY</code> |
| <code>MAWOS_OLLAMA_HOST</code> | Optional native Ollama endpoint. | <code>http://127.0.0.1:11434</code> |
| <code>POSTGRES_DB</code>, <code>POSTGRES_USER</code>, <code>POSTGRES_PASSWORD</code> | Isolated Docker PostgreSQL only. | <code>REPLACE_WITH_VALUE</code> |

Never give a server secret a <code>VITE_</code> prefix. Production rejects insecure JWT settings and wildcard/unqualified CORS configuration.

## Testing and validation

From the repository root:

~~~bash
.venv/bin/python -m pytest -q
cd frontend && npm test
cd frontend && npm run build
cd .. && .venv/bin/python -m pip check
docker compose config --quiet
docker compose ps
git diff --check
~~~

Migration status and application:

~~~bash
set -a; source .env; set +a
.venv/bin/alembic current
.venv/bin/alembic upgrade head
~~~

PostgreSQL tests are opt-in and must target an isolated test database, never the live <code>mawos</code> database:

~~~bash
export MAWOS_POSTGRES_TEST_URL='postgresql+psycopg://TEST_USER:TEST_PASSWORD@127.0.0.1:5432/mawos_test'
.venv/bin/python -m pytest tests/test_library_postgresql.py -q -rs
~~~

Use <code>docker compose ps</code> to inspect health. Backend checks <code>/</code>; frontend checks its local Nginx root; the optional PostgreSQL override uses <code>pg_isready</code>.

## Deployment notes

Deploy backend and frontend as separate Render Docker services; both images use Render’s injected <code>PORT</code>. Configure <code>MAWOS_ENV=production</code>, <code>MAWOS_DATABASE_MODE=external</code>, a restricted runtime database URL, explicit HTTPS CORS origins, and a strong JWT secret. Set <code>VITE_API_BASE_URL</code> to the browser-reachable backend <code>/api</code> URL.

Use an owner-capable account only for <code>MAWOS_MIGRATION_DATABASE_URL</code>, using <code>postgresql+psycopg://</code>. In production, <code>MAWOS_RUN_MIGRATIONS=auto</code> runs <code>alembic upgrade head</code>, removes the migration URL from the runtime environment, then starts Uvicorn with the runtime URL. Set it to <code>false</code> only for an explicitly authorized separate migration step. Leave Render’s Docker Command blank so the image entrypoint remains in control.

## Security and data handling

- JWT bearer authentication and RBAC protect API routes.
- Faculty, student, parent, and department views apply identity and department scope; parents are read-only and require explicit active links.
- Timetable operations, coverage workflows, and notifications retain workflow/audit context.
- Secrets, database URLs, provider keys, and JWT values belong in environment variables, never committed files or frontend build variables.
- The assistant uses allowlisted, scope-checked reads and does not provide arbitrary SQL or operational mutation through chat.
- PostgreSQL runtime roles should not require DDL privileges; use a separate migration owner for schema changes.

## Repository structure

~~~text
backend/        FastAPI application, domain services, agents, and Docker entrypoint
frontend/       React/Vite application, UI tests, and Nginx configuration
alembic/        Alembic migration environment and revisions
tests/          Backend test suite
docs/           Architecture and operational documentation
evaluation/     Evaluation tooling, fixtures, and recorded results
ml/             Model training, calibration, and data utilities
scripts/        Operational/import/verification scripts
data/           Project data assets
~~~

Useful references: [architecture](docs/ARCHITECTURE.md), [Docker deployment](docs/DOCKER.md), [timetable workflow](docs/TIMETABLE.md), [placement workflow](docs/PLACEMENTS.md), [library workflow](docs/LIBRARY.md), [parent portal](docs/PARENT_PORTAL.md), and [assistant notes](docs/ASSISTANT_PHASE3.md).

## Troubleshooting

| Problem | Checks |
|---|---|
| Docker service is unhealthy | Run <code>docker compose ps</code>, then <code>docker compose logs -f backend frontend</code>. Confirm backend <code>/</code> and frontend Nginx root are reachable. |
| Migration configuration | Load <code>.env</code>, verify <code>MAWOS_DATABASE_MODE</code>, and use an owner-capable <code>MAWOS_MIGRATION_DATABASE_URL</code> with <code>postgresql+psycopg://</code>. Run <code>alembic current</code> before <code>upgrade head</code>. |
| PostgreSQL connection issue | Confirm PostgreSQL is running, URL/driver are correct, and host/Docker connectivity matches the mode. External Docker mode needs <code>host.docker.internal</code>; native tools use loopback. |
| Frontend cannot reach backend | For Vite, start the API on <code>127.0.0.1:8000</code>. For containers, keep <code>VITE_API_BASE_URL=/api</code> unless using a browser-reachable separately hosted API; then rebuild the frontend and configure explicit CORS origins. |

## Current limitations

- Notifications are in-app only; email, SMS, and WhatsApp delivery are not implemented.
- No public parent registration, OTP/SMS login, payment gateway, external calendar sync, or Redis distributed event bus/cache.
- Library fines represent in-person collection, not online payment.
- The optional provider layer can be disabled without affecting deterministic record and catalogue reads.
