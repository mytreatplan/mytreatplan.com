---
title: 'One session per Doctor with an idle limit'
type: 'feature'
ticket: '6'
created: '2026-10-20'
status: 'done'
baseline_revision: '3835770d0e657c76328b2d3ef603b5e933d4f11b'
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

**Problem:** A Doctor can be logged in on several devices at once, and a logged-in session lasts Django's default two weeks. The spec (CAP-9) asks for:
- one active session per Doctor: a new login ends the older session;
- an end to a session after 15 minutes without a request, with expired sessions cleaned up.

Entry 10 (password reset) needs a way to end every session of an account.

**Approach:**
- Track each account's current session key, written on every login: portal, signup's auto-login and the admin. A new login deletes the previous session.
- A middleware ends a request whose session isn't the account's current one.
- A logged-in session's expiry is set to 15 minutes on each request, so the session row expires in the database and the existing `clearsessions` job removes it.
- One helper, `end_all_sessions(user)`, for entry 10.

## Boundaries & Constraints

**Always:**
- The single session and the idle limit apply to authenticated sessions only. Anonymous sessions (signup's wizard with its own 30-minute idle, 2.8, and the pending login) keep their behaviour.
- A new login deletes the older session's database row and records the new key, at the point Django logs a user in (the `user_logged_in` signal), so every way of logging in is covered.
- The idle limit is enforced by the middleware from the session's last-activity time (15 minutes), updated on each authenticated request, htmx requests included. The session's expiry (`set_expiry`) is renewed to 1 hour on each such request, so abandoned sessions expire and `clearsessions` (`deploy/jobs.conf`) cleans them (decision 2).
- Logout, the older session's end and the idle end leave 3.3's remembered-device cookie in place.
- Every new text is in EN/ES.

**Never:**
- A list of active sessions or a "log out everywhere" button. These aren't in the spec.
- Changing signup's 30-minute wizard idle, or the pending login's 10-minute life.
- A new session engine.

**Decisions (2026-10-20):**
1. The older browser, on its next request, is logged out and sent to the login page with the message "You were logged out because your account was logged in on another device."
2. A session ended by 15 minutes of inactivity shows on the login page: "Your session ended after 15 minutes without activity. Please log in again." For that message to be possible, the session must still exist after the 15 minutes:
- the middleware keeps the last-activity time in the session and enforces the 15 minutes itself, ending the session (`flush`) and queuing the message;
- the session's own expiry (cookie and row) is set to a longer grace, 1 hour, renewed on each authenticated request, so an idle session is still readable when the Doctor comes back within the hour;
- after that hour the row is expired, `clearsessions` removes it, and the plain login page shows.
3. The same rules apply to every authenticated session, admin and staff included: one session per account, and 15 minutes idle.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| Second login | Browser A logged in, the same account logs in on browser B | B logged in; A's session row deleted | — |
| Older browser | A's next request | Logged out; sent to login with "You were logged out because your account was logged in on another device." | — |
| Idle end | Logged in, no request for 15 min + 1 s | Next request goes to login with "Your session ended after 15 minutes without activity. Please log in again." | — |
| Activity | A request every 14 min | Stays logged in (the last-activity time slides) | — |
| Idle, back after 1 h | No request for over 1 h | Session expired; plain login page | — |
| Cleanup | Session idle over 1 h | Row expired; `clearsessions` deletes it | — |
| Signup auto-login | Signup completes on B while A is logged in | A is ended the same way | — |
| Remembered device | After either end | The mark is still there; the next login skips the code | — |
| Anonymous | Signup wizard, pending login | Unaffected | — |
| End all | `end_all_sessions(user)` | The account's sessions are gone; its next request goes to login | — |
| Admin | A staff session in `/admin/` | Same single-session and idle rules (decision 3) | — |
| Spanish | `/es/` | Spanish messages | — |

</frozen-after-approval>

## Code Map

- `mytreatplan/settings.py:229-240` -- `MIDDLEWARE`. The new middleware goes after `AuthenticationMiddleware` and before `MessageMiddleware`, or after it if it queues a message. No `SESSION_COOKIE_AGE`, `SESSION_SAVE_EVERY_REQUEST` or `SESSION_ENGINE` is set: database sessions, two weeks by default.
- `accounts/models.py` -- `User`. Add the current session key, a nullable `CharField(max_length=40)` that is never shown, with a migration. Or a one-to-one table; the plan prefers the field.
- `accounts/apps.py` -- connect the `user_logged_in` receiver (an `accounts/sessions.py` module holds the receiver, the middleware and `end_all_sessions`).
- `django.contrib.sessions.models.Session` -- delete by `session_key`.
- `signup/views.py:59-117` -- signup's own idle (`SEEN_SESSION_KEY`, `IDLE_TIMEOUT`, `idle_check`). Leave it unchanged. Its keys must survive the authenticated idle handling.
- `signup/views.py:~411` -- signup's `login(request, user)` auto-login, which fires `user_logged_in`.
- `accounts/views.py` -- `complete_login`, `logout`, the messages pattern (`messages.info(request, LOGIN_EXPIRED_MESSAGE)`). `accounts/devices.py` is the remembered cookie, which must survive.
- `deploy/jobs.conf:15` -- `clearsessions` at 03:30 UTC daily.
- Tests:
  - `accounts/tests/test_login*.py` helpers (`_login_with_code`, `doctor`);
  - two `Client()` instances act as two browsers;
  - for time, freeze it, or move the session's expiry in the database;
  - the existing browser login test.

## Tasks & Acceptance

**Execution:**
- [x] `accounts/models.py` + migration, `accounts/sessions.py` (new), `accounts/apps.py` -- the current session key, the `user_logged_in` receiver, the middleware (the older session and the idle expiry), and `end_all_sessions`.
- [x] `mytreatplan/settings.py` -- add the middleware.
- [x] `accounts/views.py` / templates -- the two messages (decisions 1 and 2), shown on the login page.
- [x] `locale/es/LC_MESSAGES/django.po` -- Spanish.
- [x] `accounts/tests/test_sessions.py` (new) -- every matrix row.

**Acceptance Criteria:**
- Given two browsers, when the Doctor logs in on the second, then the first browser's next page sends it to login.
- Given the full suite, when it runs, then signup's idle tests (2.8) and the 3.1–3.5 login tests pass.

## Implementation Notes

- `accounts/sessions.py` holds the `user_logged_in` receiver, `SessionLimitMiddleware` (after `MessageMiddleware`) and `end_all_sessions`. `User.current_session_key` (migration 0008) is written with a queryset `update`, not `save`.
- Decision 1 with the row deleted: the older browser's next request is anonymous (its row is gone), so the receiver also remembers the deleted key, hashed (SHA-256), in the database cache for SESSION_GRACE. An anonymous request whose session cookie names a missing session and matches that mark is sent to login with the message, once. An authenticated session that is not current (from before this rule, or after `end_all_sessions`) is ended without a message.
- Login page: the admin's for `/admin/` requests, else `LOGIN_URL` with `next` on a GET. An htmx request gets `204` with `HX-Redirect`. Already on the login page, or no portal routes: the request goes on, logged out, with the message queued.
- Django's own password change (`update_session_auth_hash`) cycles the key without a login: the middleware makes the new key current after the response, if the old one still was.
- Simultaneous logins: the new key is written only over the key read (`_set_current_key(..., check_expected=True)`); a login that loses re-reads the winner's key and replaces it (deleted, marked), so the other browser gets the message.
- A request in flight when its row is deleted (a login elsewhere, or `end_all_sessions`) is not saved (no `SessionInterrupted` 400): after the view, the middleware checks the row still exists (one query per authenticated request), logs out and sends the browser to login, with the replaced message when a login replaced it, none after `end_all_sessions`. A deletion in the instant between that check and the session save can still give the 400.
- Matrix row "Signup auto-login" (signup on B while A is logged in): it cannot occur as written, since signup always creates a new account. The tests show instead that signup's auto-login records its session like any login (a later login elsewhere ends it with the message), and that any new login (`force_login`) ends an earlier session.
- Spanish added by hand to `django.po`, since a full `makemessages` reorders the whole file.

## Plan Change Log

## Review Triage Log

**Pass 1 (quick lens, 2026-10-20):** high 0, medium 1, low 3, false 1, maybe-false 0. Four patches; one rejected.

| # | Finding | Verdict | Route | Evidence |
|---|---------|---------|-------|----------|
| 1 | A request in flight when its session row is deleted (a login elsewhere, or `end_all_sessions`) gets a 400 (`SessionInterrupted`), not the redirect | medium | patch | After the view, the middleware checks that the session wasn't ended meanwhile. If it was, it logs out without saving and redirects. The replaced message shows when a login elsewhere caused the end; `end_all_sessions` shows none. Tests `test_replaced_during_a_request_goes_to_login` and `test_end_all_sessions_during_a_request_goes_to_login`. Remaining: a deletion between that check and Django's save can still give a 400 (a narrow window). |
| 2 | Two simultaneous logins race on `current_session_key`; one browser ends without the message | low | patch | The login writes with the conditional `_set_current_key(..., check_expected=True)` in a retry loop. A loser replaces the winner, with its mark. Test `test_simultaneous_logins_the_loser_replaces_the_winner`. |
| 3 | Signup's auto-login runs the receiver inside signup's `transaction.atomic()` | false | rejected | No bad outcome: a new account has no previous session, and nothing follows `login` in that block. Noted for whoever edits it. |
| 4 | `test_signup_auto_login_ends_an_earlier_session` never touches signup | low | patch | Renamed `test_any_new_login_ends_an_earlier_session`. The matrix row "signup on B while A is logged in" can't occur (signup always creates a new account); noted in Implementation Notes. |
| 5 | The `CACHES` comment doesn't list the replaced-session marks | low | patch | Comment updated. |

After the patches (2026-10-20), on SQLite with Chromium (sandbox lifted): the full suite gives 1,167 passed, 3 failed (the known PostgreSQL-only tests). `makemigrations --check`: no changes.

## Verification

**Commands:**
- The local suite command (test keys, SQLite), outside the sandbox for the browser tests. Expected: only the 3 known PostgreSQL-only tests fail.
- `manage.py makemigrations --check` after adding the migration, and `compilemessages`. Expected: no changes, OK.

**Results (2026-10-20):** `DATABASE_URL=sqlite:///$TMPDIR/x.sqlite3 pytest -m "not browser"`: 1145 passed, only the 3 known PostgreSQL-only tests in `accounts/tests/test_crypto.py` failed (`test_sessions.py`: 28 passed; 31 after the review fixes). `pytest -m browser` outside the sandbox: 19 passed. `makemigrations --check --dry-run`: no changes. `compilemessages`: up to date. `check`: no issues.

CI on PostgreSQL 17 (PR #33): 1170 passed. Merged as `2d37d60`; the deploy ran migration `accounts.0008`. On staging the user reported (2026-10-21) these checks passed: a second login sends browser A to login with "…logged in on another device"; 15 idle minutes end the session with the inactivity message; a new login on a remembered browser still skips the code.
