---
title: 'Cancel, idle timeout and connection loss'
type: 'feature'
ticket: '8'
created: '2026-10-14'
status: 'built'
baseline_revision: '8c3861cfa5cf764ac18d921a1eb003372654e251'
route: 'full'
route_source: 'auto'
risk: 'low'
review: 'quick'
review_source: 'pinned'
lenses_ran: ['quick']
review_loop_iteration: 0
context: []
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** The wizard is missing three of CAP-11's failure policies:
- a Doctor can't cancel a registration (CAP-8);
- a session left idle never ends;
- a lost connection on submit shows the browser's error page, and what was typed is lost.

**Approach:**
- **Cancel:** "Cancel registration" asks for confirmation, then clears the registration through entry 10's `remove_pending(row, 'cancelled')`.
- **Idle timeout:** after 30 idle minutes the signup session ends. The data stays saved, and typing the email again resumes through entry 5's code flow.
- **Connection loss:** the wizard's forms submit through htmx. A failed send shows a "connection lost" banner, keeps the input, and retries when the connection is back.

## Boundaries & Constraints

**Always:**
- **Cancel:**
  - "Cancel registration" shows on every wizard step and on the resume page. It leads to a confirmation page that works without JavaScript (GET shows it, POST confirms).
  - On confirm: the session's pending registration, if any, goes through `remove_pending(row, 'cancelled')`. Its attempt gets `purged` with reason `cancelled` and ends `cancelled`. The signup session keys are cleared, and the person lands on an empty step 1 with "Your registration was cancelled."
  - With no pending registration (step 1 never saved), confirming only clears the session keys. An open attempt still ends `cancelled`.
  - The log keeps its personal values for 30 days, as decided in entry 10. The daily job anonymizes it.
- **Idle timeout:**
  - The session stores the time of the last signup request. On a signup request more than 30 minutes after it, the signup session keys (pending id, attempt, resume state, code-page state) are cleared. The rest of the Django session (language, CSRF) stays.
  - The attempt gets an `idle_timeout` event with reason `idle timeout`, and stays open. A resume later ends it `abandoned`, and otherwise the hourly purge ends it `timeout`.
  - The person lands on step 1 with "Your session ended after 30 minutes without activity. Enter your email to continue where you left off." A POST that arrives after the timeout is not processed.
  - The pending registration is untouched. The email check offers the resume.
- **Connection loss:**
  - The wizard's step forms, the code-help forms and the resume forms are htmx-boosted.
  - On an htmx send error or timeout:
    - nothing on the page changes and the typed input stays;
    - a banner (`role="alert"`) says "Connection lost. We'll send your data again when you're back online.", with a "Try again" button;
    - the browser's `online` event, or the button, resubmits the same form once.
  - Every page keeps working without JavaScript as plain POSTs.
  - Swapped pages must work as after a full load. The step 1 password-strength script (`signup-account.js`) has to re-initialize after a swap.
- English and Spanish for every new text.
- Decision (user, 2026-10-14): browser tests use Playwright (`pytest-playwright`, Chromium), in the dev requirements and in CI. One browser test covers the Connection lost and Retry rows. 2.14's end-to-end suite will reuse the setup.
- Decision (user, 2026-10-14): the plan is kept whole, above the size guideline.

**Never:**
- Changing the 24 h purge or the resume rules. Rate limits and the CAPTCHA (entry 9).
- Ending the whole Django session, or logging out a user.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| Cancel confirm | At the legal step, Cancel, then confirm | Row and legal choices gone; `purged` reason `cancelled`; attempt ended `cancelled`; empty step 1 with message | No error expected |
| Cancel back out | Cancel, then "Keep my registration" | Back on the same step; nothing changed | No error expected |
| Cancel before save | Step 1 never saved, Cancel, confirm | Session keys cleared; empty step 1 | No error expected |
| Cancel GET only | GET on the cancel URL | Confirmation page; nothing removed | No error expected |
| Log kept | After cancel | Attempt events keep `data` until the 30-day job | No error expected |
| Idle 31 min | Last signup request 31 min ago, any signup request | Signup keys cleared; `idle_timeout`; step 1 with the idle message; row kept | No error expected |
| Idle 29 min | Last request 29 min ago | Normal page | No error expected |
| Idle POST | A step POST after 31 min | Not saved; step 1 with the idle message | No error expected |
| Idle then resume | After idle, the same email at step 1 | Resume offer; the code restores the data | No error expected |
| Connection lost | Boosted submit, network failure | Banner shown; input kept; no navigation | No error expected |
| Retry | Banner shown, connection back (`online` or "Try again") | Same form sent once; next page shown | No error expected |
| Spanish | `/es/` | Cancel page, messages and banner in Spanish | No error expected |

</frozen-after-approval>

## Code Map

