# Physical Library Agent

The Library Agent is a deterministic, tool-backed component in the existing agent registry. Its role-checked FastAPI endpoints use the current SQLAlchemy session, recipient-owned Notification service, JWT identities, React app and secure parent-child links. It does not change the research definition of `CORE_AGENTS`, add chat mutation tools, or start background jobs.

## Reference adopted and adapted

The supplied `MAWOS-library_agent.zip` was inspected for catalogue, acknowledgement slips, physical pickup, return requests with optional reviews, counter service, separate fines and explainable recommendations. No ZIP project files or static frontend were copied.

This implementation adds immediate stock holds, consistent PostgreSQL row locks, ownership filters, decimal money, source/event uniqueness, non-destructive archiving, normalized department relations and dedicated librarian identities. Unlike the reference, a return request does **not** stop fine accrual. Reviews are stored on request but published only after that issue has been physically returned. Notifications commit with the business action, without relying on asynchronous bus delivery.

## Policy and timestamps

| Environment variable | Default |
|---|---:|
| `MAWOS_LIBRARY_LOAN_DAYS` | 7 |
| `MAWOS_LIBRARY_PICKUP_DEADLINE_DAYS` | 2 |
| `MAWOS_LIBRARY_FINE_PER_OVERDUE_DAY` | 1 |
| `MAWOS_LIBRARY_MISSED_PICKUP_FINE` | 10 |
| `MAWOS_LIBRARY_FINE_BLOCK_THRESHOLD` | 25 |
| `MAWOS_LIBRARY_RECOMMENDATION_LIMIT` | 5 |

Monetary settings use finite nonnegative `Decimal` values with at most two decimal places; stored amounts use `NUMERIC(12,2)`. Recommendations are capped at 50. Both `.env.example` and Compose expose the settings. Restart the backend after changing policy configuration.

Storage follows the repository's naive-UTC timestamp convention; JSON timestamps explicitly include UTC offsets. React displays dates in Asia/Kolkata (IST). Loans end at the issue timestamp plus seven days. Overdue days are the nonnegative difference between the physical return/current date and due date **in Asia/Kolkata**. A return later on the due date incurs zero calendar-day fine; the next IST calendar day incurs ₹1, five calendar days incur ₹5. A pending/rejected return keeps accruing. Read endpoints do not write daily fine rows.

On physical return, a positive overdue amount becomes one fixed `OVERDUE_RETURN` fine. On missed pickup, expiry creates one `MISSED_PICKUP` fine. Each has a unique `(source_type, source_id)`. Unpaid **finalized** fines of ₹25 or more block reservation, direct issue and pickup; live estimates are displayed separately and are not double-counted as finalized debt. Staff record payment only after collecting it in person. Paying enough to reduce unpaid debt below ₹25 immediately unblocks borrowing.

## Inventory and retention

All borrower mutations acquire locks in the order **Student → Book → reservation/issue**, or **Student → fine** for collection. Catalogue edits lock Book. PostgreSQL READ COMMITTED is the supported deployment isolation level. Student locking serializes eligibility against simultaneous fine creation/collection; book locking serializes competing holds, issues, returns and stock edits.

A reservation holds one copy immediately. Pickup consumes that existing hold; it does not decrement availability again. Cancel/expiry releases a pending hold once. Physical return releases the issued copy once. Duplicate pickup, return and payment confirmations return the existing result. Cancellation after the deadline expires the reservation and charges the missed-pickup fine, preventing cancellation from bypassing that policy.

Stock editing accepts only `total_copies`; availability is derived using the existing held/issued count. Lowering total below committed copies is rejected. Archiving prevents new reservations/direct issues and preserves all history. Existing pending holds can still be collected before their deadline. Expired holds stay unavailable until maintenance runs, or the owner attempts cancellation; reads have no hidden side effects.

Slip codes use `secrets.randbelow`, are six digits including leading zeroes, and remain unique across history. Database uniqueness plus savepoint retries handles random collisions. Code allocation returns a retryable error if allocation fails; codes are not recycled. Slip JSON/PDF is owner-only and `no-store`; verification uses a staff-only POST so codes are not placed in access-log URLs. PDF slips contain the reservation ID, USN, ISBN, code, status and IST deadline; the React acknowledgement also shows the full book/student names.

## Access and API

All paths below have the `/api` prefix. List endpoints default to 20 rows and cap at 100; use `offset` and `limit`. Catalogue search is literal (escaped SQL wildcards), bounded to 128 characters, and sorted alphabetically. Review responses contain rating/comment/book ID, without names or USNs. React renders comments as text.

