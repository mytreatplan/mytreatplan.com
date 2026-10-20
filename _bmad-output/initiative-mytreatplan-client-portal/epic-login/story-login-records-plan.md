---
title: 'Login records'
type: 'feature'
ticket: '4'
created: '2026-10-20'
status: 'done'
baseline_revision: 'fb30b8054c93701cd98a1345fd661084a03d39c7'
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

**Problem:** Logins leave no record (CAP-3). The spec asks for:
- every successful login, with its time, IP and User-Agent, tied to the account;
- failed attempts from the second consecutive one onwards. Wrong passwords and wrong second-factor codes are counted separately, per account, and both counts reset on a successful login. Failures for an email with no account are recorded too.

The event log (`eventlog`) has no link to an account. 3.1's unavailable-CAPTCHA case only writes a server log line.

**Approach:**
- Give `eventlog.Attempt` a nullable, indexed account reference: the user pk, stored as a plain number with no foreign key, so it outlives the account's deletion.
- Add a small activity-record API in `eventlog.service` that entry 7 reuses.
- Keep the two failure counters per account (and per typed email when there's no account).
- Record logins and failures through the API from 3.1–3.3's single places: `finish_login`, the password check and `login_code_rejected`.
- Move 3.1's unavailable-CAPTCHA warning into the event log as `check_unavailable`.

## Boundaries & Constraints

**Always:**
- Records go through `eventlog.service`. Personal values (IP, User-Agent, a typed email) live only in the encrypted `Event.data`. The other columns hold names and codes.
- A record is tied to the account by the user pk, never by the email blind index, which erasure replaces.
- Logging never breaks a login (the service's existing rule). Sentry only gets the event type and error class.
- Counters: one for wrong passwords and one for wrong codes, per account. Failures for an unknown email are counted under that email's blind index. A successful login resets both counts of the account. The first failure of a run leaves no record; the second and each later one leave one (the reason: `wrong_password`, `unknown_email`, `wrong_code` or `code_expired`; the counter; the count).
- A remembered-device login (3.3) is recorded like a code login, with a field saying no code was needed.
- The counters live where entry 5 can read them to lock (decision 2), behind functions that entry 5 calls.

**Never:**
- The lockout or IP limits (entry 5), page records (entry 7), or the 5-year retention job (entry 8).
- Passwords or codes in any record.
- Changes to signup's attempts or events.

**Decisions (2026-10-20):**
1. Until entry 8, the daily retention job leaves login attempts alone: `anonymize` and `delete_old` exclude `kind='login'`, so login records stay intact. Signup attempts keep their 30-day and 2-year rules.
2. The failure counters live in the database cache (`CACHES['default']`, as `signup/limits.py` does), one key per counter and per account (or per typed email's blind index). Each expires 24 hours after its last failure, and a successful login resets both counts of the account.
3. A failure for an email with no account records the typed email, normalized, in the encrypted `Event.data`, with the IP and User-Agent.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| Login | Password plus code | One login record: time, IP, User-Agent, account pk; counters reset | — |
| Remembered login | Password plus valid mark | One login record, marked "no code"; counters reset | — |
| 1st wrong password | Account, wrong password | No record; password count 1 | — |
| 2nd wrong password | Again | One record (`wrong_password`, count 2) | — |
| Wrong codes | 1st, then 2nd wrong code | None, then one record (`wrong_code`); code count separate from the password count | — |
| 5th code | `code_expired` | Recorded as a code failure | — |
| Unknown email | 2nd failure for an email with no account | One record with no account; the typed email, normalized, in its encrypted data (decision 3) | — |
| Reset | Failure, then a successful login, then a failure | That failure is a "first": no record | — |
| CAPTCHA unavailable | siteverify timeout | `check_unavailable` recorded in the event log; no server-log warning instead | — |
| Erased account | Account purged later | Its login records remain with the pk | — |
| Log write fails | The DB raises in `record` | Login goes on; Sentry error without values | — |
| Retention | The retention job runs past 30 days and 2 years | Login attempts untouched; signup attempts anonymized and deleted as before (decision 1) | — |
| Counter expiry | 2nd failure more than 24 h after the 1st (clock moved) | Counted as a 1st: no record (decision 2) | — |

</frozen-after-approval>

## Code Map

- `eventlog/models.py:17-35` -- `Attempt`: add `account_id` (nullable, indexed, not a ForeignKey) and a `LOGIN = 'login'` kind. Migration `eventlog/migrations/0002_...`.
- `eventlog/service.py:59-110` -- `start_attempt`, `record`, `end_attempt`, `request_data` (IP and User-Agent). Add the activity-record API, e.g. `record_activity(kind_or_event, *, request, account_id, reason='', field='', data=None)`, which creates and ends an attempt per record. Entry 7 reuses it for page records.
- `eventlog/retention.py` -- `anonymize` and `delete_old` apply to every attempt today (30 days and 2 years). See decision 1.
- `eventlog/admin.py` -- show `account_id` and `kind` (read-only), if the admin lists attempts.
- `accounts/views.py`:
  - `finish_login` (~164): record the login and reset the counters. A remembered-device login passes through it from `login`.
  - `login` (~197-235): record a password failure, with or without an account, on `invalid_login`.
  - :219: the `logger.warning('check_unavailable…')`, to become an event-log entry.
  - `login_code_rejected` (~187): record a code failure.
- Failure counters: a new `accounts/login_failures.py` (or `accounts/counters.py`), per decision 2. The email blind index for unknown emails comes from `accounts.crypto.email_index`.
- Tests:
  - `eventlog/tests/` patterns (`test_service.py`, the `sentry_capture` fixture in the root conftest);
  - `accounts/tests/test_login.py`, `test_login_code.py` and `test_remembered_device.py` helpers;
  - `signup/tests/conftest.py` `clock`.

## Tasks & Acceptance

**Execution:**
- [x] `eventlog/models.py` + migration -- `account_id`, the login kind.
- [x] `eventlog/service.py` -- the activity-record API.
- [x] `accounts/login_failures.py` (new) -- the counters (record a failure and return its count, reset, read for entry 5).
- [x] `accounts/views.py` -- the calls from `finish_login`, the password failure, `login_code_rejected`, and the unavailable CAPTCHA.
- [x] `eventlog/retention.py` -- per decision 1.
- [x] `accounts/tests/test_login_records.py` (new), `eventlog/tests` -- every matrix row.

**Acceptance Criteria:**
- Given an erased account that's been purged, when its login records are read by `account_id`, then they're all still there.
- Given the full suite, when it runs, then signup's event-log tests pass unchanged.

## Implementation Notes

- `Attempt.account_id` is a `BigIntegerField(null=True, db_index=True)` (migration `0002_attempt_account_id`); `Attempt.LOGIN = 'login'`.
- `eventlog.service.record_activity(kind, event, *, request=None, account_id=None, step='', field='', reason='', data=None)`: one attempt, one event, ended at once with `event` as its outcome. `start_attempt` takes `account_id`. If the event write fails, the attempt is deleted again, so no login attempt is left without its event. `_report_failure` is now the public `report_failure`. Counter errors, and errors in the account lookup before a counter call, are reported by `accounts.views._report_counter_failure` as "Login failure counter error: <action> (<ErrorClass>)", with no values.
- Records (kind `login`): `login` (step `login-code`, reason `code`, or step `login`, reason `remembered_device`; data `code_needed` plus IP/UA), `login_failed` (step `login`/`login-code`, field = counter `password`/`code`, reason `wrong_password`/`unknown_email`/`wrong_code`/`code_expired`, data `count` plus IP/UA, and `email` normalized for an unknown email), `check_unavailable` (step `login`, field `captcha`, reason `turnstile`, no account).
- `accounts/login_failures.py`: `record_failure(counter, *, account_id=None, email=None) -> count`, `failure_count(...)` (for entry 5), `reset(account_id)`. Values `{count, last}` in the default cache; the 24 h window is checked against `_now()`, with a cache timeout of 24 h + 60 s as a backstop.
- `finish_login(..., remembered=False)` records the login after `complete_login`; `login` passes `remembered=True`. A wrong password is detected as the form's `invalid_login` error; the account is looked up by `by_email` (an erased account's email is a tombstone, so it counts as `unknown_email`).
- Not counted: the no-mobile refusal (`refuse_login`, the password was right), a failed CAPTCHA, an empty or invalid form. A deactivated but not erased account's failures count as `wrong_password` under its pk, whatever the password.
- `login_code_rejected` keeps its `logger.info` line (3.2's test asserts it); only the CAPTCHA warning moved to the event log.
- Counters use get-then-set without a lock: two parallel failures can count as one. That is acceptable for the records; entry 5 may want a lock (as with `signup.limits.ip_reserve`) before relying on the count to lock accounts.
- Changed existing tests: `test_login.py` (the CAPTCHA-unavailable test now checks the event log), `test_remembered_device.py` (the `finish_login` spy passes `remembered`), `eventlog/tests/test_retention.py` (two tests that used kind `login` as an example of "every kind"; now they check decision 1). No signup test changed.
- For entry 5 (lockout): `accounts.login_failures` reads and then writes with no lock, so two simultaneous failures can count as one. Add a lock (as `signup.limits.ip_reserve`) before locking accounts on these counts. `failure_count(counter, account_id=…)` is the read API.

## Plan Change Log

## Review Triage Log

**Pass 1 (quick lens, 2026-10-20):** high 0, medium 2, low 3, false 0, maybe-false 0. Five patches.

| # | Finding | Verdict | Route | Evidence |
|---|---------|---------|-------|----------|
| 1 | `_password_rejected`'s account lookup (a DB query plus the blind-index HMAC) ran outside any guard, so its failure gave a 500 | medium | patch | The lookup now runs inside `_record_failure`'s try/except. Test `test_a_failing_account_lookup_still_shows_the_generic_error`. |
| 2 | A failed event write left a closed `login` Attempt with `account_id` and no Event: shown as a real login in the admin, never cleaned up | medium | patch | `record_activity` deletes the attempt when `record` returns None (`record` catches its own errors, so an outer transaction wouldn't roll back). The failing-write test asserts no login Attempt remains. |
| 3 | `anonymize` docstring line of about 110 characters | low | patch | Re-wrapped. |
| 4 | `_no_secrets` looked up the fake's current code at call time and checked nothing | low | patch | It now takes the submitted password(s) and code(s) and checks every column of the records. |
| 5 | Counter errors were reported to Sentry as "Event log write failed" | low | patch | `_report_counter_failure`: "Login failure counter error: <action> (<ErrorClass>)", with no values. |

Carried to entry 5:
- The counters are read and then written without a lock, so two simultaneous failures can count as one. Fine for records; the lockout should add a lock, as `signup.limits` does.
- A deactivated (not erased) account counts even a right password as `wrong_password`, since `authenticate` refuses it. The error shown is the same generic one.

After the patches (2026-10-20), on SQLite with Chromium (sandbox lifted): the full suite gives 1,113 passed, 3 failed (the known PostgreSQL-only tests). `makemigrations --check`: no changes.

## Verification

**Results (2026-10-20):** `DATABASE_URL=sqlite:///$TMPDIR/x.sqlite3 pytest` outside the sandbox: 1112 passed; only the 3 known PostgreSQL-only tests in `accounts/tests/test_crypto.py` failed. `makemigrations --check --dry-run`: no changes. `check`: no issues. `compilemessages`: OK.

**Commands:**
- The local suite command (test keys, SQLite), outside the sandbox for the browser tests. Expected: only the 3 known PostgreSQL-only tests fail.
- `manage.py makemigrations --check` after adding the migration, and `compilemessages`. Expected: no changes, OK.

CI on PostgreSQL 17 (PR #31): 1116 passed. Merged as `bf0f3cb`; the deploy ran migration `eventlog.0002`. On staging the user reported (2026-10-20) these checks passed in the admin: a login leaves a `login` attempt with the account id, whose event holds the time, IP and browser; of two wrong passwords only the second leaves a `login_failed` record (`wrong_password`, count 2); the second wrong code leaves a `wrong_code` record.