- `signup/retention.py` `remove_pending(row, outcome)` (entry 10): use it with outcome `cancelled`. It records `purged` and ends the attempt. For a session attempt with no row, call `eventlog.end_attempt(attempt, 'cancelled')` directly.
- `signup/views.py`:
  - **Session keys** to clear on cancel and idle: `SESSION_KEY`, `ATTEMPT_SESSION_KEY`, `RESUME_SESSION_KEY`, and `steps.CODE_STATE_SESSION_KEY`. One helper, `clear_signup_session(request)`.
  - **Idle check:** a decorator or helper applied at the top of `signup`, `resume`, `check_email` and the new `cancel`. It compares the stored time (`signup_seen_at`, epoch seconds) with `IDLE_TIMEOUT = 30 min`, then updates the time.
  - On timeout, record `idle_timeout` on the session attempt. If the request was `check_email`, return the empty partial. Otherwise redirect to `signup` with the message.
- `signup/urls.py`: `signup/cancel/` (name `signup_cancel`), placed before `signup/<slug>/`.
- Templates:
  - `wizard.html` and `resume.html` get the "Cancel registration" link and the boosted forms (`hx-boost="true"` on the form, target `body`).
  - New `cancel.html` (extends `portal_base.html`) with Confirm and "Keep my registration" (back to the current step URL).
  - `_code_help.html` forms are boosted too. Keep the 2.5b `form="resume-form"` trick on step 1 working.
- New `static/js/signup-connection.js` (deferred, loaded once from `portal_base.html` or the wizard):
  - listens to `htmx:sendError` and `htmx:timeout` and shows the banner;
  - remembers the failed form;
  - resubmits it once on `online` or the banner button, through `htmx.trigger(form, 'submit')`;
  - sets `htmx.config.timeout` to 20 s.

  The banner markup sits in the template, hidden (`hidden` attribute), with its translated strings.
