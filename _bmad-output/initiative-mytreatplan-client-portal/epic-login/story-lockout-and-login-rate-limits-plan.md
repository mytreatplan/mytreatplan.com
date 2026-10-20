---
title: 'Lockout and login rate limits'
type: 'feature'
ticket: '5'
created: '2026-10-20'
status: 'built'
baseline_revision: '547c48c761ed2c860f585f3bc217b75fafd678d3'
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

**Problem:** Nothing yet stops password or code guessing beyond the CAPTCHA (CAP-5). The spec asks that:
- 5 wrong passwords, or 5 wrong second-factor codes, lock the account for 10 minutes;
- login attempts get per-IP limits, as signup has;
- refusals show a generic error that reveals nothing.

3.4's counters are read and then written without a lock, so two simultaneous failures can count as one.

**Approach:**
- Make 3.4's `accounts.login_failures` counters atomic, with a lock as `signup.limits` uses.
- When a counter reaches 5, lock its key (the account, or the typed email's blind index) for 10 minutes. A locked key refuses the login before the password is checked, and refuses the code page.
- Add a per-IP hourly limit on password submissions to signup's `SIGNUP_IP_LIMITS`, with its reserve, refund and first-block logging.
- Record locks and IP blocks in the event log through 3.4's `record_activity`.

## Boundaries & Constraints

**Always:**
- The lock check comes after the CAPTCHA and before `authenticate`. A right password, and a remembered device (3.3), are refused while the lock lasts. A locked account with a pending login can't use the code page either: the pending login ends, with the same refusal.
- Locking resets that counter, so after the 10 minutes a new run of 5 is needed. A successful login still resets both counts (3.4).
- Unknown emails lock the same way, under their blind index, so a lockout's behaviour doesn't tell whether an account exists.
- The per-IP limit counts password submissions that reach the credentials check, in signup's hourly window (`signup/limits.py` `ip_reserve`, `first_block`). Over the limit: the refusal, with no credentials checked. Its first block in a window is recorded.
- Messages: `BLOCKED_MESSAGE` for a lock or the IP limit (decision 1). Nothing says which counter, how many attempts are left, or whether the account exists.
- Counter and lock errors never stop a login, and they're reported as 3.4 does (`_report_counter_failure`, no values).
- Every new text is in EN/ES.

**Never:**
- An email to the Doctor about the lockout, or an admin unlock UI. Neither is in the spec.
- Changing signup's limits or their keys.
- Per-IP limits on code sends: signup's existing `CODE_SENDS` IP counter already covers login codes through `send_code`.

**Decisions (2026-10-20):**
1. A locked account (or locked typed email) and an IP over the limit see signup's `BLOCKED_MESSAGE` ("We couldn't process your request. Please try again later."), as a non-field error. On the code page the pending login ends and the login page shows it.
2. The per-IP limit is 60 password submissions per IP per hour (signup's window): `SIGNUP_IP_LIMITS['login_attempts']`, overridable with the env setting `SIGNUP_IP_LIMIT_LOGIN_ATTEMPTS`.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| 5 wrong passwords | 5th wrong | Locked 10 min; the refusal; lock recorded | — |
| Locked, right password | Right password within 10 min | Refused; no pending login; not logged in | — |
| Locked, remembered device | Right password, valid mark | Refused | — |
| Lock ends | 10 min + 1 s (clock moved) | The right password works; the next wrong one is a 1st | — |
| 5 wrong codes | 5th wrong code | Locked; pending login ended; the refusal | — |
| Locked mid-pending | Lock set while on the code page | Code page refuses; back to login | — |
| Unknown email | 5 failures for an email with no account | That email "locked" the same way | — |
| Separate counters | 4 wrong passwords, then 4 wrong codes | No lock | — |
| Concurrency | 2 simultaneous wrong passwords | Both counted | — |
| IP limit | Over the hourly limit | The refusal; credentials not checked; 1st block recorded | — |
| IP refund | CAPTCHA failed | Not counted against the IP | — |
| Errors | Cache raises | Login proceeds as without a lock; Sentry without values | — |
| Spanish | `/es/` | Spanish message | — |

</frozen-after-approval>

## Code Map

- `accounts/login_failures.py` -- 3.4's counters: `record_failure`, `failure_count`, `reset`, `_key`, `_now`, `FAILURE_WINDOW_SECONDS` (24 h). Add a lock around the read-then-write, the lock state (`lock(key)`, `is_locked(...)`, 10 minutes, `LOCK_SECONDS`) and the reset on lock.
- `signup/limits.py:49-58` -- `_lock`/`_unlock` (`cache.add`/`cache.delete`), the pattern to copy.
- `signup/limits.py:181-224` -- `_wait_ip_lock`, `ip_reserve(request, counter)`, `ip_refund`, `first_block`, `IP_COUNT_TIMEOUT`.
- `mytreatplan/settings.py:104-108` -- `SIGNUP_IP_LIMITS`. Add `'login_attempts': config('SIGNUP_IP_LIMIT_LOGIN_ATTEMPTS', default=60, cast=int)` (decision 2), plus a line in `deploy/staging.env.example` if the others are listed there, and its constant in `signup/limits.py` (like `SIGNUP_STARTS`).
- `accounts/views.py`:
  - `login` (~197-260): CAPTCHA → IP reserve → lock check → `LoginForm.is_valid()`.
  - `_record_failure` / `_password_rejected` (3.4): call the lock when the count reaches 5.
  - `login_code_rejected`: the same for codes, ending the pending login.
  - `_pending_login_view` (~143): refuse a locked account.
  - `_report_counter_failure`: reuse it.
- `accounts/forms.py` -- `INVALID_LOGIN_MESSAGE`, `LoginForm.refuse`, `refuse_login`. `signup.forms.BLOCKED_MESSAGE`.
- `eventlog.service.record_activity` -- the lock and IP-block records (kind `login`; events such as `account_locked` and `rejected` with `field='ip'`).
- Tests:
  - `accounts/tests/test_login_records.py` and `test_login_code.py` helpers;
  - the `clock` fixture; patch `accounts.login_failures._now` too;
  - signup's `small_limits` pattern in `signup/tests/test_abuse.py` for lowering the IP limit.

## Tasks & Acceptance

**Execution:**
- [x] `accounts/login_failures.py` -- atomic counters, the lock, the reset on lock.
- [x] `signup/limits.py`, `mytreatplan/settings.py` -- the `login_attempts` IP counter and its setting.
- [x] `accounts/views.py`, `accounts/forms.py` -- the lock and IP checks in `login`, the code page and the pending views; the `BLOCKED_MESSAGE` refusal; the records.
- [x] `locale/es/LC_MESSAGES/django.po` -- Spanish for any new text.
- [x] `accounts/tests/test_lockout.py` (new) -- every matrix row.

**Acceptance Criteria:**
- Given an account locked by 5 wrong passwords, when the right password is entered within 10 minutes, then the Doctor isn't logged in, and after 10 minutes (clock moved) they are.
- Given the full suite, when it runs, then signup's limit tests pass unchanged.

## Implementation Notes

- `login_failures`: each count's read-then-write runs under a per-key mutex (`cache.add`, waited for up to 1 s like `signup.limits._wait_ip_lock`; not got: `CounterBusy`, reported as any counter error). The count that reaches `LOCK_THRESHOLD` (5) calls `lock(...)` (`{'until': now + LOCK_SECONDS}`, 600 s, checked against `_now()`) and deletes that count, all under the mutex. The lock is per subject (`login:lock:account:<pk>` or `login:lock:email:<blind index>`), shared by both counters. `is_locked(account_id=, email=)` counts either lock.
- `views.login`: CAPTCHA, then `_reserve_login_attempt` (`limits.LOGIN_ATTEMPTS`; blocked: `form.refuse('rate_limited_ip')`, first block recorded `rejected`, field `ip`, reason `blocked: rate limit`, data `counter`), then `_login_locked(typed email)` (looks the account up; checks its lock and the email's), then the form. The reservation is given back for a locked refusal and for an empty email/password (neither reaches the credentials check). The 5th wrong password records `login_failed` (count 5) and `account_locked` (same step/field/reason/data) and refuses with `refuse('locked')`.
- Code pages: `_pending_login_view` ends a locked account's pending login (`_locked_out`: `messages.error(BLOCKED_MESSAGE)`, redirect to login), on `login_verify` too. The 5th wrong code does the same through `login_code_rejected`'s return value. The message reaches the login page through the messages framework, as `LOGIN_EXPIRED_MESSAGE` does (not as a form error).
- Refusals while locked are not counted and not recorded (no writes for locked traffic). No new user-facing text: `BLOCKED_MESSAGE` is already translated, so the `.po` is unchanged.
- Review fixes (2026-10-20): after a successful `authenticate` the account's lock is checked again (a parallel 5th failure that locked meanwhile still refuses: no pending login, no remembered-device skip). `first_block` has its own guard (reported as `first_block`), so an over-limit request is always refused. `record_failure` returns `Failure(count, locked, errors)`: setting the lock and clearing (or, when the lock failed, storing) the count are independent guarded steps; the view reports each failed step (`lock`, `clear_count`, `store_count`), always writes the `login_failed` record, and a lock that was set always refuses with BLOCKED_MESSAGE and records `account_locked`.
- Errors: `is_locked`, the lockout's account lookup (`lock_lookup`), `ip_reserve`/`first_block`, `ip_refund` and `lock` (inside `record_failure`) are each reported through `_report_counter_failure` and the login goes on as without a lock or limit.

## Plan Change Log

- 2026-10-20 (implementation): "signup's limit tests pass unchanged" could not hold to the letter. `test_defaults_are_the_test_keys_and_the_agreed_limits` requires `set(SIGNUP_IP_LIMITS) == set(IP_COUNTERS)` (so `login_attempts` joins `IP_COUNTERS`), while `test_cache_keys_hold_no_plain_ip` required a signup flow to write a key for every `IP_COUNTERS` entry. The latter now excludes `LOGIN_ATTEMPTS`; its no-plain-IP check is unchanged. Two 3.4 tests expected the 5th wrong code to show `code_expired` on the code page; now it locks: `test_the_fifth_code_counts_as_code_expired` asserts the redirect (the records are unchanged), and `test_five_wrong_codes_invalidate_it_and_a_new_one_works` resets the counter before the 5th so it still tests the provider's own per-code limit. `test_a_failing_account_lookup_still_shows_the_generic_error` now also expects the lockout lookup's report.

## Review Triage Log

**Pass 1 (quick lens, 2026-10-20):** high 0, medium 1, low 3, false 1, maybe-false 0. Three patches (four findings); one rejected.

| # | Finding | Verdict | Route | Evidence |
|---|---------|---------|-------|----------|
| 1 | AC "signup's limit tests pass unchanged" not met: `test_cache_keys_hold_no_plain_ip` was edited | false | rejected | The edit is forced: `test_defaults_are_the_test_keys_and_the_agreed_limits` requires `SIGNUP_IP_LIMITS == IP_COUNTERS`, and the plan puts `login_attempts` in `SIGNUP_IP_LIMITS`. Only the counter set it expects drops `LOGIN_ATTEMPTS`, which signup never uses; its no-plain-IP check is unchanged. The only other fix is a reworded criterion, i.e. an edit to this plan. Raised with the user as an accepted deviation. |
| 2 | Parallel submissions pass the single lock check, so a right password authenticated after the 5th wrong one locked the account still logs in or starts a pending login | medium | patch | After a successful `authenticate`, `login` checks `is_locked` again and refuses (no pending login, no remembered skip). Tests `test_lock_set_during_the_password_check_still_refuses` and `test_remembered_device_locked_during_the_password_check`. A burst is otherwise bounded by the CAPTCHA (one solved token per request) and the IP limit. |
| 3 | A `first_block` error, after `ip_reserve` refused, let the over-limit request through, reported as `ip_reserve` | low | patch | Separate guard, reported as `first_block`; the request is still refused. Test `test_failing_block_record_still_refuses`. |
| 4 | On the 5th failure, a failing `lock()` dropped the count and 3.4's `login_failed` record; a lock whose count clear failed showed INVALID_LOGIN_MESSAGE and recorded no `account_locked` | low | patch | `record_failure` returns `Failure(count, locked, errors)`; the lock, the clear and the store are each guarded. `login_failed` is always written; a set lock always shows BLOCKED_MESSAGE and records `account_locked`. Tests `test_failing_lock_write_keeps_the_count_and_the_record` and `test_failing_count_clear_still_locks_and_refuses`. |

After the patches (2026-10-20), on SQLite with Chromium (sandbox lifted): the full suite gives 1,136 passed, 3 failed (the known PostgreSQL-only tests). `makemigrations --check`: no changes.

## Verification

**Commands:**
- The local suite command (test keys, SQLite), outside the sandbox for the browser tests. Expected: only the 3 known PostgreSQL-only tests fail.
- `manage.py makemigrations --check`, `manage.py compilemessages`. Expected: no changes, OK.

**Results (2026-10-20):** `DATABASE_URL=sqlite:///$TMPDIR/x.sqlite3 pytest -m "not browser"`: 1113 passed, only the 3 known PostgreSQL-only tests in `accounts/tests/test_crypto.py` failed (`test_lockout.py`: 19 passed). `pytest -m browser` outside the sandbox: 19 passed. `makemigrations --check --dry-run`: no changes. `compilemessages`: up to date. `check`: no issues.
