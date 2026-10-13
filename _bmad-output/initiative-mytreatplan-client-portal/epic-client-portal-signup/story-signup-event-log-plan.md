---
title: 'Signup event log'
type: 'feature'
ticket: '3'
created: '2026-10-13'
status: 'done'
baseline_revision: 'a73a4735df4a9db7a799dd2c55f7529fe638518c'
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

**Problem:** CAP-10 requires that every piece of data received or captured during signup is logged, so any attempt can be reconstructed step by step for debugging and fraud detection. Nothing records signup attempts today, and once an account is created, its `PendingRegistration` is deleted.

**Approach:**
- Add a per-attempt event log: an attempt row with start and end timestamps and an outcome, plus ordered event rows.
- Each event has non-personal columns: step, event type, field name and failure reason. Personal values go in one encrypted JSON column, so entry 10 can anonymize an event by clearing that column.
- A small API (start, record, end) is what every signup step calls; epic-login reuses it later. The tracer's steps (2.1) are logged through it.

## Boundaries & Constraints

**Always:**
- Personal values (email, IP address, browser string) are stored only in the encrypted column, using the 2.2 field encryption with its own associated data.
- `reencrypt_fields` must pick up the new column with no changes to the command.
- Passwords are never logged in any form, including hashes. A password event records only the field name and the validation code.
- Failure reasons use the wording in `registration-failure-policies.md` (`email exists`, `OTP verification failed`). Other validation failures use the form error code.
- Logging must never break signup. A failure while writing the log is reported to Sentry without the personal values, and the request carries on.
- The attempt id lives in the session next to the pending id. `PendingRegistration` gets a nullable FK to its attempt, which entries 4 (resume) and 10 (purge) need.
- An attempt survives the account's creation and the pending row's deletion.
- Decision (user, 2026-10-13): verification codes are never logged, sent or entered. Code events record only sent, accepted or rejected.
- Decision (user, 2026-10-13): every event recorded with a request stores the client IP (the last `X-Forwarded-For` entry, else `REMOTE_ADDR`) and the user agent in the encrypted data column, so they are anonymized with it.
- Decision (user, 2026-10-13): the log is a new generic `eventlog` app (models plus `service.py`), which signup uses now and epic-login reuses later.

**Never:**
- Retention, the 30-day anonymization or the 2-year deletion: those are entry 10. This entry only provides the attempt's end timestamp and the nullable data column they need.
- Logging personal values to the console, the journal or Sentry.
- Making events editable in the admin.
- Login or activity logging (epic-login).

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| Happy path | Step 1 valid, code sent, correct code | The attempt has, in order: `step_saved`(account, email), `code_sent`(email, channel), `code_accepted`, `completed`. `ended_at` is set with outcome `completed`. | No error expected |
| Registered email | Step 1 with an existing user's email | An event `rejected`, field `email`, reason `email exists`, with the email in the data. The attempt stays open. | The form shows its error as today |
| Weak or mismatched password | Step 1 password errors | One `rejected` event per field error, with the error code and no password data | No error expected |
| Wrong code | Step 2 with a bad code | `rejected`, field `code`, reason `OTP verification failed` | No error expected |
| Resend | Step 2 resend | `code_sent`, with `resend` marked in the data | No error expected |
| Race at creation | Email registered while the attempt was in progress | `rejected`, reason `email exists`, at step `verify-email` | As in 2.1 |
| Log write fails | Database error inside `record` | Signup completes. Sentry gets an event naming the event type, without personal values. | Caught in the API |
| Raw row | Any event with data | A SQL select shows `enc:v1:` in the data column and no plaintext email or IP | — |

</frozen-after-approval>

## Code Map

- `signup/views.py` -- generic wizard. Hooks go here so that no step is named:
  - an invalid form: one `rejected` event per error (`form.errors.as_data()`);
  - after `step.save`: a `step_saved` event;
  - `_create_account`: a `completed` event, or a `rejected` event when the `IntegrityError` race hits;
  - the session key sits next to `SESSION_KEY`.
- `signup/steps.py` -- `Step` gets a hook returning the loggable values of a valid form. For `AccountStep` that is the email only, never the password. `EmailCodeStep._send`/`resend` record `code_sent`.
- `signup/forms.py` -- error codes `email_registered` and `invalid_code` map to the policy reasons.
- `signup/models.py` -- add `attempt` FK (null, `SET_NULL`). Next migration is `signup/0005`.
- `accounts/crypto.py:247-295` -- `EncryptedFieldMixin`: add an `EncryptedJSONField` (JSON through `encrypt`/`decrypt`). `encrypted_fields()` and `reencrypt_fields` find it automatically.
- Client IP: Nginx sets `X-Forwarded-For $proxy_add_x_forwarded_for` (`deploy/nginx/mytp.conf.template:63`). Use its **last** entry, the one Nginx appended; fall back to `REMOTE_ADDR`.
- `mytreatplan/settings.py:128` -- `INSTALLED_APPS`; `pytest.ini` -- add the new app to `testpaths`.
- `mytreatplan/sentry.py` -- the scrubbing pattern; capture the log failure with no data.
- Tests: `signup/tests/test_wizard.py` (2.1 and 2.2 tests, the raw-SQL ciphertext check), root `conftest.py` (`portal_urls`).

## Tasks & Acceptance

