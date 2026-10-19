---
title: 'Login tracer bullet'
type: 'feature'
ticket: '1'
created: '2026-10-19'
status: 'done'
baseline_revision: '2d9d4cef64585c121a8ff099036b2622a4da9e64'
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

**Problem:** Signup creates Doctors, but there's no way back in. There's no login page and no logout; `LOGIN_URL` still sends anonymous visitors to signup; the nav's "Log in" link stays hidden because no `login` route exists.

**Approach:** Add a login page (email, password, Turnstile CAPTCHA on every attempt) and logout, in EN/ES behind the portal switch. Login lands on 2.1's welcome route. Every way to fail (a wrong password, an unknown email, an inactive or erased account) gets one generic error. Point `LOGIN_URL` and the nav at login. Later stories (3.2–3.6) add the second factor, the remembered device, records, the lockout and the single session to this flow.

## Boundaries & Constraints

**Always:**
- Users are authenticated with `django.contrib.auth.authenticate` through `accounts.backends.EmailBackend`, which uses the blind-index lookup. The stock `ModelBackend` already refuses `is_active=False` and evens out the timing for unknown emails. Erased accounts are inactive, and their email index is replaced.
- One error text for every failed login, revealing nothing. A failed CAPTCHA uses signup's generic `BLOCKED_MESSAGE`.
- Login and logout change state only by POST, with CSRF. A GET of logout doesn't log out.
- A safe `next` (same host, checked as Django's `LoginView` does) is honoured; anything else goes to `welcome`.
- Routes are `login/` and `logout/` in `accounts/urls.py`, with the plain names `login` and `logout`. They're served only while `PORTAL_ENABLED` is on, so they 404 when it's off.
- Every new text is in English and Spanish.
- Login completes through one helper (authenticate → `login()` → redirect), the seam where 3.2 inserts the second factor.

**Never:**
- New dependencies, or changes to the signup flow's behaviour (moving its Turnstile markup and JS into shared files is allowed if signup behaves the same).
- The second factor, login records, the lockout, IP limits, a session policy or password reset. Those are stories 3.2–3.10.
- Personal values in logs or Sentry.

**Decisions (2026-10-19):**
1. When the CAPTCHA can't be checked (siteverify unreachable or erroring: `UNAVAILABLE`), the login goes through, and the event is logged as at signup (`check_unavailable`, no personal value). Story 3.5's lockout and IP limits are the guard during a Cloudflare outage.
2. When a Doctor is logged in, the public site's nav and the portal pages' header show "Client portal" (to `welcome`) and a "Log out" button (a POST form), and hide "Log in" and "Sign up". Anonymous visitors see "Log in" and "Sign up" as today.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| Success | Signup user, email in another case, right password, CAPTCHA passes | Logged in; redirected to `welcome` | — |
| Safe next | `?next=/en/welcome/` | Redirected there after login | — |
| Unsafe next | `?next=https://evil.example/` | Redirected to `welcome` | — |
| Wrong password / unknown email / inactive / erased | Each case | Same generic error; no session; the email field is kept, the password isn't | — |
| CAPTCHA failed | Turnstile FAIL secret | `BLOCKED_MESSAGE`; credentials not checked | — |
| CAPTCHA unavailable | siteverify timeout | Credentials checked; a right password logs in; the unavailability logged (decision 1) | — |
| Already logged in | GET `/en/login/` | Redirected to `welcome` | — |
| Logout | POST `/en/logout/` | Session ended; redirected to login with a "logged out" message; `/en/welcome/` then redirects to login | — |
| Logout by GET | GET `/en/logout/` | Not logged out (405) | — |
| Anonymous on welcome | GET `/en/welcome/` | Redirected to `/en/login/?next=/en/welcome/` | — |
| Portal off | `PORTAL_ENABLED` False | `/en/login/` and `/en/logout/` give 404; nav shows no Log in | — |
| Nav logged in | Logged-in Doctor on a public page and on welcome | "Client portal" and "Log out" shown; "Log in" and "Sign up" hidden (decision 2) | — |
| Spanish | `/es/login/` | Spanish page, error and messages | — |

</frozen-after-approval>

## Code Map

- `accounts/urls.py:6` -- add `login/` and `logout/` beside `welcome`, with plain names (see `signup/urls.py:5`).
- `accounts/views.py:5-8` -- `welcome` (`@login_required`). New login and logout views go here. A single `complete_login(request, user)` helper is the 3.2 seam.
- `accounts/backends.py`, `accounts/managers.py:16-28` -- email lookup through the blind index; reuse it, don't change it. `signup/views.py:411` is signup's own `login(request, user)`; it stays as it is.
- `mytreatplan/settings.py:340-342` -- `LOGIN_URL = 'signup'` and its comment, to become `'login'`.
- `signup/captcha.py:22-47` -- `verify_turnstile(token, ip)`, `TOKEN_FIELD`, `PASSED`/`FAILED`/`UNAVAILABLE`. Caller example: `signup/views.py:327-356` (`_refuse`, `_guard_step1`). `eventlog.client_ip` is at `eventlog/service.py:31`.
- `signup/forms.py:24-35,51` -- `StyledForm`/`FIELD_CLASSES` and `BLOCKED_MESSAGE`; import or move them to a shared module.
- `signup/steps.py:45-47,114-117`, `signup/templates/signup/step_account.html:43-52`, `static/js/signup-account.js:7-15,42-80`, `signup/templates/signup/_scripts.html` -- the Turnstile widget context, markup and JS that holds the submit until a token arrives. Extract the widget into a shared partial and a `static/js/turnstile.js`, used by signup and login.
- `templates/portal_base.html`, `templates/includes/_form_field.html`, `signup/templates/signup/wizard.html:26-30` -- the portal layout, the field partial, and the non-field error box to mirror.
- `templates/includes/_portal_links.html:4-8`, `templates/base.html:75` -- the nav links (`{% url 'login' as login_url %}`). The logged-in state follows decision 2. `portal_base.html:18-20` header gets the same links.
- `locale/es/LC_MESSAGES/django.po` -- Spanish strings ("Log in" = "Iniciar sesión" exists).
- Tests to update:
  - `mytp_publicsite/tests/test_settings_staging.py:291` asserts no `data-portal-link="login"`; it must now expect it.
  - `signup/tests/test_wizard.py:414-419` portal-off 404: add `/en/login/` and `/en/logout/`.
  - `mytp_publicsite/tests/test_legal_seo.py:217-231` nav tests.
- Test fixtures: root `conftest.py` `turnstile` (autouse; set `TURNSTILE_FAIL_SECRET` to fail, `turnstile.error = TimeoutError()` for unavailable), `portal_urls`. Constants are in `signup/tests/helpers.py:11-18`. The browser Turnstile stub is in `signup/tests/browser/helpers.py:15-44`.

## Tasks & Acceptance

**Execution:**
- [x] `accounts/views.py`, `accounts/forms.py` (new), `accounts/urls.py` -- the login form and view, with CAPTCHA first, then `authenticate`, then `complete_login` and a safe redirect; the logout view (POST only), redirecting to login with a message.
- [x] `accounts/templates/accounts/login.html` (new) -- on `portal_base.html`, with the email, password, Turnstile widget, the generic error box and a link to signup.
- [x] Shared Turnstile partial and `static/js/turnstile.js` -- extracted from signup; signup's templates and JS use them.
- [x] `templates/includes/_portal_links.html`, `templates/portal_base.html` -- the logged-in state ("Client portal", the Log out POST button) in both, per decision 2.
- [x] `mytreatplan/settings.py` -- `LOGIN_URL = 'login'`, comment updated.
- [x] `locale/es/LC_MESSAGES/django.po` -- Spanish for every new text; compile.
- [x] `accounts/tests/test_login.py` (new) and the tests listed in the Code Map -- every matrix row, plus signup's own tests still passing.

**Acceptance Criteria:**
- Given a user created by signup, when they log in through the page in a browser (the existing Playwright setup), then the Turnstile widget holds the submit until a token arrives, and they land on the welcome screen.
- Given the full suite, when it runs, then signup's tests (including the browser tests) still pass unchanged.

## Implementation Notes

- The Turnstile site key and script URL moved to `signup.captcha` (`TURNSTILE_SCRIPT_URL`, `widget_context()`); the widget is `templates/includes/_turnstile.html` and its JS `static/js/turnstile.js` (extracted unchanged from `signup-account.js`, its bound marker now `data-turnstile-bound`). Signup's pages load it through `signup/_scripts.html`.
- `BLOCKED_MESSAGE` and `StyledForm` are imported from `signup.forms`, not moved. The login email is a `CharField` with an email input, so a malformed address gets the generic error too.
- Decision 1's log is a `accounts.views` warning (`check_unavailable: step=login field=captcha reason=turnstile`), not an event-log event: login records (and their attempts) are entry 4.
- The portal pages' header shows only the logged-in links (`authenticated_only`); anonymous visitors there see no Log in / Sign up, so signup's pages are unchanged.
- `accounts/tests/test_login.py` and `accounts/tests/browser/conftest.py` import signup's autouse `clear_cache` (so it applies to the login tests only: run for every accounts test, it queried the database inside the broken atomic block of the IntegrityError tests); the browser conftest also reuses signup's browser fixtures.

## Plan Change Log

## Review Triage Log

**Pass 1 (quick lens, 2026-10-19):** high 0, medium 1, low 4, false 0, maybe-false 0. Two patches; three rejected.

Before the review, the full suite showed 3 errors in `accounts/tests/test_user.py` and `test_crypto.py`. They came from a new `accounts/tests/conftest.py` that applied signup's autouse `clear_cache` to every accounts test. The implementer had first reported them as pre-existing; its stash check had kept the untracked conftest. Fixed before the review: the fixture now applies only to the login tests.

| # | Finding | Verdict | Route | Evidence |
|---|---------|---------|-------|----------|
| 1 | A safe `next` without a slash (`?next=foo`) gives a 500 after `auth_login`; `?next=logout` is reversed to the logout URL | medium | patch | `redirect()` → `resolve_url` tries `reverse()` and re-raises NoReverseMatch for a value with no `/` or `.`. Now `redirect_after_login` returns `HttpResponseRedirect` with the checked URL, as `LoginView` does, and `resolve_url` is used only for the welcome fallback. Test `test_relative_next_is_a_literal_url` covers `foo` and `logout`, both after a login and as a logged-in GET. |
| 2 | Decision 1 says "logged as at signup (`check_unavailable`)", but login writes a `logger.warning`, not an event-log entry | low | rejected | The event is logged with no personal value, the decision's purpose. Signup's event-log entry needs an attempt, and login attempts arrive with entry 4 (login records); building that lifecycle here would take part of entry 4 (a cue for entry 4 to move it into the event log). The warning stays visible in the server journal. |
| 3 | `LoginForm.refuse` duplicates `signup.views._refuse` (3 lines) | low | rejected | Small, with no behaviour difference. Sharing it needs a new cross-app helper. A candidate for epic-login's refactor sweep (entry 11). |
| 4 | A plan task left unticked although done | low | rejected | The fix is an edit to this plan; ticked as bookkeeping. |
| 5 | The browser test's URL check after the early submit proves nothing | low | patch | Replaced by an assertion that `BLOCKED_MESSAGE` isn't shown; the pending-note check and the arrival on welcome stay. |

After the patches (2026-10-19), on SQLite with Chromium (sandbox lifted): the full suite gives 1,009 passed, 3 failed (the known PostgreSQL-only tests in `test_crypto.py`), no errors. `makemigrations --check`: no changes. `compilemessages`: OK.

## Verification

Done locally (2026-10-19) on SQLite: `pytest -m "not browser"` 989 passed, 3 failed: only the 3 known PostgreSQL-only tests in `accounts/tests/test_crypto.py`, no errors. `pytest -m browser` outside the sandbox: 18 passed (2 new login ones). `makemigrations --check`: no changes; `compilemessages` OK; `check` clean.

**Commands:**
- The local suite command with the test keys, on SQLite. Expected: only the 3 known PostgreSQL-only tests fail.
- `pytest -m browser`, run outside the sandbox. Expected: all pass.
- `manage.py makemigrations --check`, `manage.py compilemessages`. Expected: no changes, OK.

CI on PostgreSQL 17 (PR #28): 1012 passed. Merged 2026-10-19 as `7a94df3`. On staging the user reported these checks passed: login lands on welcome with "Client portal" and "Log out" in the header; logout shows its message, and `/en/welcome/` then sends to login; a wrong password shows the generic error; `/es/login/` is in Spanish.
