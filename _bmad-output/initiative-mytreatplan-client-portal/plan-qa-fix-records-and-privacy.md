---
title: 'QA fix 1: records and privacy'
type: 'bugfix'
ticket: ''
created: '2026-10-23'
status: 'done'
baseline_revision: '820b631b5be1bedb52ca292b93d9d33da7d90bc0'
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

**Problem:** The specs-vs-code QA (`qa-spec-vs-code-2026-10-23.md`, items A2, A4, A5, A6, A7, A14 and A16, with the user's decisions recorded there) found these gaps:
- **A2:** signup's auto-login leaves no login record.
- **A4:** page records also cover the public site.
- **A5:** attempts refused during a lockout aren't recorded.
- **A6:** after an erased account is purged, its 5-year records can't be tied to the person.
- **A7:** "Code not received" is never logged as `OTP not delivered`.
- **A14:** a deactivated account's right password is counted and recorded as a wrong one.
- **A16:** phone numbers aren't redacted in Sentry's free text.

**Approach:** One fix per item, each from the user's decision:
- **A2:** record signup's auto-login as a login (reason `signup`), from `accounts/` through the `user_logged_in` signal or `login_guard`, without editing `signup/views.py`, which is the other session's R2/R3 work.
- **A4:** record pages only for the portal's routes (`signup.urls`, `accounts.urls`) and `/admin/`. The public site isn't recorded. A 404 is recorded only under a portal prefix (decision 1).
- **A5:** each attempt refused because of a lock is recorded as a `login_failed` record with reason `blocked: locked` (no count).
- **A6:** before deleting an erased account, the purge writes one activity record (kind `account`, event `account_purged`), tied to the pk and holding the encrypted email, kept 5 years with the other activity records (the lawyer: a minimal identity is allowed).
- **A7:** a resend or an SMS switch on a signup or resume code page records `OTP not delivered`.
- **A14:** a deactivated (not erased) account whose password is right is not counted as `wrong_password`. It's recorded with reason `inactive_account` and gets the same generic error.
- **A16:** phone numbers in Sentry's free text (event messages, exception values, breadcrumbs) are redacted, as emails and IPs already are.

## Boundaries & Constraints

**Always:**
- No change to texts, URLs, templates, translations, settings names or migrations, beyond what the items need.
- Records keep the rules of 3.4 and 3.8: personal values (email, IP, User-Agent, paths) only in the encrypted `Event.data`; no password or code; never block the request on a recording failure; reported to Sentry without values.
- `account` joins `ACTIVITY_KINDS` (`eventlog/retention.py`), so its records are never anonymized and are deleted 5 calendar years after they end. Other retention stays as it is.
- The lockout's behaviour is unchanged: the same messages, the same 10 minutes. A5 and A14 change only what's counted and recorded.
- Sentry redaction keeps its current shape: the same placeholder (`REDACTED`). It doesn't redact numbers too short to be phone numbers, so prices, IDs and HTTP statuses stay readable.
- Tests for every row below; the 3.1–3.12 tests pass, changing only where an item changes the behaviour they assert.

**Never:**
- Editing `signup/views.py`; the other session is changing it for R2/R3.
- Recording public-site pages.
- Storing the plain email, or anything beyond the email, in the purge record.

**Decisions (2026-10-23):**
1. A4, unresolved paths: a logged-in request that resolves to no view (a 404) is recorded only when its path, after the language prefix, is under a portal prefix: `signup/`, `login`, `logout/`, `welcome/`, `password-reset/`, `reset/`, or `/admin/`. Public-site 404s aren't recorded.
2. (2026-10-23, at review) A14: a deactivated account's right password counts toward the password failure count like any failed attempt, so the lockout can't reveal which guess was right. It's recorded with reason `inactive_account`, not `wrong_password`, and gets the same generic error. This replaces "is not counted as `wrong_password`" in the Approach as far as counting goes.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| A2 signup login | Signup completes with the mobile code | One `login` record with the new account's pk, reason `signup`, IP and User-Agent | — |
| A2 normal login | Portal login with the code | Still one record (`code`), not two | — |
| A4 portal page | Logged-in GET of `/en/welcome/` | One `page` record | — |
| A4 public page | Logged-in GET of `/en/` (home) or a legal page | No record | — |
| A4 admin page | Staff GET of `/admin/` | One `page` record | — |
| A4 unresolved | Logged-in GET of `/en/welcome/x/` (404); of `/en/no-such-page/` (404) | The first is recorded with `reason` 404; the second isn't (decision 1) | — |
| A5 during a lock | A login attempt for a locked account or locked email | `BLOCKED_MESSAGE`; one `login_failed` record, reason `blocked: locked` | — |
| A6 purge | An erased account past its retention is purged | One `account` record (`account_purged`), the pk and the encrypted email; the account is gone | — |
| A6 retention | The `account` record at 5 years minus a day, and at 5 years plus a day | Kept intact, then deleted | — |
| A7 resend | Signup email or mobile code page: "Send a new code" (sent) | An event with reason `OTP not delivered` | — |
| A7 SMS switch | "Send by SMS instead" (sent) | An event with reason `OTP not delivered` | — |
| A7 login codes | Resend on the login code page | No signup event (no attempt), unchanged | — |
| A14 inactive, right password | Deactivated account, right password | Generic error; counted like any failure (decision 2); recorded with reason `inactive_account`; 4 wrong passwords plus the right one lock as 5 wrong would | — |
| A14 inactive, wrong password | Deactivated account, wrong password | Counted as `wrong_password`, as now | — |
| A16 Sentry | An event message or exception with `+34 622 918 374`, `622918374` or `+34622918374` | Redacted; a 3- or 4-digit status or ID stays | — |

</frozen-after-approval>

## Code Map

- **A2:**
  - `accounts/sessions.py:110` `on_user_logged_in`: the existing `user_logged_in` receiver, which runs for every login.
  - `accounts/login_guard.py:107` `record_login(request, user, remembered)` and its reasons (`BY_CODE`, `BY_REMEMBERED_DEVICE`).
  - `accounts/views.py` `finish_login` calls `record_login` for portal logins.
  - Signup's `login(request, user)` is at `signup/views.py:401`. Don't edit it: detect signup logins in `accounts` (e.g. the portal login marks its own logins, so any other `user_logged_in` from the signup flow records reason `signup`), and avoid double records for portal logins and admin logins.
- **A4:** `accounts/page_records.py:58-68` `is_recorded`, and `mytreatplan/urls.py:36-47` (`portal_patterns` = `signup.urls` and `accounts.urls`; the public site is `mytp_publicsite.urls`; the admin is at `admin/`). Decide by `request.resolver_match`, the view's module or URL names, or by the path outside the language prefix.
- **A5:** `accounts/views.py:236-240` (the lock check before the password), `accounts/login_guard.py` `login_locked`, `record_failure`, and the records' event and reason constants (`LOGIN_FAILED_EVENT`, `REJECTED_EVENT`). The `blocked: …` reason style is `signup.forms.BLOCKED_RATE_LIMIT`.
- **A6:** `accounts/erasure.py:89-103` `purge_erased` (one transaction per batch; delete the legal choices, then the users); `eventlog.service.record_activity`; `eventlog/models.py` kinds; `eventlog/retention.py` `ACTIVITY_KINDS`. The erased user's email is still in `User.email` (encrypted field) at purge time.
- **A7:** `signup/steps.py` `send_code` (:255, `resend=` argument), `CodeStep.resend` and `MobileCodeStep.send_sms` (:420-475, the "We have sent you a new code" messages), and `signup.forms.FAILURE_REASONS` for the reason string. Signup and resume pages pass an attempt; login passes `attempt=None`.
- **A14:** `accounts/login_guard.py:160-166` (the account lookup when `authenticate` fails); check `user.check_password(password)` for an inactive, non-erased user without logging them in.
- **A16:** `mytreatplan/sentry.py:27-46` (`_EMAIL_RE`, `_IP_CANDIDATE_RE`, the text scrubber used on messages, exceptions and breadcrumbs), and its tests in `mytp_publicsite/tests/test_monitoring.py`.
- **Tests:**
  - `accounts/tests/conftest.py`, `helpers.py` (3.11): the shared fixtures, `lock_clock`;
  - `accounts/tests/test_login_records.py`, `test_lockout.py`, `test_page_records.py`, `test_erasure.py`;
  - `signup/tests/test_mobile_code.py` and `test_event_log.py` for A7;
  - `test_monitoring.py` for A16.

## Tasks & Acceptance

**Execution:**
- [x] A2: `accounts/sessions.py` or `accounts/login_guard.py`, with tests.
- [x] A4: `accounts/page_records.py`, with tests.
- [x] A5, A14: `accounts/login_guard.py` and `accounts/views.py`, with tests.
- [x] A6: `accounts/erasure.py`, `eventlog/models.py`, `eventlog/retention.py`, with tests.
- [x] A7: `signup/steps.py`, `signup/forms.py`, with tests.
- [x] A16: `mytreatplan/sentry.py`, with tests.
- [x] Update the QA file's A items to "fixed by plan-qa-fix-records-and-privacy".

**Acceptance Criteria:**
- Given the full suite, browser tests included, when it runs, then it passes, and no file under `signup/views.py` changes.
- Given an erased account purged, when its records are read by pk, then the `account_purged` record decrypts to its email.

## Implementation Notes

- **A2:** `login_guard.on_user_logged_in`, a second `user_logged_in` receiver (`accounts/apps.py`), records a login only when the request's resolved view is in `signup.views` (step `signup`, reason `signup`, `code_needed: True`). Portal logins (`accounts.views`, recorded by `finish_login`), admin logins and `force_login` are not matched, so no double record. `signup/views.py` is untouched. The record is written inside `_create_account`'s transaction (a savepoint), so a rolled-back signup leaves none. The accounts `doctor` fixture deletes that record, so the 3.x tests keep seeing only their own records.
- **A4:** `page_records.is_portal_path(path_info)`: `/admin/…`, or a LANGUAGES prefix followed by one of `PORTAL_PREFIXES` (decision 1). It decides resolved and unresolved paths alike; a test checks every route of `signup.urls` and `accounts.urls` is under a prefix. The test-only pages of `test_page_records.py` moved under `/en/welcome/test-pages/`.
- **A5:** `login_guard.record_blocked` (`login_failed`, reason `blocked: locked`, no field, no count) on the login page's lock check (which keeps its IP attempt, so the IP limit bounds these records), the "locked meanwhile" check, and POST submissions on the second-factor pages (step `login-verify` or `login-code`; a GET is not recorded). The 5th wrong code is the lock itself and stays `account_locked` only. An unknown locked email's record holds the normalized email.
- **A14:** `record_failure(..., password=)`: the account lookup it already made loads `password`, `is_active`, `erased_at`; a deactivated, not erased account with the right password (`hashers.check_password`, no setter, so no hash upgrade) is counted like any failed password (decision 2: recorded from the 2nd, locks at 5) with the reason `inactive_account`.
- **A6:** `Attempt.ACCOUNT = 'account'` joins `ACTIVITY_KINDS`. `purge_erased` reads each account's email inside a per-account guard and writes `_record_purge` (event `account_purged`, step `purge`, data `{'email': …}` encrypted, no IP/UA) inside the batch's transaction before deleting; an account whose email cannot be read (reported without values) or whose record could not be written is kept for the next run.
- **A7:** a sent resend (`send_code(..., resend=True)`: "Send a new code", "Send by SMS instead", the resume page's resend) logs its `code_sent` with reason `OTP not delivered` (`signup.forms.OTP_NOT_DELIVERED`). Login passes no attempt, so nothing is logged there.
- **A16:** `_PHONE_RE` in `mytreatplan/sentry.py`, run after the email and IP passes: 9–15 digits after a `+` (spaces, hyphens, brackets allowed) or 9–15 contiguous digits. Not redacted: shorter numbers, space-separated groups without `+`, digits inside words, decimals, or after a hyphen.

## Plan Change Log

## Review Triage Log

**Pass 1 (quick lens, 2026-10-23):** high 1, medium 2, low 3, false 0, maybe-false 0. Five patches (one after a user decision); one rejected.

| # | Finding | Verdict | Route | Evidence |
|---|---------|---------|-------|----------|
| 1 | A14: a deactivated account's right password was uncounted while wrong ones counted, so the moment of the lock revealed the right guess (a password oracle) | high | patch (decision 2) | The user chose to count it like any failure. It is recorded as `inactive_account`, and 4 wrong + the right one lock as 5 wrong would (`test_deactivated_account_locks_whichever_the_fifth_guess`). |
| 2 | A5: a lock refusal gave back its IP slot while writing a 5-year record, so records could be added without reaching the IP limit | medium | patch | No refund on a lock refusal. `test_attempts_during_a_lock_count_against_the_ip`. |
| 3 | A5: `_pending_login_view` recorded page loads and labelled verify-page refusals `login-code` | low | patch | Only POSTs are recorded, with `VERIFY_STEP` or `CODE_STEP`; GETs aren't (tests for each). |
| 4 | A6: the purge decrypted every email in a batch outside any guard, so one unreadable row aborted the purge | medium | patch | Each email is read in its own guard; an unreadable one is reported without values and kept for the next run (`test_purge_keeps_an_account_whose_email_cannot_be_read`). |
| 5 | A16: `_PHONE_RE` can swallow a 3-digit status written right after a phone number (`+34622918374 429`) | low | rejected | Rare (statuses are logged as `HTTP 429`, which the test shows intact), and a stricter pattern would add complexity. Known limits recorded: a phone with spaces and no `+` isn't redacted; a 9–15-digit run (an epoch timestamp, a large ID) is. |
| 6 | `scrub_event` docstring still said "emails and IPs" | low | patch | It now names phone numbers. |

After the patches (2026-10-23), in the worktree, on SQLite with Chromium (sandbox lifted): the full suite gives 1,337 passed, 3 failed (the known PostgreSQL-only tests). `signup/views.py` is unchanged.

## Verification

**Commands:**
- From this worktree, with the shared venv: `DATABASE_URL="sqlite:///$TMPDIR/qa1.sqlite3" "/Users/jadeandres/Library/CloudStorage/GoogleDrive-mytpdso@gmail.com/Mi unidad/Desarrollo Portal MyTreatplan/code/.venv/bin/python" -m pytest -q -p no:cacheprovider`, with the test keys, outside the sandbox for the browser tests. Expected: only the 3 known PostgreSQL-only tests fail.
- `manage.py makemigrations --check`. Expected: no changes (the `account` kind is a free CharField value).

Rebased onto master after PR #42 (R2/R3) merged (2026-10-23). One test needed changing: `/es/signup/` for a logged-in Doctor is now a redirect to welcome (R2), so it is not a page record. `test_signup_while_logged_in_redirects_and_is_not_recorded` covers that. Full suite on the rebased branch: 1,346 passed, 3 failed (the known PostgreSQL-only tests).

CI on PostgreSQL 17 (PR #43): 1349 passed. Merged as `f3b8949`. On staging the user reported (2026-10-23) these checks passed: a new signup leaves a `signup` login record; logged in, the home page leaves no page record and welcome does; an attempt during a lock is recorded `blocked: locked`; a signup resend logs `OTP not delivered`.
