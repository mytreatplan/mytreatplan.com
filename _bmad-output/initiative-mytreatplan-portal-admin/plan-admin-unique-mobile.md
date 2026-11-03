---
title: 'Unique mobile for admin users'
type: 'bugfix'
ticket: ''
created: '2026-11-03'
status: 'built'
baseline_revision: 'e6a70a934bf61f903036387ad6ea8a3cb948a923'
route: 'full'
route_source: 'auto'
risk: 'low'
review: 'quick'
review_source: 'pinned'
lenses_ran: ['quick']
review_loop_iteration: 0
context: [] # code repo: mytp-admin, branch from origin/master after the date/region filters PR is merged. Patterns: staff.User.email_index (blind index set in save, unique), mytp_core.crypto.phone_index, 3.9's IntegrityError-to-form-error handling in staff/admin_users.py.
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** The user found (2026-11-03, 3.9 check) that two admin users can be given the same mobile. In production that would send one person's SMS codes to another. `staff.User.phone` is encrypted and has no blind index, so its uniqueness can't be checked.

**Approach:**
- Add a `phone_index` blind index to `staff.User` (mytp-core's `phone_index`), set on every save like `email_index`, and unique among users who have a mobile.
- Accept mobiles only in international E.164 form.
- Refuse a duplicate with a form error on create and edit, including a concurrent duplicate.
- A migration fills the index for existing users.

## Boundaries & Constraints

**Always:**
- **The index.** `phone_index = CharField(max_length=BLIND_INDEX_LENGTH, blank=True, editable=False, db_index=True)`. It's set in `save()` from the normalized mobile (blank when there's no mobile), and the `update_fields` expansion works as it does for `email_index`.
- **The constraint.** A `UniqueConstraint` on `phone_index` with the condition that it isn't blank, so any number of users can have no mobile.
- **Normal form and check.** Spaces, dashes, dots and brackets are removed, then the mobile must be `+` followed by 8 to 15 digits (first digit 1–9); otherwise the form error "Enter the mobile in international format, e.g. +34600000000." The normalized value is stored and indexed. The form error reads "This mobile is already used by another admin user."
  - It's checked on create and edit.
  - A concurrent duplicate (IntegrityError) becomes the same form error, as with the email in 3.9.
- **Deleted users:** a logical delete clears the user's mobile and its index (in `delete_user`, audited in the same `delete` entry as "changed"), so the number can be given to another user.
- Decisions (user, 2026-11-03): a deleted user's mobile is freed on delete (sign-ups are controlled by the Sysadmin); only E.164 international format is accepted.
- **Migration:**
  1. Add the field.
  2. Backfill: decrypt and normalize each existing mobile, then compute the index. A mobile that can't be normalized is left as is, with its index computed from the trimmed value.
  3. Add the constraint.

  If existing users already share a mobile, the migration stops with a message naming their UUIDs (never the number), so the Sysadmin can fix them first. admindev has a handful of users.
- **Audit:** `phone_index` stays in `AUDIT_EXCLUDED`, and `phone` stays masked.
- **Logs and errors** carry no mobile number.

**Never:**
- No phone-number library, no SMS check of the number, no change to the sign-in or the code sending.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| Duplicate on create | a new user with another user's mobile | refused | form error on mobile |
| Same number, other spacing | "+34 600 000 000" vs "+34600000000" | treated as the same number | form error |
| Duplicate on edit | change a mobile to one in use | refused | form error |
| Own mobile kept | edit a user without changing their mobile | saved | — |
| No mobile | several users without one | all allowed | — |
| Race | two concurrent saves with the same mobile | one saved | the other gets the form error |
| Deleted user's mobile | reuse a deleted user's mobile | allowed: the delete cleared it | — |
| Invalid format | "600000000" or "+34abc" | refused | form error with the expected format |
| Migration with duplicates | two existing users share a mobile | the migration stops, naming their UUIDs | — |

</frozen-after-approval>

## Code Map

All in `mytp-admin/`:
- `staff/models.py` -- `User.phone` (EncryptedCharField, blank) :89; `email_index` and `save()` (blind-index pattern, `update_fields` expansion); `AUDIT_EXCLUDED` :109 (already lists `phone_index`); `clean_fields`.
- mytp-core 0.5.0 `mytp_core.crypto.phone_index`, `BLIND_INDEX_LENGTH`.
- `staff/admin_users.py` -- `AdminUserForm` (`clean_email` shows the duplicate pattern), the `create`/`edit` IntegrityError handling and `add_errors` (3.9).
- `staff/service.py` -- `create_user`, `update_user` (`full_clean` runs `validate_unique` and the constraints), `delete_user` (also clears `phone` and `phone_index`).
- `staff/managers.py` `create_staff_user`.
- The new migration `staff/0005_user_phone_index.py`: AddField, a RunPython backfill (historical model, decrypting through the field), and AddConstraint.
- Tests: `staff/tests/test_admin_users.py`, `staff/tests/test_users.py` (migrations complete), and a migration test for the backfill and the duplicate stop.

