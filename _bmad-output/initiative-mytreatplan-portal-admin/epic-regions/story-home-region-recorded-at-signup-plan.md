---
title: 'Home region recorded at signup'
type: 'feature'
ticket: '2'
created: '2026-11-02'
status: 'built'
baseline_revision: '5148c12b6bc8ff4be15f42b10c537fa23f959246'
route: 'full'
route_source: 'auto'
risk: 'medium'
review: 'quick'
review_source: 'pinned'
lenses_ran: ['quick']
review_loop_iteration: 0
context: []
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** R2 / AD-14 say each doctor's home region is stored at signup and never recomputed. Admin region limits (AD-15) and later epics filter on it. The User model has no such field.

**Approach:** Add an immutable `home_region` to `accounts.User`. A new account gets the instance's region (`settings.REGION_PROFILE.code`) when it is created, whatever its country. Existing accounts are filled by a data migration. The admin shows it read-only.

## Boundaries & Constraints

**Always:**
- The value comes only from the instance's region profile at creation, never from the country map or the user's country. Nothing recomputes it later.
- Allowed values are the profile codes (`regions.profiles.PROFILES` keys). No region name is written as a literal outside `regions/profiles.py`, because `regions/tests/test_guard.py` enforces that.
- The migrations come after `accounts.0011_uid_unique`. Rows the previous release inserts during the deploy's `migrate` (release.sh migrates before switching) must not fail.
- The field is never in a form, and it is shown read-only in the user admin.
- Decision (user, 2026-11-02): existing accounts (and `''` rows) are filled with the instance's region (`settings.REGION_PROFILE.code` at migrate time): eu on dev and production, ae on dev-ae. This replaces the ticket's literal "existing accounts get eu", which it equals everywhere except dev-ae.

**Never:**
- No signup warning or refusal (entry 3), no geo-IP, and no admin-site filtering (later epics).
- No change to the PendingRegistration model.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| Signup on eu | the final signup step on an instance with REGION=eu | `user.home_region == 'eu'` | — |
| Signup on ae | the same, with REGION=ae (override_settings REGION_PROFILE) | `'ae'`, even with the country set to ES | — |
| createsuperuser / create_user | the manager creates a user | the instance region | — |
| Map change | the user's country's state edited in `Country` after signup | home_region unchanged | — |
| Existing rows | users present before the migration | the instance's region (eu on dev, ae on dev-ae) | — |
| Old-release insert | a raw INSERT without the column, after the add step | succeeds; the next step or fill treats it per Design Notes | — |

</frozen-after-approval>

## Code Map

- `accounts/models.py` -- `User` (line ~57). `uid` (~79) is the precedent for an immutable, `editable=False` field with a `db_default` that covers the deploy window. Add `home_region` after `uid`.
- `accounts/managers.py` -- `_create_user`. No change is needed if the model default covers it, but test it.
- `signup/views.py` `_create_account` (~390) -- builds `user_model(email=..., password=..., **USER_FIELDS)`. The default applies there, so no change is needed. Test through the existing signup flow helpers in `signup/tests`.
- `accounts/migrations/0009_uid_add.py`, `0010_uid_fill.py`, `0011_uid_unique.py` -- the three-step pattern (add nullable with db_default, fill in batches, then make required) and its comments.
- `regions/profiles.py` -- `PROFILES`, `RegionProfile.code`. A choices helper (e.g. `REGION_CHOICES`) belongs here. `settings.REGION_PROFILE` is the instance's profile.
- `regions/tests/test_guard.py` -- forbids region-name comparisons outside the profile module. Run it.
- `accounts/admin.py` -- fieldsets (~100) and `readonly_fields` (~117). Add `home_region` next to `uid` ("Global id" section, or its own "Region" section), read-only.
- `accounts/tests/test_user.py` -- where the model tests go. 1.8's `test_an_insert_without_uid_gets_its_own_from_the_database` is the raw-INSERT precedent.

