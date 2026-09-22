# Versioned academic timetables

Implemented on `feature/timetable-generation`. The starting working tree was clean and no applicable `AGENTS.md` was found. No commit or push was performed.

## Design and migration contract

The reference archive was extracted outside the repository, under `/tmp/mawos-chronos-reference-902rlphu`, and inspected without modifying it. No license granting source-copy permission was evident. This feature contains a clean Python adaptation of the concepts in its solver: staffing checks, minimum-remaining-values (MRV) backtracking, bounded repair and hill climbing. It does not copy its TypeScript source, routes, database layer, authentication, seed or publication implementation.

The existing MAWOS structures are retained:

| Existing structure | Integration |
| --- | --- |
| `departments` | Authority for HOD scope and resource ownership |
| `faculty`, `subjects` | Referenced by ID/code; assigned teacher must be qualified and in the department |
| `students` | Authenticated student’s department, year, semester and section determine visibility; enrollment also checks section capacity |
| `teaching_assignments` | Staffing authority; new term-specific requirements refer to existing assignments |
| `timetable_slots` | Legacy research data only; not a publication source or fallback for application reads |

Migration **`20260908_timetable`**, following `20260907_scholarship_workflow`, adds 13 narrowly scoped tables:

- `tt_terms`, `tt_periods`, `tt_holidays`, `tt_sections`, `tt_rooms`
- `tt_qualifications`, `tt_faculty_limits`, `tt_faculty_unavailable`, `tt_room_unavailable`
- `tt_requirements`, `tt_runs`, `tt_entries`, `tt_audit`

No legacy data is deleted, converted, automatically imported, or seeded. Staffing is not duplicated in requirements: they reference `teaching_assignments`. Qualifications and unavailable periods are relational, not JSON arrays. JSON is used only for immutable input snapshots, metrics, conflicts and audit detail.

PostgreSQL enforces distinct section/faculty/room cells per run, foreign keys for every referenced resource, composite section/run/term/department consistency, a valid period reference, and at most one `PUBLISHED` run per term/department through `uq_tt_published_scope`. The absence of a published run is a supported empty state. Configuration checks reject overlapping period times and overlapping academic terms. Publication independently verifies all remaining hard constraints.

All timetable mutations take one transaction-scoped PostgreSQL advisory lock, shared across API processes. The short transaction serializes configuration changes and publication; CPU solving runs outside it. Archival, publication, publisher/validator metadata and audit events commit together. Constraint or injected failures roll everything back. Published and archived entries remain immutable through the API.

## Configure and use

1. Admin creates a non-overlapping academic term, defines weekly periods/breaks/closed periods, records holidays and creates department-owned rooms.
2. HOD configures sections for every enrolled department cohort, reviews/creates teaching assignments, records explicit qualifications and faculty load limits, then sets term-specific weekly requirements. Credits are not silently converted into demand.
3. Faculty record their own unavailable periods; HOD/admin record room unavailability.
4. HOD checks readiness and generates a draft with a chosen seed. Missing data, cross-department references, insufficient capacity, invalid blocks and impossible elementary loads produce actionable preflight errors.
5. HOD reviews coverage, hard conflicts, missing periods and weekly preview. Locking a period locks its entire occurrence/block. Generating from that draft preserves its locked entries in a new version.
6. A separate validation action is available. Only a `COMPLETE` run with zero independently verified violations can be explicitly published. The previous published version becomes `ARCHIVED`; it is retained.

### Bulk import from existing academic data

`scripts/bootstrap_timetable_data.py` derives term configuration from enrolled
`students` and existing `teaching_assignments`. It inserts only missing
`tt_sections`, `tt_qualifications`, `tt_faculty_limits`, `tt_requirements`, and,
only when explicitly requested, clearly named placeholder rooms. It never
updates or deletes existing rows and never creates or publishes timetable runs.

Dry-run is the default and PostgreSQL transactions are explicitly read-only:

```bash
set -a; source .env; set +a
.venv/bin/python scripts/bootstrap_timetable_data.py --term-id 1
```

New weekly requirements are intentionally blocked unless the operator supplies
`--weekly-periods N`, configures `MAWOS_TIMETABLE_DEFAULT_WEEKLY_PERIODS`, or
explicitly chooses `--use-subject-credits`. The report identifies which source
was used, and existing requirement values always win. The scheduling defaults for new requirements are
`--max-per-day 2`, `--block-length 1`, and `--room-type classroom`; every use is
listed under `defaulted_values` in the report. Faculty load limits are not
guessed: both `--faculty-daily-limit` and `--faculty-weekly-limit` are required
to create missing limits.

