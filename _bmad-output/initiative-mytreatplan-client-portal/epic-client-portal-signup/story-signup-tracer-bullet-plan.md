---
title: 'Signup tracer bullet'
type: 'feature'
ticket: '1'
created: '2026-10-12'
status: 'built'
baseline_revision: 'd291eea2488c353efb81f35736cfc670b003bc36'
route: 'full'
route_source: 'auto'
risk: 'high'
review: 'quick'
review_source: 'pinned'
lenses_ran: ['quick']
review_loop_iteration: 0
context: []
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** No Doctor can create an account. The URL names `signup` and `login` exist only as placeholders, and `accounts.User` still logs in with `username`. Every later signup entry needs a working wizard skeleton first.

**Approach:**
- Make email the username.
- Add a server-side pending registration and a small wizard with a step registry, at URL name `signup` behind `PORTAL_ENABLED`.
- Step 1 collects email and password.
- Step 2 checks an email code sent through a verification-provider interface. A console fake provider implements it.
- A correct code creates the user, deletes the pending registration and logs the user in on a welcome page.

## Boundaries & Constraints

**Always:**
- **User:**
  - `email` unique and `USERNAME_FIELD`; `username` removed.
  - A custom manager with `create_user`/`create_superuser`, so `createsuperuser` asks for an email.
  - Email normalized: domain lowercased, surrounding spaces stripped.
  - Admin fieldsets updated.
  - One new migration on top of `0001`.
- **Pending registration:**
  - Holds email, the **hashed** password (`make_password`, never plaintext), the current step and `created_at`/`updated_at` (entry 10 purges by them).
  - Its id lives in the session. No user exists before the email code is correct.
- **Step registry:**
  - An ordered list of steps (slug, form or handler, template). Entries 4–7 add or extend steps by changing the registry, not the views.
  - A step cannot be reached before the earlier steps are saved: going there redirects to the current step.
- **Step 1:**
  - Email and password twice.
  - Django's password validators, with the minimum length raised to 12 (`signup-fields.md`).
  - An already registered email gets a plain form error. Entry 5 adds the recovery link.
- **Verification provider:**
  - An interface `send(channel, contact, purpose, language)` / `check(channel, contact, purpose, code) -> bool`, picked by the setting `VERIFICATION_PROVIDER` (dotted path, default the fake).
  - Channels `email` and `mobile` exist from the start.
  - The fake writes the 6-digit code to the log (console). It stores no code: see Design Notes.
- **Account creation:**
  - In one transaction: create the user with the stored hash, delete the pending registration, `login()`, and redirect to the `welcome` route.
  - A wrong code shows a form error and keeps the step.
  - Resend is allowed (no limits until entry 7).
- **Routes:**
  - Inside `i18n_patterns` via `portal_patterns`, so they exist only when `PORTAL_ENABLED`.
  - The URL name `signup` is not namespaced.
  - `welcome` requires login; its placeholder text is left for the onboarding spec.
- **Templates and strings:**
  - Templates extend `base.html` and use the Tailwind design tokens.
  - All strings are in English and Spanish. Claude drafts the Spanish, and it goes to the team review.

- Decision (user, 2026-10-12): signup goes live on staging with the merge (`PORTAL_ENABLED` stays on there, behind basic auth); fake-provider codes appear in the server journal.
- Decision (user, 2026-10-12): the user migration runs in place (staging has 0 users). The local dev user then gets an email, or `createsuperuser` is run again, to log in to admin by email.
- Decision (user, 2026-10-12): the plan is kept whole although above 1600 tokens (first end-to-end slice).

**Never:**
- Encryption, blind index or Argon2 (entry 2).
- The event log (3).
- The other step-1 fields (4).
- Duplicate or resume flows (5).
- Legal step (6).
- Mobile code or limits (7).
- Purge (10).
- Twilio (11).
- A login page (epic-login).
- Plaintext passwords or codes in the DB, logs other than the fake's line, or Sentry.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| Happy path | Valid email and password, then the console code | User exists, pending registration gone, logged in on `welcome` | — |
| Invalid step 1 | Bad email, mismatched or weak password | Stays on step 1, no pending registration | Field errors |
| Registered email | Email of an existing user | Stays on step 1 | "already registered" error |
| Wrong code | Code not matching | Stays on the code step, no user | Code error |
| Skipping ahead | GET the code step with no pending registration | Redirect to step 1 | — |
| Portal off | `PORTAL_ENABLED=False` | `/en/signup/` 404; nav links hidden | — |
| Spanish | `/es/signup/` | Wizard in Spanish | — |

