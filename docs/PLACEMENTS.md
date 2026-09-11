# Placement Agent

## Compatibility and design

The existing `placement_drives` and `placement_shortlists` tables are reused.
The existing `uq_shortlist_entry(drive_id, usn)` constraint is retained. Existing
shortlist probabilities and reason strings are preserved until an explicit
evaluation updates them. The former implementation had no drive status, outcome
table, fee subscription, guarded model loading, or management pages.

Business rules live in `backend/app/placement/service.py`; the agent adapts
attendance/fee events, and the API enforces JWT roles and transaction boundaries.
Existing student dashboard and placement statistics response fields remain
compatible. Student dashboard reads now use stored results or a read-only
hard-filter preview. They never score a model or create shortlist records.

The admin page is `/admin/placements`, and the student page is
`/student/placements`. Routes and navigation are scoped to those roles.
Authenticated staff may list drive details, but only administrators may access
full shortlists/outcomes or other students' eligibility through placement APIs.

## Evaluation and lifecycle

Only year-4 students enter shortlist generation. Hard filters collect every
failure in department, CGPA, backlogs, attendance, fee-clearance order. Fee
clearance uses MAWOS's existing `fees_cleared` helper. Reasons are persisted as
one string joined by `; `. A hard rejection never invokes the model.

After passing hard rules, the trusted Random Forest probability is compared to
`MAWOS_PLACEMENT_MODEL_THRESHOLD` (default `0.5`, valid inclusive range `0–1`).
Shortlists are ordered by eligibility, descending model probability, then USN;
rules-only rows have no fabricated probability or model version. Student UI
contains no other candidates or rankings.

Drives default to `OPEN`; admins may create `DRAFT`. Editing is limited to
`DRAFT`/`OPEN`, including opening a draft. Generation accepts only `OPEN` or
`SHORTLIST_GENERATED` and moves to `SHORTLIST_GENERATED`. Existing shortlist
rows (including those created by automatic events) require `regenerate=true`.
Close accepts `OPEN`/`SHORTLIST_GENERATED`; cancel sets `CANCELLED`.

Attendance and fee events automatically evaluate active drives dated today
minus seven days or later. `OFFER_ACCEPTED`, `OFFER_DECLINED`, and `REJECTED`
outcomes freeze automatic changes for that pair; `OFFER_MADE` does not.
Manual regeneration may rewrite eligibility but never outcomes. Accepting
an offer on another drive requires explicit `allow_multiple_offers=true`.
PostgreSQL drive/student row locks serialize competing generation and offer
requests, with database uniqueness as an additional safeguard.

## Model provenance and fallback

The existing `ml/train.py` already trains the compatible classifier with
100 estimators, maximum depth 8, minimum leaf size 5, random seed 42, and feature
order `cgpa`, `backlogs`, `attendance_pct`. Its existing local artifact was
reused without retraining or overwriting scholarship models/data.

Artifact path is fixed by source code, never supplied by an API caller. Only
trusted repository-generated artifacts belong there. Runtime validates the
classifier type, parameters, feature count/names when available, and classes.
The stored model version is `rf-` plus the first 12 hexadecimal characters of
the artifact SHA-256 (15 characters total), so replacing the artifact changes
the recorded version. The artifact inspected for this implementation is
`rf-4fb7a57a6ad9` (full SHA-256
`4fb7a57a6ad92559152f5a1e874dd36b40865b9de3c3b4da9c5f38615446d0a2`).

Missing, invalid, corrupt, incompatible or failing artifacts produce safe
rules-only results. Passing candidates receive exactly:

```text
Meets all drive criteria (model unavailable, rules-only evaluation)
```

For a hard-filter rejection both probability and model version are null.
Without a stored shortlist, eligibility returns `NOT_EVALUATED`, checks only
hard rules, and does not persist. Passing preview eligibility is null until
evaluated; failing previews report their hard-filter reasons.

## API

All paths below start with `/api/placements` and require a JWT.

| Method and path | Access / action |
| --- | --- |
| `GET /drives`, `GET /drives/{id}` | Any authenticated role; counts included in admin list only |
| `POST /drives` | Admin create (all required drive fields) |
| `PUT /drives/{id}` | Admin full editable-drive replacement |
| `POST /drives/{id}/close`, `/cancel` | Admin explicit lifecycle actions |
| `POST /drives/{id}/shortlist` | Admin; JSON `{"regenerate": false}` or explicit `true` |
| `GET /drives/{id}/shortlist` | Admin ranked shortlist |
| `GET /drives/{id}/eligibility/{usn}` | Student own USN / admin any student |
| `PUT /drives/{id}/outcomes/{usn}` | Admin outcome upsert |
| `GET /drives/{id}/outcomes` | Admin outcomes |

USNs are trimmed and uppercased. Student cross-USN requests return 403 before
lookups, even for nonexistent students. Invalid payloads return 422; missing
records return 404; lifecycle/regeneration/accepted-offer conflicts return 409.
SQL errors return controlled messages without database details.

Outcome request fields are `outcome_status`, optional positive
`package_offered`, and `allow_multiple_offers` (default false).

## Events and notifications

The existing bus receives `placement.updated` after attendance/fee evaluation
with `usns` actually evaluated and `entries_updated`. Manual batches publish
`placement.shortlist_generated` with drive/company/count/version and
`placement.notification_required` for eligible students with `usn`, `drive_id`,
and `notification_type=PLACEMENT_SHORTLISTED`. Outcome writes publish
`placement.offer_made`, `.offer_accepted`, `.offer_declined`, or `.rejected`.
Events are published after the domain transaction commits, using existing
workflow ID conventions.

