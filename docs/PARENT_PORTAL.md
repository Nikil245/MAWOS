# Parent Portal

MAWOS parents have independent `users` credentials with role `parent`; student
credentials are never shared or impersonated. Admin creates the account and its
explicit `parent_students` links. All child reads first authorize an active
parent profile and active mapping in PostgreSQL.

The first-login policy is intentionally small and compatible with existing
authentication: `users.must_change_password` is set for every Admin-created
parent. A securely generated temporary password is returned only in the create
response, never stored in plaintext, and the parent must replace it through the
authenticated password-change endpoint before any protected data API is usable.
MAWOS continues to use its existing salted PBKDF2 password implementation.

## Manual migration

After backing up and selecting the intended database explicitly, an operator may
run:

```bash
MAWOS_ENV=production MAWOS_DATABASE_MODE=external MAWOS_DATABASE_URL='postgresql+psycopg://…/mawos' MAWOS_JWT_SECRET='…' .venv/bin/alembic upgrade 20260914_parent_portal
```

Do not run the migration through a test command or against an unverified target.

## Excluded future scope

- Payment gateway or parent fee edits
- Parent public signup, invitations by SMS, OTP, or SMS login
- Leave approval, messaging, and QR attendance
- Parent copies of student placement/scholarship notifications
- Attendance-shortage, fee-due, and exam-ineligibility parent notifications
  until each module has a trustworthy source transaction and deduplication key
- A configurable institutional marks pass threshold (the current marks policy
  defines CIE maximums, but no pass/fail threshold, so the portal does not invent one)
