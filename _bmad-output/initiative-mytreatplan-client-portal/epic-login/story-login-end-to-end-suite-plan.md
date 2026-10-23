---
title: 'Login end-to-end suite'
type: 'feature'
ticket: '12'
created: '2026-10-23'
status: 'done'
baseline_revision: '2cbfd48023578974145c27e4e5489a46f761f8e6'
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

**Problem:** Each login story has client tests, and a few have browser tests: login and logout, a wrong password, the remembered device, the password reset. Nothing yet runs the login spec's success signal end to end in a browser. The signal: a Doctor created by signup logs in with the password and a code to the contact of their choice, ticks "remember this device", logs in next time without a code, logs out, recovers a forgotten password through the emailed link from signup's recovery link, and logs in with the new one. Their logins and the pages they opened are on record. Nor does any browser test exercise the single session, the idle limit or the lockout.

**Approach:** A Playwright suite in `accounts/tests/browser/`, with the fake verification provider and the test mail backend. It covers:
- the success signal as one journey, in English and Spanish;
- one browser test per capability that a browser shows differently from the client tests: the second factor by mobile with the SMS fallback, the single session in two browser contexts, the idle end, the lockout, and the activity records seen after a journey.

It reuses signup's and login's browser setup and 2.14's helpers.

## Boundaries & Constraints

**Always:**
- Marker `browser`. The tests skip as now when Playwright or Chromium is missing, and CI runs them in its `pytest` step (PostgreSQL 17, DEBUG off).
- Codes come from the fake's `current_code`. Reset links come from the test mail backend's outbox (the fragment link from 3.10). Never parse codes or links from pages.
- No network: the Turnstile stub, `external_checks`, and no real Twilio or SMTP.
- Each test is independent and uses its own browser contexts, so they run in any order.
- Time moves in-process, as in 2.14: patch the `_now`/clock functions of `accounts.views`, `accounts.login_guard`, `accounts.login_failures`, `accounts.sessions` and the fake provider, or move stored times. No `time.sleep`.
- Assertions check what the person sees (texts, URLs, field values). Records are checked in the database for the activity rows.
- No product code changes.

**Never:**
- Duplicating client-test internals (counter values, cache keys, event payload details beyond what the row needs).
- Real Twilio, SMTP, KMS or Sentry.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| Success EN | Signup Doctor: login, email code, tick "remember"; log out; log in without a code; log out; signup's recovery link → reset via the mailed fragment link; log in with the new password and a code | Each step as the spec says; ends on welcome | — |
| Success ES | The same from `/es/` | Spanish pages and mail | — |
| Mobile code | Choose mobile; WhatsApp off | Code by SMS, the page says so; logs in | — |
| Wrong code | A wrong code, then the right one | "not correct", then welcome | — |
| Single session | Context A logged in; context B logs in | A's next page: login with "…logged in on another device" | — |
| Idle | Logged in; 15 min + 1 s (clock moved) | Next page: login with the idle message | — |
| Lockout | 5 wrong passwords | Block message; the right password refused; after 10 min (clock moved) it works | — |
| Records | After the EN journey | `login` records (with IP and User-Agent) and `page` records (welcome) for the account | — |
| Forgot link | Login page's "Forgot your password?" | Opens the request page | — |

</frozen-after-approval>

## Code Map

- `accounts/tests/browser/conftest.py` -- reuses signup's `legal_documents`, `plain_http`, `turnstile_script` and `clear_cache`; signup's browser conftest skips when Chromium is missing.
- `accounts/tests/browser/test_login.py:54-160`, `test_password_reset.py:35-` -- the existing login and reset browser tests. Their flows (signup through real pages, logout from the header, the fragment link from `mailoutbox`) are the patterns to reuse. Move shared steps into an `accounts/tests/browser/helpers.py`.
- `signup/tests/browser/helpers.py:41-145` -- `route_turnstile`, `url`, `wait_for_token`, `fill_step1`, `press`, `follow`, `enter_code`, `email_code`/`mobile_code` (use `purpose='login'` for login codes), `to_email_step`, `to_mobile_step`. Signup through the browser comes from `signup/tests/browser/test_success.py`.
- `accounts/tests/helpers.py`, `accounts/tests/conftest.py` (3.11) -- shared client helpers and constants; `lock_clock`/`idle_clock` show which `_now` functions to patch.
- `accounts/login_guard.py` (lockout, records), `accounts/sessions.py` (`IDLE_LIMIT`, the messages), `accounts/views.py` (login, code pages), `accounts/password_reset.py` (`reset_url`, the outbox mail, the `RESET_PAGES` exemption).
- `eventlog.models.Attempt` (`LOGIN`, `PAGE`, `account_id`), `Event.data` (decrypted IP and User-Agent) -- for the records row.
- `signup/tests/browser/test_failure_policies.py` -- 2.14's patterns for in-process clocks under `live_server` and for two contexts.

