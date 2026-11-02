---
title: 'Admin users and roles'
type: 'feature'
ticket: '1'
created: '2026-11-02'
status: 'done'
baseline_revision: 'ef6b3ec953e1121e950df91ba25e44e939167d5e'
route: 'full'
route_source: 'auto'
risk: 'high'
review: 'quick'
review_source: 'pinned'
lenses_ran: ['quick']
review_loop_iteration: 0
context: [] # code repo: mytp-admin (sibling of mytreatplan.com); baseline_revision is a mytp-admin commit
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** A1: the admin site (repo `mytreatplan/mytp-admin`, live on admindev since 1.10) has no users. Every later admin entry needs staff with exactly one of six roles, a region registry, and a way in.

**Approach:** In mytp-admin, add a `staff` app with:
- the admin's own user model: UUID primary key, email login, field-encrypted contact data with the admin site's keys, never hard-deleted;
- a single role field;
- a Region registry seeded with eu and ae, which a Commercial Regional Manager links to;
- a `create_sysadmin` command;
- password sign-in that protects every page, landing on the existing empty AdminLTE layout as the Sysadmin's dashboard frame.

This turns on mytp-core field encryption for the admin site.

## Boundaries & Constraints

**Always:**
- The user model is on `AbstractBaseUser`, with no `PermissionsMixin`, `is_staff` or `is_superuser`, because AD-17's matrix (entry 6) decides access.
- Fields:
  - `id` UUID pk; `email` (`EncryptedEmailField`) with a unique `email_index` blind index set on every save, as in the portal's `accounts.User.save()`; `phone` (`EncryptedCharField`, optional);
  - `display_name` and `title` (plain text, because AD-22 sends them in the config snapshot); `is_active`;
  - `time_zone`, validated against `zoneinfo.available_timezones()`, default `Europe/Madrid`;
  - `role`: choices sysadmin, financial_manager, commercial_manager, commercial_regional_manager, clinical_manager, clinical_user. Required, with a DB check that it's one of them;
  - `regions`: M2M to Region.
- Validation in one place (a model method used by the command, and by forms later): a role is required. A Commercial Regional Manager needs at least one region, and any other role has none.
- Never hard-deleted: `delete()` on the model and on its querysets raises. Deactivating means `is_active=False`. The logical-delete UI is entry 9.
- Argon2 is the first password hasher (add `argon2-cffi==25.1.0`), with the portal's password validators (minimum 12 characters, plus common, numeric and similarity checks).
- Login is required on every view except the login page, `/healthz` and `/robots.txt`. `NoindexMiddleware` stays first, and the release health check keeps working.
- `create_sysadmin --email --display-name` asks for the password twice with no echo. It never takes it as an argument.
- The admin never imports portal packages (`test_never_imports_portal_code`). Patterns are mirrored, not imported.
- Decision (user, 2026-11-02): admindev uses the KMS key provider from the start (`FIELD_KEY_PROVIDER=kms`), with its own admindev KMS key that the user creates (portal README steps 11–12). CI and local tests use the fixed test env keys. The README gets the admindev key steps, and `deploy/jobs.conf` runs `kmscheck`.
- Decision (user, 2026-11-02): the Region seed reads `REGION_INSTANCE_URL_EU` / `REGION_INSTANCE_URL_AE` from settings, defaulting to `https://mytreatplan.com` / `https://mytreatplan.ae`. admindev's settings.env sets the dev and dev-ae URLs before the first migrate.

**Never:**
- No 2FA, branded login page, lockout or single session (entries 2–4), no permission matrix, per-role dashboards or menu filtering (6), no lists (7), no audit (8), no user-management UI (9), no emergency reset (10), no profile or inbox (11).
- No `django.contrib.admin` (AD-11). No web signup or password reset.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| First Sysadmin | `create_sysadmin --email a@x --display-name A`, matching passwords | an active sysadmin with an encrypted email | weak or mismatched password: asks again, or exits non-zero, with nothing created |
| Duplicate email | the command for an existing email (any case) | refused | non-zero exit, message |
| Sign-in | the correct email (any case) and password on `/login/` | redirected to `/`, which shows the layout with "Sysadmin" | wrong password or inactive user: the same generic error |
| Anonymous | `GET /` or any page | 302 to `/login/?next=…` | `/healthz` and `/robots.txt` answer as before |
| No role | saving a user with role `''` | refused | ValidationError from `full_clean` / the command; IntegrityError from a raw save |
| CRM without region | role commercial_regional_manager, no regions | refused | ValidationError |
| Region on another role | role sysadmin with regions | refused | ValidationError |
| Delete | `user.delete()` or `User.objects.filter(...).delete()` | refused | raises |
| Logout | POST `/logout/` | session ended, back to login | — |