</frozen-after-approval>

## Code Map

- `accounts/models.py:4` -- `User(AbstractUser)` with no fields; `accounts/migrations/0001_initial.py` is the only migration (`email` not unique). `accounts/admin.py:6` -- the stock `UserAdmin`; it needs fieldsets without `username`.
- `mytreatplan/settings.py` -- `AUTH_USER_MODEL` (L175), `PORTAL_ENABLED` (L58), `INSTALLED_APPS` (L108), validators (L177), `MAILERS` console backend (L238). `CACHES`, `LOGIN_URL` and `PASSWORD_HASHERS` are not set.
- `mytreatplan/urls.py:36-52` -- `portal_patterns = []` and `portal_urlpatterns()` inside `i18n_patterns`. Signup goes here.
- `templates/includes/_portal_links.html:4` -- the nav links appear when `signup`/`login` resolve. `templates/base.html` -- blocks `title`, `content`. HTMX and Tailwind are already loaded.
- `mytp_publicsite/tests/test_settings_staging.py:193,223,257-263` -- these assert `portal_patterns == []`, string-replace that exact line, and expect no `/en/signup/` link with the switch on. All three must be rewritten for real routes. The `reload_urlconf` teardown order (1.13 review #1) must not leak the routes into other tests.
- `mytp_publicsite/tests/urls_with_portal.py`, `test_legal_seo.py:222` -- stub `signup`/`login` views; keep them working.
- `mytp_publicsite/tests/test_platform.py:17-42` -- user-model label and migration order checks.
- `mytp_publicsite/tests/test_i18n.py:20` -- `TRANSLATABLE_FOLDERS`; add any new app. Every new string needs a Spanish entry in `locale/es/LC_MESSAGES/django.po`.
- `pytest.ini` -- `testpaths`; add any new app. No test uses the DB yet. CI has a `postgres:16` service and runs `makemigrations --check`.

## Tasks & Acceptance

**Execution:**
- [x] `accounts/models.py`, `accounts/managers.py`, `accounts/admin.py`, `accounts/migrations/0002_*.py` -- email as username.
- [x] `signup/` (new app: `models.py`, `steps.py`, `forms.py`, `views.py`, `urls.py`, `verification.py`, templates) -- pending registration, registry, steps, provider interface and fake.
- [x] `accounts/views.py`, `accounts/urls.py`, template -- the `welcome` route.
- [x] `mytreatplan/settings.py`, `mytreatplan/urls.py`, `settings.env.example`, `deploy/staging.env.example` -- the app, `VERIFICATION_PROVIDER`, `LOGIN_URL`, minimum length 12, portal routes.
- [x] `locale/es/LC_MESSAGES/django.po` -- Spanish strings.
- [x] `signup/tests/`, `accounts/tests/`, `mytp_publicsite/tests/test_settings_staging.py`, `pytest.ini`, `test_i18n.py` -- every matrix row, the manager, the fake provider, and the updated portal tests.

**Acceptance Criteria:**
- Given a local run with `PORTAL_ENABLED=True`, when a person registers with email and password and enters the code from the console, then they land logged in on the welcome page.
- Given the new user, when they log in to `/admin/` with email and password (as a superuser made by `createsuperuser`), then email login works.
- Given the DB after signup, then no table holds the plaintext password or the code.

## Implementation Notes

- Registry (`signup/steps.py`): each `Step` has `slug`, `form_class`, `template_name`, `title`, `actions` (extra buttons, e.g. `resend`) and the hooks `get_form_kwargs`, `get_initial`, `save` (stores data in the pending registration) and `enter` (runs when the wizard moves into the step; the email step sends its code there). The views (`signup/views.py`) never name a step; after the last step is saved they create the account. Step 1 is at `signup` (`/<lang>/signup/`), later steps at `signup_step` (`/<lang>/signup/<slug>/`).
- `PendingRegistration.step` is the slug of the first unsaved step. Going back to step 1 is allowed; saving it again updates the row and sends a fresh code.
- "Already registered" compares case-insensitively (`email__iexact`); the stored email keeps the local part's case (domain lowercased, spaces stripped). A race with another signup for the same email is caught at account creation (form error, no user, no login).
- `LOGIN_URL = 'signup'` until epic-login adds `login`: anonymous visitors to `welcome` go to signup.
- Logging: `LOGGING` sends the `signup.verification` logger at INFO to the console (stderr, the journal on staging). That logger is ignored by Sentry (`mytreatplan/sentry.py`, `CODE_LOGGERS`).
- Layout: `templates/portal_base.html` (light header, noindex) and `templates/includes/_form_field.html`; field classes are set in `signup/forms.py` (scanned by Tailwind via `@source`).
- Tests: `portal_urls` fixture (root `conftest.py`) serves the real `portal_patterns` through `signup/tests/urls_portal.py`. pytest-django now runs DB tests first, so `test_request_error_reaches_sentry_scrubbed` disconnects `close_old_connections` around its raw WSGI call, as Django's test client does.
- Spanish strings use "usted", as the existing catalogue does (e.g. "Contáctenos"); for team review.

## Plan Change Log

## Review Triage Log

Pass 1 (quick lens, 2026-10-13): high 0, medium 2, low 2, false 0, maybe-false 0. The reviewer's sandbox couldn't reach a database; on my side the full suite passed (344, 6 skipped) before the review.

| # | Finding | Verdict | Route | Evidence / action |
|---|---------|---------|-------|-------------------|
| 1 | `_create_account` uses step 1's `AccountForm.error_messages` for the race error, so the generic views name a step | low | patch | Confirmed (`signup/views.py` imports `AccountForm`). The "account already exists" check belongs to account creation, not step 1. Patch: a module-level message shared by the form and the view. |
| 2 | Emails that differ only in case can both be registered: `unique=True` is case-sensitive; the `iexact` check races; the admin change form and `createsuperuser` check exact matches | medium | patch | Confirmed: migration 0002 adds a plain unique index, and the check in `_create_account` locks only the pending row. Same root cause as #3 (email case not handled the same everywhere). Patch: a case-insensitive unique constraint (`Lower('email')`) in 0002 (not yet merged). |
| 3 | Login is case-sensitive: the default `get_by_natural_key` is an exact match, so `Ana@x.com` can't log in as `ana@x.com`, though signup says it's "already registered" | medium | patch | Confirmed: `UserManager` has no override; the admin login test uses only lowercase. Grouped with #2. Patch: `get_by_natural_key` with `email__iexact`, plus a mixed-case login test. |
| 4 | `portal_base.html` comment mentions portal links the header doesn't render | low | patch | Confirmed; the links aren't needed on portal pages. Patch: correct the comment. |

## Design Notes

- **Stateless fake code:**
  - The code is `HMAC(SECRET_KEY, channel|contact|purpose|10-minute window)` reduced to 6 digits.
  - `check` accepts the current and previous window.
  - Nothing is stored, it works across Gunicorn workers, and it mirrors Twilio's "codes never in our DB".
  - Expiry and attempt counting come in entry 7.
- **Hash in the pending registration:** the user later gets the same hash, so the password is never held in plaintext between steps.

## Verification

Done locally (2026-10-13) after the review patches: `pytest` 348 passed, 6 skipped (PyYAML workflow tests, run in CI), on SQLite because the local Postgres role cannot create a test database (CI runs them on Postgres 16); `makemigrations --check` no changes; `manage.py check` no issues. Implementer's run of the full flow on the local Postgres DB in `/en/` and `/es/`: code in the console, wrong code kept the step, right code landed logged in on welcome; no table held the password or the code; admin login by email works. Staging checks pending (the merge deploys signup there).

**Commands:**
- `pytest` (local DB user needs CREATEDB) -- expected: all pass
- `python manage.py makemigrations --check --dry-run` -- expected: no changes
- `python manage.py check` -- expected: no issues

**Manual checks (if no CLI):**
- `runserver` with `PORTAL_ENABLED=True`: the full flow in `/en/` and `/es/`.