Writes require both `--apply` and `MAWOS_ALLOW_TIMETABLE_BOOTSTRAP=true`.
Placeholder rooms additionally require both `--create-placeholder-rooms` and
`--confirm-placeholder-rooms`; their names begin with `PLACEHOLDER-` and they
must not be treated as verified physical rooms. The HOD page uses the same
engine, always previews first, requires an explicit confirmation, and limits an
HOD to the department recorded in their authenticated user row. Admin may omit
`department` to preview or apply across all departments. Apply requests must
echo the latest dry-run `preview_hash`; changed inputs return `409` and require
a new preview.

The independent validator checks section/faculty/room collisions, assignment and qualification, availability, room type/capacity, open periods, faculty daily/weekly load, subject weekly/daily demand, complete consecutive lab blocks and fixed entries. Publication compares the current configuration hash with the generation snapshot and validates against both current and snapshot inputs. A configuration or source-lock change during solving rejects the entire save.

Room compatibility is directional. A sufficiently large `computer_lab` may host
an ordinary `classroom` lecture, but a classroom never satisfies a
`computer_lab` requirement. Preflight also compares required room-period demand
with the available compatible room-period capacity and reports the exact
capacity shortfall before search when that elementary bound is impossible.

Generation is a bounded synchronous request handled in FastAPI’s worker thread. Actual computation runs in a separate spawned process. There are **two solver slots per API process**, no unbounded job queue, a **20-second worker deadline**, and a maximum request budget of **2,000,000 candidate checks**. Excess concurrency and deadline expiration return controlled `503` errors without saving a run. Unexpected failures expose no raw exception.

The solver is pure and deterministic: no database, HTTP, commits, environment access, wall clock or global mutable state. Its local seeded RNG drives ordering and optimization. The total candidate-check counter is never reset between restarts, repair or optimization. Preflight and static-domain construction are outside that step counter but inside the worker wall-time bound. Metrics distinguish rejected search candidates from final hard violations. Room alternatives at a given time are collapsed to the smallest available compatible room during MRV; repair considers the full room domain. Soft costs include gaps, subject spread, faculty streaks, load balance, room preferences and earlier high-priority subjects.

## API contract

All endpoints use the existing JWT authentication, `/api` prefix, controlled `detail` errors and database session dependency. IDs are checked server-side. Inaccessible department resources return `404`; disallowed roles return `403`.

| Role | Method and path under `/api` | Purpose |
| --- | --- | --- |
| Authenticated | `GET /timetable/terms` | Term choices |
| Admin | `POST /admin/timetable/terms` | Create term |
| Admin | `GET /admin/timetable/configuration` | Departments and rooms |
| Admin | `POST /admin/timetable/rooms` | Create department-owned room |
| Staff | `GET /timetable/terms/{term}/periods` | Read period template and holidays |
| Admin | `PUT /admin/timetable/terms/{term}/periods` | Save template; omitted existing periods become closed |
| Admin | `POST /admin/timetable/terms/{term}/holidays` | Record/update holiday |
| HOD | `GET /hod/timetable/terms/{term}/configuration` | Review own department configuration |
| HOD | `POST /hod/timetable/terms/{term}/sections` | Configure section and size |
| HOD | `POST /hod/timetable/assignments` | Configure staffing |
| HOD | `POST /hod/timetable/qualifications` | Record qualification |
| HOD | `PUT /hod/timetable/terms/{term}/faculty-limits` | Configure daily/weekly load |
| HOD | `POST /hod/timetable/terms/{term}/requirements` | Configure section-assignment demand, block and room type |
| HOD/Admin | `POST /timetable/terms/{term}/bootstrap` | Dry-run or explicitly apply an insert-only academic-data bootstrap |
| Faculty/HOD | `GET`, `PUT /faculty/timetable/terms/{term}/availability` | Own unavailable periods only |
| HOD/Admin | `PUT /timetable/terms/{term}/rooms/{room}/availability` | Department room/institution room unavailability |
| HOD | `POST /hod/timetable/terms/{term}/preflight` | Readiness and actionable issues |
| HOD | `POST /hod/timetable/terms/{term}/runs` | Generate and atomically save a draft |
| HOD/Admin/Principal | `GET /timetable/runs?term_id=...` | Scoped history, latest 200 runs |
| HOD/Admin/Principal | `GET /timetable/runs/{run}` | Status, metrics, conflicts, missing requirements and preview |
| HOD | `POST /hod/timetable/runs/{run}/validate` | Independent validation |
| HOD | `PATCH /hod/timetable/runs/{run}/entries/{entry}/lock` | Lock/unlock entire occurrence |
| HOD | `POST /hod/timetable/runs/{run}/publish` | Disabled compatibility route; directs callers to secure operations |
| Faculty/HOD/Principal/Admin | `POST /timetable/operations/preview` | Strict allowlisted preview; faculty limited to own reschedule requests |
| HOD/Principal/Admin | `POST /timetable/operations/confirm` | Revalidate and explicitly apply a preview |
| HOD/Principal/Admin | `GET /timetable/operations/pending`, `/audit` | Scoped review queue and operational audit |
| Student | `GET /student/timetable` | Own published weekly/today/current/next data |
| Faculty/HOD | `GET /faculty/timetable` | Own published teaching data |
| Principal/Admin | `GET /principal/timetable/overview` | Institution-wide coverage and version status |