**Execution:**
- [x] `accounts/crypto.py` -- `EncryptedJSONField`.
- [x] `eventlog/` app (models, `service.py` API, read-only admin with events inline and decrypted, migration) -- the generic log.
- [x] `signup/models.py`, migration, `signup/views.py`, `signup/steps.py` -- attempt per signup, events from the tracer.
- [x] `mytreatplan/settings.py`, `pytest.ini` -- register the app.
- [x] tests -- every matrix row; `reencrypt_fields` covers the new column; the API on its own.

**Acceptance Criteria:**
- Given a completed tracer signup, when an admin opens its attempt, then every step, sent code and the outcome appear in order with their times.
- Given CI on PostgreSQL, when the suite runs, then all tests pass, including the raw-SQL ciphertext check.

## Implementation Notes

- Steps declare `saved_event` (default `step_saved`; `EmailCodeStep` uses `code_accepted`) and `log_fields` (`AccountStep`: `email` only), so the views stay generic. `Step.log_data` returns the cleaned value, or the value as received when the field was rejected (so `email exists` logs the email as typed).
- `code_sent` reads the attempt from `pending.attempt_id`, so steps need no session access.
- The views resolve the attempt from the session, else from the pending row; an ended or missing attempt is dropped, and only a POST to step 1 starts a new one. A stale pending id also clears the attempt id.
- `completed` and `end_attempt` run after the account transaction commits; the race `rejected` event after its rollback.
- Every API call writes inside its own savepoint and catches any error; Sentry gets `Event log write failed: <action> <event> (<ErrorClass>)` only, never the exception text.
- Admin: `Attempt` view-only (no add/change/delete) with an `Event` tabular inline, data shown decrypted as JSON. `Event` has no admin page of its own.
- Verified on SQLite (only the 3 known PostgreSQL-only tests fail) and on a local PostgreSQL 17 (438 passed); `makemigrations --check` clean.

## Plan Change Log

## Review Triage Log

**Pass 1 (quick lens, 2026-10-13):** high 0, medium 0, low 5, false 1, maybe-false 0. One patch; five findings rejected.

| # | Finding | Verdict | Route | Evidence |
|---|---------|---------|-------|----------|
| 1 | `EncryptedJSONField`: a serialize/deserialize round trip double-encodes `data` | low | patch | Reproduced by the reviewer. `to_python` returns the JSON string as is, and `get_prep_value` encodes it again. Fix: `to_python` parses string input; a round-trip test is added. |
| 2 | Signups already past step 1 at deploy are never logged | low | rejected | Real, but staging only, it lasts at most one 24h pending window, and signup is not in production. The fix adds a new branch that starts an attempt for a pending row without one. |
| 3 | A failed code send is not logged (`OTP send failed`) | low | rejected | The fake provider does not fail, and send-failure handling (retry, alert, reason) is entry 7. Logging it here would add an error branch that entry 7 replaces. |
| 4 | Staff without the Event permission see an attempt with no events | low | rejected | Only the devops superuser has admin access. The fix adds permission overrides on the inline. |
| 5 | A double submit at the last step can log a second `code_accepted` | low | rejected | Needs two concurrent final POSTs. The extra event is harmless to reconstruction, and the fix reorders the generic view's save and log. |
| 6 | A deferred FK error inside an outer transaction would break the caller's commit | false | rejected | No caller runs inside a transaction: there is no `ATOMIC_REQUESTS`, and the wizard calls `record` outside `transaction.atomic`. The claim is about future callers only. |

## Design Notes

- **Model sketch:**
  - `Attempt`: UUID pk, `kind` (`signup`; login adds its own), `started_at`, `ended_at` (null, indexed), `outcome`.
  - `Event`: `attempt` FK (cascade), `created_at`, `step`, `event`, `field`, `reason`, `data` (`EncryptedJSONField`, null).
- **API sketch:** `start_attempt(kind)`, `record(attempt, event, *, request=None, step='', field='', reason='', data=None)` and `end_attempt(attempt, outcome)`. When `request` is given, `record` adds the IP and user agent to `data`.
- **Start of an attempt:** at the first POST to step 1 with no attempt in the session. Page views alone are not data received.

## Verification

**Commands:**
- `DATABASE_URL="sqlite:///$TMPDIR/x.sqlite3" ../.venv/bin/python -m pytest -q` -- expected: only the known PostgreSQL-only tests fail.
- `manage.py makemigrations --check` (with test keys) -- expected: no changes.

Done locally (2026-10-13) after the review patch, on SQLite: `pytest` 436 passed, 6 skipped (PyYAML), 3 failed only because those tests use PostgreSQL-only raw SQL. The implementer's run on a throwaway PostgreSQL 17 passed all 438 tests before the patch. `makemigrations --check` reports no changes; `check` reports no issues (1 silenced). CI on PostgreSQL 16 (PR #14): 445 passed.

Staging (2026-10-13): the merge deployed and ran migrations `eventlog.0001` and `signup.0005`. A person completed a signup at https://dev.mytpdso.com/en/signup/ and opened its attempt in the admin (Eventlog → Attempts): `step_saved`, `code_sent`, `code_accepted` and `completed` appeared in order, with outcome `completed`.

**Manual checks:**
- After the merge, run a signup on staging and open the attempt in the admin. The steps, code sends and outcome should be listed.