## Tasks & Acceptance

**Execution:**
- [x] `regions/profiles.py` -- the choices built from PROFILES -- one source for the allowed codes
- [x] `accounts/models.py` -- `home_region` CharField(max_length=2, choices, editable=False), with a callable default returning `settings.REGION_PROFILE.code` and the db_default from Design Notes -- set at creation only
- [x] `accounts/migrations/0012_…`, `0013_…` -- add the field, then fill existing and `''` rows with the instance's region -- after 0011
- [x] `accounts/admin.py` -- show it read-only
- [x] tests (`accounts/tests/test_user.py`, `signup/tests`) -- every matrix row, including a migration test (rows created before 0012 read the filled value after 0013), using the migration-test pattern from 1.8 if present

**Acceptance Criteria:**
- Given dev after deploy, when a new doctor signs up, then the Django shell (or admin) shows `home_region` eu. On dev-ae, a new signup shows ae.
- Given `makemigrations --check`, when run with REGION=eu and with REGION=ae, then neither reports changes (the model state doesn't depend on settings).

## Design Notes

The database default must not depend on settings, because migrations are frozen and makemigrations would differ between regions. So `db_default=''` (region-neutral). Rows the old release inserts during `migrate` get `''`, and the fill migration sets every `''` row to the fill value. The field keeps `db_default=''`. The Python `default` callable gives every ORM-created row the instance region. Residual risk: a row inserted by the old release after the fill migration has run (a few seconds, during release.sh's createcachetable and switch) keeps `''`. Record this in a model comment. Readers treat `''` as "created by the previous release", and the next release's fill can be re-run if one appears (check: `User.objects.filter(home_region='').count()` on staging after the deploy).

## Verification

**Commands:**
- the suite command (local-dev-notes) -- expected: all pass except the 3 known PostgreSQL-only failures
- `REGION=ae python manage.py makemigrations --check --dry-run` and the same with eu -- expected: "No changes detected"

**Manual checks (if no CLI):**
- Staging (user): after deploy, a new signup on dev and on dev-ae, then `User.objects.values_list('home_region', flat=True)` per site through the Django shell: eu on dev; on dev-ae, ae for the new one. Existing rows: eu on dev, ae on dev-ae.

## Implementation Notes

- `regions.profiles.REGION_CHOICES` = `(code, code)` per profile; `accounts.models.instance_region()` is the Python default; `db_default=''`.
- Migrations: `accounts/0012_home_region_add` (AddField, db_default ''), `0013_home_region_fill` (one UPDATE of `home_region=''` rows to `settings.REGION_PROFILE.code`; `fill_home_regions` importable for a re-run). No third "make required" step: the column is NOT NULL from 0012.
- Admin: own "Region" fieldset, read-only. Spanish strings added to `locale/es/LC_MESSAGES/django.po` ("home region", "Region"), required by the catalogue-completeness test.
- Tests: `accounts/tests/test_user.py` (home region section: choices, manager/createsuperuser per region, not recomputed after a map change and a save on another region, not in a form, admin read-only, raw INSERT without the column then fill, migration 0011 -> 0012 -> leaves with ae), `signup/tests/test_home_region.py` (full signup on eu and ae with country ES).
- Verified: full suite 1591 passed, 3 known PostgreSQL-only failures; `makemigrations --check --dry-run` with REGION=eu and ae: no changes.

## Plan Change Log

## Review Triage Log

### Pass 1 (quick, 2026-11-02): high 0, medium 0, low 0, false 0, maybe-false 0

No findings. Reviewer ran test_user, test_home_region, test_guard (38 passed) and makemigrations --check under REGION=eu and ae (no changes). Noted, not defects: 0012 freezes `accounts.models.instance_region` as its default (keep a stub there if it ever moves); a row the old release inserts after 0013 keeps `''` (the accepted residual risk; re-run `fill_home_regions` from the shell).