- `static/js/signup-account.js`: wrap its setup in a function called on `DOMContentLoaded` and on `htmx:load` for swapped content, without double-binding.
- `templates/base.html` already loads `htmx.min.js` (2.0.11, `static/js/`).
- `locale/es/LC_MESSAGES/django.po`.
- Tests:
  - `signup/tests/test_cancel_idle.py` (new) covers cancel and idle with the Django client. For time, patch the clock or set `signup_seen_at` in the session.
  - New `signup/tests/browser/test_connection.py`, marked `browser`, with `pytest-playwright` and Django's `live_server`:
    - start step 1 or the legal step;
    - `context.set_offline(True)`, press Continue, check the banner and that the input stays;
    - `set_offline(False)` (which fires `online`), then land on the next step with exactly one `step_saved`.
  - `pytest.ini`: register the `browser` marker. The browser tests need `DJANGO_ALLOW_ASYNC_UNSAFE=true` (Playwright's sync API runs an event loop).
  - `requirements-dev.txt`: pin `pytest-playwright`, and with it `playwright`.
  - `.github/workflows/ci.yml`: add `python -m playwright install --with-deps chromium` before the tests. The existing pytest run then includes the browser tests.
  - Locally, the browser tests skip themselves when Chromium isn't installed. They must not fail the plain SQLite run.
  - Don't download Chromium, or install anything else outside the sandbox. The browser test is verified in CI. A local PostgreSQL run is also not needed.

## Tasks & Acceptance

**Execution:**
- [x] `signup/views.py`, `signup/urls.py` -- cancel view, idle check, session clean-up.
- [x] Templates and `static/js/signup-connection.js`, `signup-account.js` -- cancel link and page, boosted forms, banner, re-init.
- [x] `locale/es/LC_MESSAGES/django.po`.
- [x] `requirements-dev.txt`, `pytest.ini`, `.github/workflows/ci.yml` -- Playwright and the `browser` marker.
- [x] Tests -- every matrix row; the connection rows in the browser test.

**Acceptance Criteria:**
- Given staging, when a person cancels at the legal step, then the admin shows no pending registration for that email and the attempt ended `cancelled`.
- Given staging and a browser switched offline (DevTools) at a step, when Continue is pressed and the connection comes back, then the banner shows, the typed data stays, and the step is submitted once.
- Given CI on PostgreSQL, when the suite runs, then every test passes.

## Implementation Notes

- **Idle check:** the `idle_check` decorator wraps `signup`, `resume` and `cancel`. `check_email` calls the same helper (`_idle_timed_out`) itself and answers with the empty partial. Every signup request stores `signup_seen_at`; a request more than `IDLE_TIMEOUT` (30 min) after the last one counts as idle (exactly 30 min does not), but only while the session holds signup state (`SESSION_KEY`, `ATTEMPT_SESSION_KEY` or `RESUME_SESSION_KEY`): with none, nothing is lost and the request proceeds. The idle message is queued by the helper, so a timeout hit by the live email check shows it on the next page. `_now()` is patchable.
- **Cancel:** `signup/cancel/` (`signup_cancel`). The link carries `?from=<step slug>` or `?from=resume`, so "Keep my registration" returns to that page; an unknown slug, a step not reached yet, or `resume` without an offer falls back to the current step. After `remove_pending`, the session's attempt is ended `cancelled` if it is still open (no row, or a different attempt). The confirmation form is a plain POST (not boosted).
- **Scripts:** `signup/_scripts.html` loads `signup-connection.js` and `signup-account.js` in the head of `wizard.html` and `resume.html`, so a page reached by a swap has them. `test_strength_meter_is_loaded_on_step1_only` changed accordingly: zxcvbn-ts stays on step 1 only, `signup-account.js` is on every signup page. When step 1 arrives by a swap without zxcvbn-ts, `signup-account.js` loads it from `#password-strength[data-scripts]`. Each element is bound once (`data-signup-bound`).
- **Retry:** htmx 2.0 forgets the clicked button once the focus moves (to "Try again"), so `signup-connection.js` records the submitter and adds its name/value as a hidden input for the resend (the code-help `action=resend`/`sms` buttons, resume `action=send`). Only boosted forms trigger the banner; the live email check's failure is ignored. A new request from the failed form hides the banner.
- **Error responses:** `signup-connection.js` swaps a 4xx/5xx response to a boosted form like a full load (`htmx:beforeSwap`, `shouldSwap`), without the banner; the live email check keeps htmx's default.
- **History:** the banner carries `hx-history="false"`, so htmx never stores a signup page (personal values) in its history cache; Back refetches from the server.
- **Browser test:** `signup/tests/browser/test_connection.py` runs with `django_db(transaction=True, serialized_rollback=True)` (live_server flushes the tables; the serialized data restores the legal documents). It overrides the Secure-cookie settings for plain-HTTP `live_server`, sets `DJANGO_ALLOW_ASYNC_UNSAFE` at import, and skips when `pytest_playwright` is missing (module `importorskip`) or Chromium is not downloaded (`signup/tests/browser/conftest.py`, at collection, before pytest-playwright's session `browser` fixture). Covers Connection lost and Retry (the `online` event on step 1, "Try again" then `online` on the legal step).
- Verified on SQLite (only the 3 known PostgreSQL-only tests fail; the browser test skips), `makemigrations --check`: no changes, `compilemessages`: compiles. The browser test was not run locally (no Playwright, and the sandbox blocks launching Chrome); it is verified in CI.

## Plan Change Log

## Review Triage Log

**Pass 1 (quick lens, 2026-10-14):** high 0, medium 3, low 3, false 0, maybe-false 0. Four patches (one also raised by the orchestrator); two findings rejected.

| # | Finding | Verdict | Route | Evidence |
|---|---------|---------|-------|----------|
| 1 | The `chromium_installed` check is function-scoped, so the session `browser` fixture launches first. With pytest-playwright but no Chromium, the test ERRORs instead of skipping | medium | patch | Real: pytest sets up higher-scoped fixtures first. Fix: `signup/tests/browser/conftest.py` marks the `browser` tests skipped at collection when Chromium's executable is missing. |
| 2 | Boosted forms show nothing for a 4xx/5xx response (CSRF 403, 404, 500); before, a plain POST showed the error page | medium | patch | Real: htmx 2 doesn't swap error responses by default. Fix: `htmx:beforeSwap` swaps 4xx/5xx responses of boosted forms, as a full load would, with no connection banner. |
| 3 | The idle timeout fires on the timestamp alone: someone who only looked at step 1, cancelled or finished gets "Your session ended", and a first step 1 POST after 30 min is discarded (also raised by the orchestrator) | medium | patch | Real: `signup_seen_at` was checked with no signup state. Fix: the timeout applies only when the session holds a pending registration, an attempt or a resume offer. Tests added for both no-state cases. |
| 4 | When the live email check is the first request after the timeout, the keys are cleared but the idle message never shows | low | patch | Direct correction: `_idle_timed_out` queues the message, so it shows on the next page whichever request detected the timeout. Test added. |
| 5 | After an htmx timeout (20 s) the server may still have saved, so the retry submits the step again | low | rejected | Needs a response slower than 20 s. The repeated step save updates the same registration, and a repeated code send is held by the cooldown. A fix needs a token per submission. |
| 6 | The timeout path has no browser test | low | rejected | `htmx:timeout` goes through the same handler as `htmx:sendError`, which the browser test covers. A timeout test needs a slowed server in the live-server test. |

## Verification

**Commands:**
- `DATABASE_URL="sqlite:///$TMPDIR/x.sqlite3" ../.venv/bin/python -m pytest -q` (with test keys) -- expected: only the 3 known PostgreSQL-only tests fail.
- `manage.py makemigrations --check`, `manage.py compilemessages` -- expected: no changes; compiles.

Done locally (2026-10-14) after the review patches, on SQLite: `pytest` 696 passed, 7 skipped (PyYAML, and the browser test without Playwright), 3 failed only because those tests use PostgreSQL-only raw SQL. `makemigrations --check` reports no changes; `compilemessages` is up to date; `node --check` passes on both scripts. The browser test has not run yet. Pending: its first run in CI, CI on PostgreSQL, and the staging checks (cancel at the legal step; DevTools offline then back).