| Role | Endpoints |
|---|---|
| Authenticated | `GET /library/books`, `GET /library/books/{id}`, `GET /library/books/{id}/reviews` |
| Student, own identity only | `GET /student/library/{summary,reservations,issues,fines,recommendations}` |
| Student | `POST /student/library/reservations`, `POST /student/library/reservations/{id}/cancel` |
| Student, own reservation only | `GET /student/library/reservations/{id}/slip`, `GET /student/library/reservations/{id}/slip.pdf` |
| Student, own issue only | `POST /student/library/issues/{id}/return-request` |
| Librarian/Admin | `GET /librarian/library/summary`, `GET /librarian/library/records/{pickups,returns,issues,overdue,fines,reviews}` |
| Librarian/Admin | `POST /librarian/library/verify-slip`, `POST /librarian/library/reservations/{id}/pickup` |
| Librarian/Admin | `POST /librarian/library/issues`, `POST /librarian/library/issues/{id}/return`, `POST /librarian/library/issues/{id}/reject-return` |
| Librarian/Admin | `POST /librarian/library/fines/{id}/paid` |
| Librarian/Admin | `POST /librarian/library/books`, `PUT /librarian/library/books/{id}`, `POST /librarian/library/books/{id}/archive` |
| Admin only | `GET/POST /admin/librarians`, `PUT /admin/librarians/{user_id}` |
| Parent, linked active child only | `GET /parent/children/{usn}/library`; also included in existing child dashboard |

Admin does not impersonate anyone: `/admin/library` uses the same staff-authorized workflow endpoints. HOD, faculty and principal receive no borrower reports or librarian powers. Parent data contains current borrowed titles/status/due dates/live estimates and paid/unpaid totals, with no slips, reservation IDs, borrower history or mutation controls.

Librarian creation generates a temporary password, returns it once with `Cache-Control: no-store`, and stores only a PBKDF2 hash. The existing password-change gate applies. The admin can rename/deactivate/reactivate the profile; inactive accounts are rejected at login and on subsequent JWT-authenticated requests.

## Migration — manual operator action only

New revision: **`20260915_library`**, predecessor: **`20260914_parent_portal`** (the actual repository head before this change).

It adds books, department mappings, reservations, issues, fines, reviews and librarian profiles, and extends the existing users role check. As with the parent migration, downgrade intentionally preserves all tables, data and the expanded role constraint; it only allows the Alembic revision pointer to move back. Re-upgrade tolerates the retained structures. This is a history-preserving rollback, not schema removal.

After reviewing the change and building the updated backend image, the exact manual migration command from the project directory is:

```bash
docker compose run --rm --no-deps backend python -m alembic upgrade 20260915_library
```

This command targets the database selected by the existing Compose environment. It was **not run** during implementation. No live migrations, seeds, resets or database writes were performed.

## Reservation expiry maintenance

Run one bounded batch explicitly:

```bash
docker compose run --rm --no-deps backend python -m backend.app.library.maintenance --batch-size 200
```

Or, in an already configured native environment:

```bash
.venv/bin/python -m backend.app.library.maintenance --batch-size 200
```

A batch commits each expired reservation independently, releases its copy, creates its missed-pickup fine at most once, and persists recipient-owned notifications. Rerunning and even overlapping invocations safely re-check locked status. Increase batch size up to 1000 or run additional batches to clear a backlog.

Schedule the Docker command every five minutes from the project directory using **one operator-managed cron entry or one dedicated worker**. No library scheduler runs in FastAPI startup or each backend container. Use an absolute working directory in cron; monitor its exit code and reported expired count.

## Versioned catalogue import

`data/library_catalogue_v1.json` contains 150 unique ISBN-13 catalogue entries and 1,110 physical copies. Each record retains its Open Library edition/work sources. Department codes are recommendation tags only; catalogue browsing and reservation remain available to every authenticated student.

From an environment configured with the intended MAWOS PostgreSQL URL, preview the entire operation (the default mode) with:

```bash
.venv/bin/python scripts/import_library_catalogue.py --dry-run
```

Dry-run validates all records, starts a read-only PostgreSQL transaction, reports catalogue and database skip/add counts, and performs no DML. Review that report before applying. The live import is a separate, explicit operator action:

```bash
MAWOS_ALLOW_LIBRARY_CATALOGUE_IMPORT=true \
  .venv/bin/python scripts/import_library_catalogue.py --apply
```

Apply inserts only ISBNs absent from `books`, creates only their department relevance rows, and commits the batch in one transaction. An existing ISBN is skipped without changing any of its fields or stock. Repeating apply therefore adds no duplicates. The script does not create borrowers, accounts, circulation records, fines, reviews, notifications, or any other data. It was not run against the live database during implementation.

## Academic Assistant catalogue access

Authenticated students can ask the Academic Assistant to find active books by title, author, ISBN, category, description keyword, or department relevance, and to report current copy availability. These requests are routed before general AI and execute a backend-owned read-only Library Agent query. Results contain catalogue metadata and stock only—never database IDs, borrower identities, reservations, issues, fines, payments, or credentials.

Exact availability and search answers are rendered deterministically by FastAPI. For broad recommendations, FastAPI may send the configured generative provider a short list of books that FastAPI already established as available, containing only an index, title, author, and category. Catalogue strings are marked as untrusted data, the original prompt and live stock counts are not included, and the provider may return only validated list indices. FastAPI renders the final titles and refreshed stock; invalid or unavailable model output falls back to deterministic catalogue results. Groq and Ollama have no database credentials or database network access.

Assistant catalogue access is read-only and student-scoped. Reservations, holds, renewals, issues, returns, reviews, fine actions, catalogue edits, and information about other borrowers are not assistant capabilities. Students use `/student/library` for normal authorized library workflows.

## Exact browser acceptance checklist

Use a separate authorized test/staging deployment with this migration applied, two student accounts, an admin, and a parent linked to only one of those students. This checklist is provided for operator acceptance; no live browser actions or live data setup were performed.

**Admin**

1. Sign in and open `/admin/librarians`. Create a librarian with a unique lowercase username and display name. Save the once-shown password, then dismiss it; reload and confirm it cannot be retrieved.
2. Open `/admin/library`. Add a book with a unique 10/13-character ISBN, title, author, category, a valid department code and one total copy. Confirm its displayed Book ID and availability of one.
3. After that copy is held/issued, edit total copies to zero; expect a validation conflict. Increase total and confirm derived availability. Archive the book; verify history remains and new reservations/direct issues fail.
4. Rename, deactivate and reactivate the librarian in `/admin/librarians`. Verify a deactivated librarian's next login/API action is denied. Admin retains direct oversight access throughout.

**Librarian**

1. Choose Librarian on `/login`, sign in using the temporary credentials, and complete `/change-password`. Confirm landing at `/librarian/library` and that Student/Admin pages are guarded.
2. For the student's reservation below, enter its six-digit code into **Verify pickup slip**. Confirm student, title and deadline; click **Confirm verified pickup** only when handing over the book. Repeat the API confirmation if testing retries; there must be one issue and one inventory hold.
3. Use **Direct counter issue** with an active Book ID and student USN. Confirm due date in **All issues / Counter returns**. Confirm an immediate physical return there; availability increases once.
4. Open **Pending returns**. Reject a request with a nonempty reason. After a new request, click **Confirm physical return** when the book is handed over. Inspect **Overdue report** and **Fine collection**; the final overdue fine is created once.
5. In **Fine collection**, collect money physically, then click **Confirm cash collected / Mark paid**. Repeated confirmation does not duplicate payment or notifications. Check catalogue details and **Reviews** for the now-published plain text review.

**Student**

1. Open `/student`; the Library card shows issued count, unpaid fines, current estimate and next due date. Follow **Open Library** to `/student/library`.
2. Search by title, author, ISBN and category. Use **Details & reviews**; check availability, average rating and plain text comments. Inspect explainable recommendations.
3. Reserve the one-copy book. Confirm navigation to `/student/library/slips/{reservationId}`, six-digit code including any leading zeroes, status and IST deadline. Download/open the PDF. A second student attempting the last copy must receive an unavailable conflict.
4. Go back to **My reservations**; cancel before the deadline and verify the copy becomes available once. Reserve again and have the librarian confirm pickup. Inspect due date in **Borrowed books & history**.
5. Request return with an optional 1–5 rating and comment. Confirm pending status and the notice that fines continue until physical return. After rejection, confirm the reason is shown and another request is possible.
6. With overdue fixtures in the isolated environment, compare one-day and five-day estimates (₹1 and ₹5). After physical confirmation, verify the estimate leaves current borrowed totals and a single fixed amount appears in **Fine history**.
7. With unpaid finalized fines at ₹25 or more, verify reserve/pickup/direct issue fails. After staff collect enough to fall below ₹25, verify borrowing succeeds. There is no online payment button.
8. Inspect notifications for reservation, issue, expiry, return, fine creation and payment. Sign in as the second student and try the first student's slip URL or mutation IDs; expect denial and no private data.

