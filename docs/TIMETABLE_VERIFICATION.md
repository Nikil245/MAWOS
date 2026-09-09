# Timetable implementation verification

Final verification on branch `feature/timetable-generation`. No commits or pushes.

## Results

| Check | Result |
| --- | --- |
| Full backend suite | **437 passed**, zero skips; 21.19 seconds |
| Timetable PostgreSQL integration | **12 passed**, zero skips, on `mawos_test` |
| Full frontend suite | **96 passed** in 12 files; includes 20 timetable tests |
| Frontend production build | Passed; 2,430 modules transformed |
| `pip check` | No broken requirements found |
| `git diff --check` | Passed |
| Alembic migration | `20260908_timetable` upgrade → downgrade to `20260907_scholarship_workflow` → upgrade succeeded on **mawos_test only** |
| Live database | Read-only schema/record inspection and bootstrap dry run; row counts unchanged; no migrations, seeding, generation, publication or writes |
| Browser/live deployment | Not performed; no browser or live verification claimed |

The backend run retained 406 existing dependency/model warnings; frontend scholarship tests retained React `act(...)` warnings. Vite reported a large-bundle advisory (approximately 748 kB before gzip). All required checks passed.

PostgreSQL tests exercise actual unique constraints and foreign keys, atomic generation and publication rollback, two competing publication sessions (same and different drafts), and authenticated personal GET endpoints inside `SET TRANSACTION READ ONLY` with flush/commit explicitly forbidden. Existing isolation tests confirm that a test URL pointing at live `mawos` is rejected before connection.

The production application has not been restarted against live PostgreSQL. Its existing schema check requires the new migration before deployment. The live migration command is documented, unexecuted, in [TIMETABLE.md](TIMETABLE.md).

## Solver performance

Command: `.venv/bin/python scripts/benchmark_timetable.py`.

Synthetic realistic-shaped dataset: **12 sections, 24 faculty, 14 rooms, 60 section-subject requirements, 216 weekly periods**, including consecutive laboratory blocks and a daily closed/break period. The total configured search budget was 500,000 candidate checks per run.

| Seed | Placed / required | Independent hard violations | Total candidate checks | Wall time | Soft score |
| --- | --- | --- | --- | --- | --- |
| 7 | 216 / 216 | 0 | 419,799 | 639.65 ms | 893.80 |
| 19 | 216 / 216 | 0 | 419,438 | 655.22 ms | 858.25 |
| 41 | 216 / 216 | 0 | 420,232 | 673.77 ms | 843.75 |

These are local pure-solver measurements, not production latency or evidence of optimality. Worker startup, DB snapshot construction and persistence are additional costs. This benchmark performs no database access. The frozen P1 research benchmark and solver remain unchanged.

## Remaining limitations

- Scheduling resources belong to one department; shared resources and institution-wide publication are unsupported.
- Published-term scheduling inputs and historical staffing assignments are immutable through the API. Future configuration must use a new term; historical teacher reassignment requires a further versioned-staffing design.
- Generation is request-bound with two solver slots per API process and a 20-second worker deadline. There is no durable background queue, in-flight run polling or live solver trace.
- Bounded heuristic search may return a partial diagnostic draft; it never publishes one and does not prove infeasibility or optimality.
- Alternating-week schedules, one-off class substitutions and faculty soft-preference editing are unsupported. Resource editing/deletion is limited to preserve history.
- No real institutional timetable data was configured or published. The live database was inspected only through read-only transactions, and no browser/live deployment verification was performed.

See [TIMETABLE.md](TIMETABLE.md) for the configuration workflow, authorization details, endpoint catalog, transaction design and full operational limits.

## Complete file inventory

### Modified existing files

- [README.md](../README.md)
- [.env.example](../.env.example)
- [backend/app/agents/tools.py](../backend/app/agents/tools.py)
- [backend/app/api/routes.py](../backend/app/api/routes.py)
- [backend/app/main.py](../backend/app/main.py)
- [backend/app/models.py](../backend/app/models.py)
- [frontend/README.md](../frontend/README.md)
- [frontend/src/App.jsx](../frontend/src/App.jsx)
- [frontend/src/layouts/AppLayout.jsx](../frontend/src/layouts/AppLayout.jsx)
- [frontend/src/pages/faculty/FacultyTimetable.jsx](../frontend/src/pages/faculty/FacultyTimetable.jsx)
- [frontend/src/pages/hod/HodDashboard.jsx](../frontend/src/pages/hod/HodDashboard.jsx)
- [frontend/src/pages/student/StudentTimetable.jsx](../frontend/src/pages/student/StudentTimetable.jsx)
- [frontend/src/routes/roleRoutes.js](../frontend/src/routes/roleRoutes.js)
- [frontend/src/services/api.js](../frontend/src/services/api.js)

### Added files

- [alembic/versions/20260908_timetable.py](../alembic/versions/20260908_timetable.py)
- [backend/app/timetable/__init__.py](../backend/app/timetable/__init__.py)
- [backend/app/timetable/api.py](../backend/app/timetable/api.py)
- [backend/app/timetable/bootstrap.py](../backend/app/timetable/bootstrap.py)
- [backend/app/timetable/contracts.py](../backend/app/timetable/contracts.py)
- [backend/app/timetable/models.py](../backend/app/timetable/models.py)
- [backend/app/timetable/reads.py](../backend/app/timetable/reads.py)
- [backend/app/timetable/runner.py](../backend/app/timetable/runner.py)
- [backend/app/timetable/service.py](../backend/app/timetable/service.py)
- [backend/app/timetable/solver.py](../backend/app/timetable/solver.py)
- [backend/app/timetable/validation.py](../backend/app/timetable/validation.py)
- [docs/TIMETABLE.md](../docs/TIMETABLE.md)
- [docs/TIMETABLE_VERIFICATION.md](../docs/TIMETABLE_VERIFICATION.md)
- [frontend/src/pages/timetable/Timetable.jsx](../frontend/src/pages/timetable/Timetable.jsx)
- [frontend/src/test/timetable.test.jsx](../frontend/src/test/timetable.test.jsx)
- [scripts/benchmark_timetable.py](../scripts/benchmark_timetable.py)
- [scripts/bootstrap_timetable_data.py](../scripts/bootstrap_timetable_data.py)
- [tests/test_timetable_api.py](../tests/test_timetable_api.py)
- [tests/test_timetable_postgresql.py](../tests/test_timetable_postgresql.py)
- [tests/test_timetable_runner.py](../tests/test_timetable_runner.py)
- [tests/test_timetable_solver.py](../tests/test_timetable_solver.py)
- [tests/timetable_fixtures.py](../tests/timetable_fixtures.py)
