---
title: 'Fix: no signup while logged in; cancel works after the idle timeout'
type: 'bugfix'
ticket: ''
created: '2026-10-23'
status: 'built'
baseline_revision: '820b631b5be1bedb52ca292b93d9d33da7d90bc0'
route: 'oneshot'
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

**Problem:** The signup retrospective found two defects.
- **R2 (action item 3):** the signup wizard (`signup/views.py`) serves logged-in users. A logged-in Doctor who completes a signup with new details gets a second account and is silently switched to it, because `login(request, user)` flushes their session.
- **R3 (action item 4):** "Cancel registration" after the 30-minute idle timeout doesn't cancel. `idle_check` handles the POST first, clears the session keys and redirects, so `remove_pending` never runs. The row stays until the 24 h purge, and the message invites the person to continue.

**Approach (the user chose to do both together, 2026-10-23):**
- **R2:** every signup page (the wizard steps, resume and cancel) sends a logged-in user to the welcome page, as the login page does. The live email check answers with nothing. Nothing is saved, and the session's signup keys are left alone.
- **R3:** the idle timeout no longer applies to a cancel POST. The cancel removes the session's pending registration, ends its attempt `cancelled` and shows "Your registration was cancelled.", as at any other time. A cancel GET after the timeout still goes to step 1 with the idle message.
- **Tests:** a logged-in user on each signup page (step 1 GET and POST, a later step, resume, cancel, check-email) is redirected or answered with nothing, and no pending registration or second user is created. A cancel POST after 31 idle minutes removes the row and ends the attempt `cancelled`.

</frozen-after-approval>

## Implementation Notes

- Oneshot route: a decorator, a branch in `cancel`, and tests.
- Multi-goal check: R2 and R3 are separate shippable fixes. The user asked for both in one change.

- Files: `signup/views.py` (`anonymous_only` on `signup`, `resume` and `cancel`; `check_email` answers nothing to a logged-in user; `cancel` applies the idle timeout to its GET only and stores the request time on its POST), `signup/tests/test_logged_in.py` (new), `signup/tests/test_cancel_idle.py` (two tests).
- Results (SQLite, test keys, browser outside the sandbox): the full suite gives 1,296 passed and 3 failed (the known PostgreSQL-only tests). `makemigrations --check`: no changes. The review patches touched only docstrings and tests; the two affected files were re-run after them (38 passed).

## Review Triage Log

**Pass 1 (quick lens, 2026-10-23):** high 0, medium 0, low 4, false 0, maybe-false 0. No behavioural bug; all four patched.

| # | Finding | Verdict | Route | Evidence |
|---|---------|---------|-------|----------|
| 1 | The module docstring says the idle timeout applies with no exception | low | patch | It now says a confirmed cancel is always done, and that a logged-in user goes to welcome. |
| 2 | The `idle_check` docstring names only the live email check as an exception | low | patch | It now names the cancel GET-only rule. |
| 3 | No test of a logged-in cancel POST with a signup in progress, or that the signup keys stay | low | patch | `test_logging_in_mid_signup_stops_the_wizard` now posts cancel: the row and the open attempt stay, as do the session keys. |
| 4 | The idle cancel GET test doesn't check the cleared keys or the `idle_timeout` event | low | patch | Both are now asserted. |

## Verification

**Commands:**
- The local suite (test keys, SQLite), browser tests outside the sandbox -- expected: only the 3 known PostgreSQL-only tests fail.
- `makemigrations --check` -- expected: no changes.