## Tasks & Acceptance

**Execution:**
- [x] `accounts/tests/browser/helpers.py` (new) -- login, code, logout, reset-link steps.
- [x] `accounts/tests/browser/test_success.py` (new) -- Success EN and ES.
- [x] `accounts/tests/browser/test_login_policies.py` (new) -- one test per remaining matrix row, each docstring naming its capability.
- [x] `accounts/tests/browser/__init__.py` -- a docstring mapping CAP-1–CAP-9 and the success signal to their tests.

**Acceptance Criteria:**
- Given CI, when `pytest` runs, then every browser test runs (none skipped) and passes.
- Given the suite, when run locally 3 times in a row with Chromium, then it passes each time.

## Implementation Notes

- `helpers.py` holds the steps in English and Spanish (`TEXTS`): `sign_up`, `log_out`, `submit_password`, `choose`, `log_in`, `request_reset`, `reset_link` (from `mailoutbox`), `set_new_password`, and `success_journey` (shared by the success test and the records test). `test_login.py` and `test_password_reset.py` now use `sign_up`, `submit_password`, `wait_for_mail` and `NEW_PASSWORD` from it.
- Login codes to one contact obey signup's one-minute resend cooldown (`signup.limits`): the journey moves the `clock` by `COOLDOWN_SECONDS` before the last login (a code again after the reset), and the single-session test's browser B takes its code by mobile (A's email code was just sent).
- The SMS fallback uses the fake provider's `FAKE_NO_WHATSAPP_NUMBER` (the fake ignores `TWILIO_WHATSAPP_ENABLED`, as signup's row 8).
- The idle test moves `idle_clock` (sessions, signup limits, fake provider); the lockout test `lock_clock` (views, failure counters, signup limits, fake provider).

## Plan Change Log

## Review Triage Log

**Pass 1 (quick lens, 2026-10-23):** high 0, medium 0, low 6, false 2, maybe-false 0. Six patches; two rejected. No bug in product code (none changed).

| # | Finding | Verdict | Route | Evidence |
|---|---------|---------|-------|----------|
| 1 | AC 1 (every browser test runs and passes in CI) not shown | false | rejected | Not a defect: CI runs after the push, and the PR records its count. |
| 2 | `test_password_reset.py` and `test_login.py::test_remembered_device…` still did steps by hand that `helpers.py` covers, contrary to the Implementation Notes | low | patch | They use `submit_password`, `choose` and `reset_link`. |
| 3 | `assert event.created_at is not None` can't fail | low | patch | Each login record's time is checked to lie within the journey. |
| 4 | `len(pages) >= 4` looser than the four landings named | low | patch | `== 4`. |
| 5 | The records test asserted login reasons in order and the page path, beyond the row and the plan's Never on payload details | low | patch | It checks only 3 login records with IP and User-Agent, and 4 `welcome` page records. |
| 6 | The plan's Always and the module docstring name `accounts.login_guard`'s `_now`, which doesn't exist | low | patch (docstring) | The docstring lists what `lock_clock`/`idle_clock` patch. The frozen Always line stays as approved; the lock lives in `login_failures`, whose clock is patched, so the test is right. |
| 7 | A second browser context could leak if `route_turnstile(other)` raised | low | patch | Moved inside the `try`. |
| 8 | (reviewer note) The plan's clock list doesn't match the code | false | rejected | Same root as #6, which is handled in the test docstring; no behaviour depends on it. |

After the patches (2026-10-23), on SQLite with Chromium (sandbox lifted): `pytest -m browser` gave 29 passed on 3 runs in a row; the full suite gives 1,280 passed, 3 failed (the known PostgreSQL-only tests).

## Verification

**Commands:**
- The local suite command (test keys, SQLite), outside the sandbox. Expected: only the 3 known PostgreSQL-only tests fail.
- `pytest -m browser` 3 times. Expected: passes each time.

**Results (2026-10-23):** `DATABASE_URL=sqlite:///$TMPDIR/full.sqlite3 pytest` outside the sandbox (Chromium cannot launch inside it): 1280 passed, only the 3 known PostgreSQL-only tests in `accounts/tests/test_crypto.py` failed. `pytest -m browser` 3 times in a row: 29 passed each time (20 earlier + 9 new). Not yet run in CI (PostgreSQL 17).

CI on PostgreSQL 17, DEBUG off (PR #39): 1283 passed, none skipped (all 29 browser tests ran). Merged as `12f51dc`; the user reported (2026-10-23) that the staging deploy completed ok. Nothing to set up on staging.
