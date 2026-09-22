# Secure timetable operations

## Architecture

MAWOS uses a deterministic, versioned timetable domain service. The assistant
does not receive a database connection, credentials, SQL, ORM objects, or an
arbitrary write/API tool.

The command boundary is:

1. Free text is routed to Timetable Operations. It never mutates data.
2. `/api/timetable/operations/interpret` may return only an allowlisted action
   and explicit identifiers. `/preview` uses strict Pydantic models with extra
   fields forbidden.
3. FastAPI derives identity, role and department from the JWT, resolves every
   ID, and checks current publication, occurrence, attendance and constraints.
4. A preview is saved with a 15-minute, one-time capability whose token is
   stored only as SHA-256. The preview has a correlation ID and a PREVIEWED
   audit event.
5. An authorized HOD, Principal or Admin explicitly confirms it. Confirmation
   locks and revalidates current state. Faculty-created reschedule requests are
   confirmed from the department review queue, never by faculty.
6. SQLAlchemy domain services perform the bounded write. A CONFIRMED event
   records actor, requested action, affected record IDs, before/after summary,
   timestamp and correlation ID. Existing event-bus notifications are emitted
   only after commit.

Generation snapshots normalized MAWOS departments, terms, periods, holidays,
sections, subjects, teaching assignments, qualifications, faculty limits and
availability, requirements, rooms, capacity/type/availability, and optionally
locked entries from a parent version. The pure seeded solver uses MRV
backtracking, bounded repair and soft-cost optimization. Independent validation
checks weekly demand, duplicate occurrences, blocks, section/faculty/room
collisions, qualifications, availability, room compatibility/capacity, and
faculty limits. Partial results retain conflicts/unplaced demand and cannot be
published.

`TimetableGenerationSource` is the narrow import boundary. Only
`mawos-native` is enabled; a future licensed adapter may map a vendor export or
API to the validated domain input but cannot receive a session or publish.

## Actions and authorization

| Action | Faculty | HOD | Principal/Admin |
| --- | --- | --- | --- |
| `generate_timetable_draft` | denied | own department | selected department |
| `get_timetable_conflicts` | denied from operations | own department | institution-wide |
| `preview_reschedule_class` | own assigned occurrence request | own department | institution-wide |
| `confirm_reschedule_class` | denied | own department | institution-wide |
| `preview_cancel_class` / `confirm_cancel_class` | denied | own department | institution-wide |
| `preview_replacement_slot` | own assigned occurrence request | own department | institution-wide |
| `confirm_replacement_slot` | denied | own department | institution-wide |
| `preview_coverage_assignment` / `confirm_coverage_assignment` | may accept/decline a proposal, cannot assign | qualified own-department candidates | institution-wide; HOD-absence escalation |
| `publish_timetable_draft` | denied | own department | institution-wide |

The confirm names are response/audit names for their corresponding preview.
HTTP confirmation uses a strict `/confirm` schema containing only a preview
UUID and, for its creator, the one-time token. Role, actor, department, faculty
and student identity overrides are rejected. Foreign-department IDs produce
scoped `404` responses.

Students, parents and librarians have no operation routes. Students and linked
parents see only their relevant published version and safe dated change
notices. Faculty see only their own published teaching schedule. Librarians
retain their existing timetable access policy (no operational write access).

The former research `TimetableAgent` now returns `proposal_only` plus proposed
slots. It never deletes or inserts `timetable_slots`; production persistence is
available only through the versioned operations/domain layer.

## Publication and occurrence changes

Generation always creates a new run. Publication requires a valid COMPLETE
run and a preview; confirmation archives the previous published run and
publishes the new run atomically. Runs and entries remain as version history.
The old direct HOD publish endpoint is retained only as a controlled `409`
upgrade response, so it cannot bypass confirmation.

Reschedule/cancel operations are dated immutable exceptions over a published
weekly entry. They never rewrite or delete the source entry. Revalidation
blocks holidays, past/out-of-term targets, breaks/closed or non-contiguous lab
periods, incompatible/undersized/unavailable rooms, unavailable faculty,
attendance already submitted, and section/faculty/room collisions against
published entries and prior dated changes.

Approved absences resolve their active published occurrences and create
coverage requests. The server returns four deterministic resolution paths:

1. qualified, available, within-limit substitute candidates;
2. the first conflict-free replacement slot in the next 14 days;
3. cancellation explicitly marked as requiring a make-up class;
4. unresolved/HOD decision when neither operational option is selected.

No substitute is auto-selected. A proposed substitute must accept. Attendance
continues to bind to the final active occurrence and accepted substitute;
attendance on a source occurrence prevents later timetable modification.

## Data changes

Migration `20260921_timetable_operations`, after
`20260916_faculty_coverage`, adds only:

- `tt_operation_previews` for expiring confirmation capabilities;
- `tt_operation_events` for append-only operational audit history;
- `tt_occurrence_changes` for dated reschedule/cancellation history.

It adds scoped, correlation, target and uniqueness indexes. No existing
timetable, coverage, attendance or user row is converted, deleted or seeded.
The migration downgrade intentionally retains operational/audit history.

## VidyaERP compatibility boundary

Assessment date: 2026-09-21. VidyaERP's public product material describes
clash-aware timetable management, but its public integrations list only email,
SMS, payment gateways, biometric devices, accounting systems, online learning
platforms and cloud storage, with provider availability confirmed during a
requirements discussion. Its public resources page makes module configuration
documentation and data-readiness material request-access items. No public,
supported timetable API specification, authentication contract, webhook, or
documented import/export schema was found.

Therefore MAWOS does not call, scrape, copy, or claim compatibility with
VidyaERP. It uses `mawos-native`. A future integration requires written vendor
authorization and a versioned public/contracted format, then a separate adapter
that produces the native validated snapshot; it still cannot publish or bypass
preview, authorization, validation or audit.

Reviewed public first-party pages:

- https://www.vidyaerp.com/modules.html
- https://vidyaerp.com/resources.html
- https://www.vidyaerp.com/

## Local rebuild and migration commands

These commands are for an explicitly selected local/development database. Do
not point them at a live database. Review generated SQL and take a backup under
the deployment change process before an authorized production rollout.

```bash
.venv/bin/python -m pip install -r requirements.txt
cd frontend && npm ci && npm run build && cd ..

.venv/bin/alembic heads
.venv/bin/alembic upgrade 20260921_timetable_operations

.venv/bin/python -m pytest -q
cd frontend && npm test -- --run && cd ..
.venv/bin/python -m pip check
docker compose config -q
git diff --check
```

No migration is run automatically at startup. This implementation did not
apply a migration, alter live data, change an environment/secret, commit, or
push.

## Current limitations

- Rooms and teaching resources are department-owned in the current schema.
  Principal/Admin can review all departments, but genuinely shared resources
  need a future institution-owned resource model before cross-department
  collision solving is meaningful.
- Approved dated absences are handled as occurrence operations, not by marking
  a faculty member unavailable for every recurrence in a weekly base draft.
- Replacement search is deterministic and bounded to 14 days. It reports an
  unresolved conflict rather than silently placing an invalid class.
- The solver is bounded and heuristic: a partial report is not a mathematical
  proof of infeasibility or global soft-score optimality.
- Operations use a short request/worker model, not a durable distributed job
  queue. There is no live progress stream.
- No VidyaERP adapter is enabled without a supported contract and authorization.