</frozen-after-approval>

## Code Map

All paths are in `mytp-admin/` (sibling of this repo) unless noted.
- `mytp_admin/settings.py`:
  - L48–65 already read the field-key settings.
  - Remove `SILENCED_SYSTEM_CHECKS` `mytp_core.E001/E002`, silence `auth.W004` (the email is unique through `email_index`).
  - Add to `INSTALLED_APPS` (L110–124) `staff`, and set `AUTH_USER_MODEL='staff.User'`, `AUTHENTICATION_BACKENDS`, `PASSWORD_HASHERS`, `AUTH_PASSWORD_VALIDATORS`, `LOGIN_URL`, `LOGIN_REDIRECT_URL`.
  - The comment at L181–184 marks the place.
- `mytp_admin/wsgi.py` -- call `mytp_core.crypto.validate_field_keys()` at startup, as the portal's wsgi does.
- `mytp_admin/urls.py` -- add `login/` and `logout/` (Django auth views, minimal template) and keep the current routes.
- `layout/middleware.py` -- `NoindexMiddleware` is unchanged. Put the login-required middleware next to it, or use Django 5.1+'s `LoginRequiredMiddleware` with `login_not_required` on healthz and robots.
- `layout/templates/layout/base.html`, `home.html` -- `home` shows the role's display label in the heading and the signed-in name with a logout button. The login template extends a bare AdminLTE login box. Entry 4 brands it.
- `layout/tests/test_settings.py` -- update L40 and L53, the "silenced" tests, to assert that the checks are active and the encrypted fields exist.
- `layout/tests/test_pages.py` -- `test_home_is_the_empty_adminlte_layout` must sign in first, and an anonymous request must redirect.
- `pytest.ini` -- add `staff` to `testpaths`. `conftest.py` has a SQLite JSON workaround and gets the fixed test keys (same as the portal's conftest).
- `.github/workflows/ci.yml` -- add the test `FIELD_ENCRYPTION_KEYS` / `BLIND_INDEX_KEY` env (values from the portal's ci.yml).
- `settings.env.example`, `deploy/staging.env.example`, `deploy/jobs.conf` (`kmscheck`), README server steps -- the admindev KMS key settings and `REGION_INSTANCE_URL_*` (decisions above).
- mytp-core `vendor/mytp_core-0.3.0` provides `EncryptedEmailField`, `EncryptedCharField`, `email_index`, `normalize_email_for_index`, `BLIND_INDEX_LENGTH`, `validate_field_keys`, and the `generate_field_keys`, `generate_kms_field_key` and `kmscheck` commands. No new mytp-core release.
- Portal patterns to mirror, read-only from mytreatplan.com: `accounts/models.py` (L57–146, the email index in save), `accounts/managers.py` (`by_email`, `get_by_natural_key`), `accounts/backends.py`, and `mytreatplan/settings.py` L415–475. Don't copy uid/CROSSES_SITES, erasure, sessions or login_guard.

## Tasks & Acceptance

**Execution:**
- [x] `staff/` app (models, manager, backend, migrations: schema, then the Region seed from the settings, `management/commands/create_sysadmin.py`) -- the model and command
- [x] settings, wsgi, urls, middleware, templates -- turn on keys and sign-in
- [x] CI, env examples, jobs.conf, README -- keys for CI and admindev
- [x] tests in `staff/tests/` and the updated `layout/tests` -- every matrix row, plus a check that the email is ciphertext in the DB row and that `makemigrations --check` is clean

**Acceptance Criteria:**
- Given admindev after deploy and the user's key setup, when the user runs `create_sysadmin` over SSH and signs in, then the page shows the AdminLTE layout headed "Sysadmin" with their name, and logout works.
- Given admindev, when `/healthz` and `/robots.txt` are fetched without a session, then they answer as before, and the release health check passes.

## Design Notes

The region registry (`staff.Region`): `code` (2 chars, primary key), `name`, `instance_url`. The app is named `staff`, not `regions`, because `regions` is a portal package that the admin's import guard forbids.

## Verification

**Commands:**
- in mytp-admin: `.venv/bin/python -m pytest -q`, `python manage.py check`, `makemigrations --check --dry-run` -- all clean
- CI on the mytp-admin PR -- green

**Manual checks (if no CLI):**
- admindev (user): set up the keys, deploy, run `create_sysadmin`, sign in, log out.

## Implementation Notes

- 2026-11-02, mytp-admin branch `story/3.1-admin-users` (uncommitted): 86 tests pass on SQLite and on a local PostgreSQL 17; `check`, `makemigrations --check` clean; `staff` migrations reverse; migrating a DB built by the current master (as admindev's) applies `staff.0001`/`0002` cleanly (the old, empty `auth_user` table stays, unused).
- Role/region check is `User.validate_access(regions)`, called by `User.objects.create_staff_user` (used by `create_sysadmin`); it is not in `clean()`, so forms can pass the regions they are about to set. `full_clean` computes `email_index` first, so a duplicate email (any case) is a ValidationError.
- Sign-in: Django's `LoginRequiredMiddleware`; `robots_txt` and `healthz` are `login_not_required` (the decorator sets an attribute on mytp-core's `healthz` function in place). `createsuperuser` raises, pointing to `create_sysadmin`.
- admindev KMS: README server step 11 — its own service key `mytp-admindev` in the staging KMS domain, dev's access certificate copied, field keys generated with the live release before merging (the new release's `migrate` and Gunicorn refuse to run without keys). `kmscheck` job at 04:05 UTC.

## Plan Change Log

## Review Triage Log

### Pass 1 (quick, 2026-11-02): high 0, medium 0, low 2, false 0, maybe-false 0

| # | Finding | Verdict | Route | Evidence / action |
|---|---------|---------|-------|-------------------|
| 1 | A stale tab's POST `/logout/` was sent to `/login/?next=/logout/`; after signing in, LoginView GETs `/logout/` → 405 | low | patch | Real: LogoutView was behind LoginRequiredMiddleware and POST-only. Patched: `login_not_required` on logout; an anonymous POST lands on `/login/`; test added. |
| 2 | README server setup: on a fresh host the first deploy (step 6) fails without keys, step 11.4 assumes a live release, and the kmscheck timer from step 3 fails until KMS is set | low | patch | Real for a fresh host (admindev already exists). Patched: notes at steps 4 and 6 (do 11.1–11.4 first, from an unpacked release; kmscheck fails until then). |

Verified after the patches: mytp-admin suite 86 passed (SQLite, CI env), `check` clean, `makemigrations --check` no changes.

## Staging

- Merged in mytp-admin PR #1 (2026-11-02), deploy 30c9392 green. Before the merge (user, README step 11.1–11.4): a new KMS service key `mytp-admindev` in the staging OKMS domain, dev's access certificate copied, the KMS and `REGION_INSTANCE_URL_*` lines in settings.env, field keys generated; `kmscheck` ok, 5 settings present.
- Step 11.6 (user): `~/deploy-admindev` rebuilt from the portal's merged `deploy/` with the admin's `jobs.conf` and `staging.env.example`; `provision.sh` with `WSGI_MODULE=mytp_admin.wsgi`: 6 changes, the `kmscheck` timer installed; `job kmscheck: ok`; `/healthz` answers `ok / site: admindev`. Its Sentry monitor stays disabled (basic plan, epic-platform Notes), like the others.
- Step 11.7 (user, after entry 4's 2FA was live): `create_sysadmin` over SSH; signed in (by SMS code); the dashboard is headed "Sysadmin"; Log out returns to the login page.