The Notification Agent stores an individual in-app notice. The drive-specific
title plus recipient/source identify prior notices; student row locking makes
repeat delivery safe across concurrent workers. Repeated regeneration does not
duplicate an existing notice, even if it has already been read. This uses the
existing notification drawer and recipient filtering. As elsewhere in MAWOS,
the in-process bus is not a durable outbox: process loss after commit can lose
an event; explicit regeneration can retry notification delivery.

## Migration and operational safety

Source revision chain ends in `20260908_timetable` → `20260911_placement`.
The configured live database could not be reached during read-only inspection;
its applied revision, placement row counts, and any data issues remain
unconfirmed. No live schema/data writes were performed.

The new migration adds drive fields/defaults, status constraints/indexes,
shortlist model version and the outcome table/unique constraint/index. Existing
drives get `OPEN`, fee-clearance false and UTC migration-time timestamps (their
original creation timestamps are unknown). No legacy placements are replaced.
Existing duplicate shortlist pairs, if uniqueness was absent, cause a controlled
migration failure for administrator review; nothing is deleted or deduplicated.

Downgrade deliberately retains all columns, constraints and outcome rows, and
rolls back only Alembic revision tracking. This preserves data for application
rollback; re-upgrade is idempotent. It is not a physical schema rollback.
Fresh databases still require the existing MAWOS baseline restored first—the
baseline migration is intentionally empty. Startup never performs migrations.

Do not run without a verified database backup. Confirm the intended URL,
database name and current revision, stop application writers, then manually:

```bash
# Load reviewed environment values through your normal process configuration.
.venv/bin/alembic current
.venv/bin/alembic upgrade 20260911_placement
# Container equivalent (use the matching external/docker DB Compose files):
docker compose exec backend alembic upgrade 20260911_placement
```

If the backend is stopped because the new table is absent, use a one-off
container instead, after the same backup/target verification:

```bash
docker compose run --rm --no-deps backend alembic upgrade 20260911_placement
```

None of these live migration commands were executed during implementation.
Do not stamp past unapplied migrations. Rebuild backend/frontend containers
when deploying the code; do not bring up the new backend before its reviewed
migration, because the existing schema verifier requires the outcome table.

## Verification and browser checks

```bash
.venv/bin/pytest
cd frontend
npm test
npm run build
cd ..
.venv/bin/python -m pip check
git diff --check
```

PostgreSQL tests require `MAWOS_POSTGRES_TEST_URL` to target **mawos_test**:

```bash
.venv/bin/pytest tests/test_placement_postgresql.py -q
```

They verify the server-side database name, run in unique test schemas, and
exercise migration upgrade/downgrade/re-upgrade and concurrent shortlist/offer
requests. Migration fixtures roll back; concurrency fixtures retain their
uniquely named test schema for inspection. The implementation verification used
a temporary PostgreSQL container with tmpfs storage and stopped it afterwards;
the existing Docker volumes and live database were untouched.

Manual browser acceptance after deployment: log in as an admin, create/open a
drive, confirm generation/regeneration, inspect results, record an offer and
exercise the second-offer warning. As a student, verify the own-record page and
one shortlist notice. Check refresh/navigation at desktop and mobile widths,
and verify staff cannot render either placement workspace.

Recorded verification: backend **482 passed, 17 skipped** (PostgreSQL checks
are opt-in in the ordinary run); focused PostgreSQL **2 passed** in isolated
`mawos_test`; frontend **116 passed**; production build, project-venv `pip check`
and `git diff --check` passed. The existing large-bundle warning and Python
dependency deprecation warnings remain. Missing/corrupt/incompatible artifacts
and prediction failures were verified to use rules-only fallback.

## Files changed for this feature

- `.env.example`, `docker-compose.yml`: model threshold setting.
- `backend/app/models.py`: additive placement fields/outcome metadata.
- `backend/app/main.py`: register placement routes.
- `backend/app/agents/placement.py`: placement event adapter and compatibility reads.
- `backend/app/agents/notification.py`: scoped, idempotent shortlist notifications.
- `backend/app/placement/__init__.py`: domain package.
- `backend/app/placement/schemas.py`: validated request contracts.
- `backend/app/placement/scoring.py`: trusted model validation/versioning/fallback.
- `backend/app/placement/service.py`: rules and transactional workflows.
- `backend/app/placement/api.py`: authenticated HTTP interface.
- `alembic/versions/20260911_placement.py`: data-preserving migration.
- `frontend/src/services/api.js`: placement API wrappers.
- `frontend/src/App.jsx`, `frontend/src/routes/roleRoutes.js`,
  `frontend/src/layouts/AppLayout.jsx`: protected pages, return paths and navigation.
- `frontend/src/pages/placement/Placements.jsx`: admin and student pages.
- `tests/test_placement.py`, `tests/test_placement_postgresql.py`: backend contracts
  and guarded PostgreSQL migration/concurrency tests.
- `frontend/src/test/placements.test.jsx`: role-safe frontend flows.
- `docs/PLACEMENTS.md`: compatibility, operational instructions and verification.

Earlier uncommitted Docker/database configuration changes in the workspace
were preserved; they are not newly implemented placement changes.
