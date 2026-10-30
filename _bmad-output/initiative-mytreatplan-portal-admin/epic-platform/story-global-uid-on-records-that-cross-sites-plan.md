---
title: 'Global uid on records that cross sites'
type: 'feature'
ticket: '8'
created: '2026-10-30'
status: 'built'
baseline_revision: 'a07788629b9996f5fa8ecc2f2cb3aadc89882e27'
route: 'full'
route_source: 'auto'
risk: 'medium'
review: 'quick'
review_source: 'pinned'
lenses_ran: ['quick']
review_loop_iteration: 0
context:
  - '{project-root}/_bmad-output/initiative-mytreatplan-portal-admin/architecture-portal-admin/architecture-portal-admin.md'
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** AD-5 says a record that crosses sites is referenced only by a UUID and integer keys never leave an instance. Part-1 tables that will cross sites (`User`, `ContactMessage`, `LegalDocument`, `LegalChoice`) have integer keys and no UUID.

**Approach:** Add an immutable, unique `uid` UUID to those four models, backfilled for existing rows with a distinct value each (add nullable → fill → make unique and non-null, in that order), and a convention test: every model marked as crossing sites must have a `uid` or a UUID primary key. `eventlog.Attempt` and `signup.PendingRegistration` already have UUID keys.

## Boundaries & Constraints

**Always:** migrations run safely on PostgreSQL with existing data (staging has real rows) and on SQLite; `uid` is never editable in forms or the Django admin; existing integer keys and foreign keys stay as they are.

**Never:** no change to primary keys; no data sent anywhere (epic-admin-interface uses `uid` later).

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| Existing rows | Database with users, contact messages, legal documents and choices | After migrate each row has a distinct uid | — |
| New row | Create a user | uid set automatically, unique | — |
| Edit | Save the user again | uid unchanged | — |
| Convention | A model marked crossing without uid or UUID pk | Convention test fails naming it | — |

</frozen-after-approval>

## Code Map

- `accounts/models.py:54` `User`, `mytp_publicsite/models.py:32` `ContactMessage`, `legal/models.py:37` `LegalDocument`, `:100` `LegalChoice` -- add `uid = UUIDField(default=uuid.uuid4, unique=True, editable=False)` via three-step migrations per app (AddField null=True without default unique; RunPython filling uuid4 per row in batches; AlterField unique non-null).
- Marking: a class attribute or a small registry (`CROSSES_SITES = True`) read by the convention test in a shared tests module; also mark `eventlog.Attempt` and `signup.PendingRegistration` (UUID pk).
- Django admin (`accounts/admin.py` etc.) -- show `uid` read-only where the model is listed.

## Tasks & Acceptance

**Execution:**
- [x] the four models and their migrations (per app, three steps).
- [x] the crossing-sites marker and the convention test.
- [x] tests for backfill distinctness, auto-set on create, immutability on save.

**Acceptance Criteria:**
- Given a copy of staging-like data, when migrations run, then every existing row has a distinct uid.

## Implementation Notes

- Setup: this story is built in the worktree `../mytreatplan-1.8` (branch story/1.8-global-uid) while 1.6 is built in the main checkout; work only in this worktree; the venv `../.venv` is shared (run its python from this worktree's root); do not commit or push.
- Database default: `uid` also has `db_default=UUID4()` (Django's function: `gen_random_uuid()` on PostgreSQL, a registered `UUIDV4()` on SQLite), with `default=uuid.uuid4` kept for the ORM. It is set already in each app's add step and kept in the unique step, so rows the previous release inserts without `uid` (release.sh migrates before restarting gunicorn, and after an automatic rollback) each get their own value instead of failing on NOT NULL or sharing one UUID between the fill and unique steps. On PostgreSQL the volatile default also fills existing rows at the add step; the fill step stays for any row still null. Covered by `test_an_insert_without_uid_gets_its_own_from_the_database` (raw SQL INSERT without `uid`), passing on SQLite and on a local PostgreSQL 17.
- PostgreSQL verification (local PostgreSQL 17, throwaway cluster, before the db_default change): full migrate, then the three apps rolled back to their pre-story migrations (accounts 0008, legal 0002, mytp_publicsite 0001), seeded through the historical models with 60 users, 1203 contact messages (more than the fill batch of 500), the 4 legal documents and 240 legal choices, then `migrate` forward: for every table row count = distinct uids = non-null uids, and `uid` is `uuid NOT NULL` with a UNIQUE constraint. The non-browser suite passed on that PostgreSQL (1456 passed), and `mytp_publicsite/tests/test_cross_sites.py` passed again on PostgreSQL after the db_default change.

## Plan Change Log

## Review Triage Log

- Pass 1 (quick): 5 findings; high 1, medium 1, low 1, false 1, plan bookkeeping 1. Patched 3, rejected 1, recorded 1.
  - high, patch: `uid` was NOT NULL without a database default, so during release.sh's migrate (old release still serving) and after an automatic rollback the old code's INSERTs into users, contact messages and legal tables would fail. Added `db_default=UUID4()` from the add step; a raw-SQL insert test proves distinct values.
  - medium, patch (same root cause): rows inserted between the fill and unique steps would share one UUID and break the unique step; the database default fills them.
  - low, reject: `editable=False` keeps uid out of forms and the admin but code could still reassign it; unlikely in practice and a full guard would add machinery.
  - false: the PostgreSQL acceptance check was run (60 users, 1203 messages, 4 documents, 240 choices; row count = distinct = non-null), only not recorded; now in Implementation Notes.
  - patch: Execution boxes ticked.
- After patches: full suite 1487 passed, 3 known PostgreSQL-only failures; `check` and `makemigrations --check` clean.

## Verification

**Commands:**
- Portal full suite, from the repo root (non-secret test keys; venv `../.venv`; browser tests need the sandbox off): `export FIELD_ENCRYPTION_KEYS="test1:dGVzdC1vbmx5LWZpZWxkLWtleS1ub3Qtc2VjcmV0ISE=" BLIND_INDEX_KEY="dGVzdC1vbmx5LWJsaW5kLWluZGV4LWtleS0zMmJ5dGU="; export DATABASE_URL="sqlite:///$TMPDIR/x18.sqlite3"; ../.venv/bin/python -m pytest -q -p no:cacheprovider --tb=line` -- expected: all pass except the 3 known PostgreSQL-only tests in `accounts/tests/test_crypto.py`.
- `../.venv/bin/python manage.py makemigrations --check --dry-run` -- expected: no changes after the new migrations.
- If a local PostgreSQL is available: migrate a database with sample rows forward from the previous state -- expected: distinct uids.