## Tasks & Acceptance

**Execution:**
- [x] the model field, normalization, `save()`, the constraint and migration 0005
- [x] the form and view errors on create and edit (including the race)
- [x] tests for every matrix row; the README "Admin users" note

**Acceptance Criteria:**
- Given admindev after deploy (user), when the Sysadmin gives a second user a mobile already used, written with spaces, then the form refuses it with the duplicate message.

## Verification

**Commands:**
- mytp-admin suite (CI env), `check`, `makemigrations --check` -- pass; CI on PostgreSQL -- green.

## Implementation Notes

- 2026-11-03, branch `fix/unique-admin-mobile` in mytp-admin (from origin/master e6a70a9, uncommitted).
- `staff/models.py`: `normalize_phone`, `is_valid_phone` (`E164`, now 8–15 digits; the old form regex allowed 7), `phone_index_for`, the two messages; `phone_index` field; `staff_user_phone_index_unique` with `violation_error_code='unique'`, so `full_clean` keys the error on `phone_index` (mapped to the form's `phone`).
- The race: `add_errors` maps an IntegrityError whose message contains `phone_index` (SQLite names the column, PostgreSQL the constraint) to the mobile, any other to the email. The tests take the database's real IntegrityError, so CI on PostgreSQL checks the message.
- The service also normalizes the mobile (create and update), so stored values are normalized whoever calls it. The form's input `max_length` went from 16 to 32, so a spaced mobile isn't refused for its length before it's normalized.
- Delete records the mobile as `set` → `empty` (the audit's masking) in the `delete` entry.
- Migration test: `staff/tests/test_phone_index_migration.py` (transactional, migrates to 0004 and back to the latest).
- Verified locally (SQLite): full suite 622 passed, `check` and `makemigrations --check` clean. CI on PostgreSQL not run yet.
- Review fixes (coordinator, 2026-11-03):
  - The backfill clears deleted users' mobiles and indexes before the duplicate check, so a deleted user never blocks the migration or keeps a number taken.
  - A value without digits ("n/a") gets no index, in `phone_index_for` and in the migration.
  - The migration computes every index with its own copy of `phone_index_for`, not `phone.strip()`, so it matches what `save()` computes. This replaces the plan's "computed from the trimmed value"; with mytp-core's digits-only normalization, the result is the same for any value that has digits.
  - `User.save()` normalizes the mobile and `User.clean_fields` checks the E.164 format, so the manager, the command and the service all follow the form's rule. The service's own normalization stays, so the edit compares like with like.
  - Only the three staff test files were re-run (87 passed).

## Plan Change Log

## Review Triage Log

### Pass 1 (quick, 2026-11-03): high 0, medium 1, low 3, false 2, maybe-false 0

| # | Finding | Verdict | Route | Evidence / action |
|---|---------|---------|-------|-------------------|
| 1 | The backfill indexed users already deleted: their mobile stayed taken, and a shared one stopped the migration on a user that can't be edited | medium | patch | Patched: the backfill clears phone and index for `deleted_at` rows first; test. |
| 2 | Legacy values with no digits ("n/a") all indexed as `blind_index('+')`, reported as a shared mobile | low | patch | Patched: no digits = no mobile (blank index), in the model and the migration; test. |
| 3 | The migration indexed invalid mobiles from `strip()`, the app from `normalize_phone` | low | patch | Patched: the migration uses its own copy of the same helpers; test that backfilled and saved indexes match. |
| 4 | Only the service normalized and only the form checked E.164 (manager and commands skipped both) | low | patch | Patched: `save()` normalizes, `clean_fields` checks the format; tests. |
| 5 | The race tests don't hit a real database IntegrityError | low | rejected | The mapping is tested with a captured error; the constraint name holds on PostgreSQL by reading; CI runs PostgreSQL. |
| 6 | PostgreSQL and the acceptance check not yet run | false | rejected | The PR's CI and the user's post-deploy check. |

Verified after the patches: mytp-admin suite 624 passed (SQLite, CI env), `check` clean, `makemigrations --check` no changes.