**Parent**

1. Sign in and open `/parent`; select the linked child and inspect **Library** (`/parent#library`). Verify borrowed titles, due dates, live overdue amounts and paid/unpaid totals match the child's records.
2. Switch between linked children if applicable. Try `/api/parent/children/{unlinkedUsn}/library`; expect 403. Disable a link through the existing admin parent workflow and confirm access is removed.
3. Verify there are no reserve/return/payment controls, slip codes or history of other borrowers. Parent, HOD, faculty and principal must be denied access to `/librarian/library` and staff report APIs.

Repeat at mobile width: use the sidebar menu, search, tabs, forms and slip download; check loading, empty, validation and retry states. Refresh views after changing test timestamps to obtain fresh calendar-day estimates.

## Verification and isolated PostgreSQL tests

Commands used:

```bash
.venv/bin/python -m pytest -q
.venv/bin/python -m pytest tests/test_library_postgresql.py -q -rs
(cd frontend && npm test -- --reporter=dot)
(cd frontend && npm run build)
.venv/bin/python -m pip check
docker compose config --quiet
git diff --check
```

`tests/test_library_postgresql.py` requires an explicitly configured `MAWOS_POSTGRES_TEST_URL` using `postgresql+psycopg` and database name **`mawos_test`**. It validates the URL before connecting and `current_database()` before creating or cleaning a unique temporary schema. It tests upgrade/downgrade/re-upgrade, constraints, concurrent attempts for the last copy, and concurrent pickup/return/expiry with notification deduplication. It never falls back to the live URL. SQLite tests do not establish PostgreSQL locking correctness.

## Excluded scope

No public registration, payment gateway, online fine payment, SMS/email, QR attendance, destructive deletion, copied static frontend, or per-container library scheduler. No ML recommendation model, external catalogue ingestion, or chat-triggered library mutations were added. No commit, push or deployment was performed.

## Results from this implementation

- Full backend suite: **536 passed, 24 skipped**. Library workflow coverage: **19 passed**.
- Focused PostgreSQL library run: **3 skipped** because `MAWOS_POSTGRES_TEST_URL` is not configured. Migration and real PostgreSQL locking remain unverified in this environment.
- Full frontend suite: **131 passed across 16 files**.
- Production frontend build: passed; Vite reports a main-chunk size warning (approximately 840 kB uncompressed).
- `pip check`, `docker compose config --quiet`, and `git diff --check`: passed.
- Existing test output includes Starlette/joblib deprecations and React `act` warnings in scholarship tests.
- Browser acceptance checklist: documented above, not executed against live data.

## Files changed for Library

This manifest describes only this task's additions/edits. Unrelated dirty worktree changes already present were preserved.

New files:

- `alembic/versions/20260915_library.py`
- `backend/app/agents/library.py`
- `backend/app/library/__init__.py`
- `backend/app/library/api.py`
- `backend/app/library/models.py`
- `backend/app/library/schemas.py`
- `backend/app/library/service.py`
- `backend/app/library/maintenance.py`
- `frontend/src/pages/library/Library.jsx`
- `frontend/src/test/library.test.jsx`
- `tests/test_library.py`
- `tests/test_library_postgresql.py`
- `docs/LIBRARY.md`

Existing worktree files extended:

- `.env.example`, `docker-compose.yml`, `README.md`
- `backend/app/agents/__init__.py`
- `backend/app/api/routes.py`, `backend/app/api/schemas.py`
- `backend/app/auth.py`, `backend/app/config.py`, `backend/app/main.py`, `backend/app/models.py`
- `backend/app/parent_portal.py`
- `backend/app/provenance.py` (convert policy money only for approximate natural-language claim comparison)
- `frontend/src/App.jsx`, `frontend/src/routes/roleRoutes.js`, `frontend/src/layouts/AppLayout.jsx`
- `frontend/src/services/api.js`
- `frontend/src/pages/auth/LoginPage.jsx`
- `frontend/src/pages/student/StudentDashboard.jsx`
- `frontend/src/pages/parent/ParentPortal.jsx`