Personal timetable paths also support `/weekly`, `/today`, and `/current-next` suffixes; each returns the same coherent view contract. Student and faculty identity parameters are never accepted as authority. All these GET routes suppress autoflush and perform no writes, flushes or commits.

The existing `/timetable/{dept}/{year}/{section}` and CSV endpoints now use published versions and role filtering. Student/faculty dashboard timetable data and the assistant’s timetable tool use the same published source. Legacy `/hod/generate-timetable` and `/hod/generate-timetable-live` return `410` directing users to the versioned workflow. Legacy research solvers are proposal-only and cannot write timetable rows. Startup does not generate timetable data.

Current/next calculations use timezone-aware `Asia/Kolkata` datetimes. Current means `starts_at <= now < ends_at`. Next means a later start, continuing across weekdays, weekends, holidays and future published terms. Returned classes include code/name, faculty display name, room, section, date/day and start/end times. No active published term, holidays, free periods/breaks and no remaining classes have explicit empty messages. Partial or latest drafts are never substituted for publication.

## Frontend

The existing React layouts and authorization gates are retained. Routes:

- `/hod/timetable`: configuration, readiness, generation, result metrics, conflicts, unplaced demand, history, section preview, locks, validation and explicit publication.
- `/student/timetable`, `/faculty/timetable`: current/next cards, today’s classes and responsive weekly cards; faculty availability editor.
- `/admin/timetable`: academic terms, normalized period template, holidays and rooms.
- `/principal/timetable`: institution-wide draft/publication previews, confirmation controls, coverage and operational audit history.

Pending action gates use both an immediate ref and disabled controls to prevent duplicate clicks. Personal views refresh every 30 seconds without unmounting the availability editor on successful refresh. Loading, controlled errors and empty states are covered by component tests. React performs no authoritative scheduling or hard-constraint validation.

## Verification and safety

Development verification uses only isolated SQLite and **`mawos_test`**. `isolated_test_database_url` rejects a test URL targeting `mawos`, and PostgreSQL cleanup additionally checks `SELECT current_database()` before deleting only fixture-owned records.

Executed on `mawos_test`: `upgrade 20260908_timetable` → `downgrade 20260907_scholarship_workflow` → `upgrade 20260908_timetable`. All three succeeded. The live database was later inspected only in explicit read-only transactions for bootstrap development and its full dry run; row counts were unchanged. It was not migrated, seeded, generated into, published into, or modified.

Deployment command, **documented but not executed against live `mawos`**:

```bash
# Run only as a separately authorized deployment, with the intended deployment
# environment already configured and a verified backup/change plan.
.venv/bin/alembic upgrade 20260908_timetable
```

Do not start the updated PostgreSQL application against an unmigrated schema: startup’s existing schema check intentionally detects the new missing tables. This implementation does not bypass it or migrate at startup.

Verification results and complete modified-file inventory are in [TIMETABLE_VERIFICATION.md](TIMETABLE_VERIFICATION.md).

## Current limits

- Rooms and teachers are department-owned for scheduling. Principal/Admin can operate across departments, but shared cross-department resources require a future institution-owned resource model.
- After the first publication in a term, its periods, holidays, sections, requirements, load limits and availability cannot be changed through these APIs. Configure the next term separately. Existing teaching assignments referenced by publication history cannot be reassigned; supporting historical staffing changes requires a further versioned-assignment design.
- The guarded bootstrap may derive sections and qualifications from enrolled students and authorized teaching assignments. Weekly demand still requires an existing requirement, an explicit fixed value, or explicit opt-in to subject credits; legacy `timetable_slots` are never imported.
- Generation is request-bound. Run-status reads expose saved results; there is no durable background queue, in-flight run ID, or live progress stream. Invalid preflight and worker failures save no run. The database status vocabulary reserves `DRAFT`, `GENERATING` and `FAILED`; successful persistence currently produces `COMPLETE` or `PARTIAL`.
- Search is bounded and heuristic. Partial output is diagnostic and cannot publish; it is not a proof that a feasible timetable does not exist, and soft-score optimality is not guaranteed.
- Period and holiday authoring uses weekly recurrence plus whole-day closures. Dated reschedule/cancellation exceptions are supported; alternating-week base patterns and faculty preferences beyond hard availability are not exposed.
- Resource editing/deletion is deliberately limited: rooms can be created and their term availability managed; qualification records can be added. Historical timetable versions are never deleted by the application.
- No browser or live deployment verification was performed. Production build reports Vite’s existing large-bundle advisory; the scholarship test suite emits existing React `act(...)` warnings.
