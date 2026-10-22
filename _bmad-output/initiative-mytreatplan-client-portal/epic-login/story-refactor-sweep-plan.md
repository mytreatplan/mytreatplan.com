---
title: 'Refactor sweep'
type: 'refactor'
ticket: '11'
created: '2026-10-22'
status: 'built'
baseline_revision: '6cd623d5aea71a3c38f0518fa0af3d23b44b9377'
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

**Problem:** Epic-login's ten stories left behind repeated code and stale comments.
- **Repeated code:**
  - three copies of the form "refuse" helper (signup, login, reset);
  - three copies of the cache-mutex loop;
  - two copies of the "reserve an IP attempt, else log the first block" helper;
  - two copies of the unavailable-CAPTCHA record.
- **Leftovers:** an alias left by 3.2, and an unused read helper's stale note.
- **Stale comments.**
- **Tests:** accounts tests import each other's fixtures and helpers across modules, with `# noqa`, and redefine small helpers four times.

Only one item was left explicitly for this sweep (3.1's `refuse`). The only deferred epic-login item (the admin site's second factor) is a feature, out of scope.

**Approach:** A behaviour-neutral cleanup.
- Share the duplicated helpers once.
- Drop the alias.
- Correct the stale comments.
- Move the accounts tests' shared fixtures and helpers to `accounts/tests/conftest.py` and a helpers module.
- Do the optional items per decision 1. Treat the signup-named settings, keys and messages per decision 2.

## Boundaries & Constraints

**Always:**
- No change to behaviour, URLs, templates, texts, translations, settings names, env names, cache keys, session keys, log or Sentry messages and tags, or migrations.
- Each shared helper keeps every caller's exact behaviour: its counter, step, report function, message and tag.
- Shared test fixtures stay opt-in where they touch the database cache. An autouse `clear_cache` for every accounts test broke the IntegrityError tests in 3.1. Fixtures with the same names in different modules (`doctor`, `staff`) get distinct names in a shared conftest.
- The full suite has the same test count, give or take only the merged duplicate fixtures, and passes. `check` and `makemigrations --check` stay clean.

**Never:**
- New dependencies.
- Rewriting modules beyond the listed items.
- Touching ticket files, other plans' frozen blocks, or `deploy/` scripts.
- The admin site's second factor (deferred work).
- Unifying the `_now`/`_sleep` copies (decision 1).
- Renaming any signup-named setting, env name, cache key, log message or session key (decision 2).

**Decisions (2026-10-22):**
1. Besides the core set, two optional items:
   - (a) split `accounts/views.py`: the login guard (failure records, lockout, IP limit; about :226-380) moves to `accounts/login_guard.py`, and the views stay;
   - (c) one Sentry error reporter for `_report_counter_failure` and `password_reset._report`, with each caller keeping its exact message and tag.

   Not (b): the five `_now` and three `_sleep` copies stay as they are.
2. The signup-named items that login and reset also use get comments only, each saying it also serves login and reset: `SIGNUP_IP_LIMITS` and its env names, the `signup:ip:` key prefix, "Signup code send failed", `signup_code_state`. Nothing is renamed.

</frozen-after-approval>

## Code Map

- **A1, the refuse helper:** `accounts/forms.py:63-68` `LoginForm.refuse`, `signup/views.py:325-330` `_refuse`, and `accounts/password_reset.py:267-272` `ResetRequestForm.refuse`. Use one helper, e.g. `refuse(form, code, message=BLOCKED_MESSAGE)` in `signup/forms.py` or a `StyledForm` method.
- **A2, the alias:** `signup/views.py:449-450` `_code_help = code_help_context`. Call `code_help_context` at :479 and :629. Check that no test patches `_code_help`.
- **A3, `failure_count`:** `accounts/login_failures.py:147` is used only by tests. Keep it as a test API and correct its docstring ("for entry 5" no longer holds).
- **B5, the mutex loop:** `accounts/login_failures.py:40-42,50,82-93` (constants, `CounterBusy`, `_acquire`/`_release`), `accounts/password_reset.py:109-111,141,149-168` (inline), and `signup/limits.py:187-194` `_wait_ip_lock`. Use one `cache_mutex` helper with the same constants and exceptions. The tests that patch `_sleep` (`test_lockout.py:262,276`) follow the helper.
- **B6, reserve or block:** `accounts/views.py:349-370` `_reserve_login_attempt` and `accounts/password_reset.py:275-293` `_ip_blocked`. Use one helper taking the counter, the step and the report function.
- **B8, the unavailable CAPTCHA:** the `check_unavailable` record at `accounts/views.py:402-408` and `accounts/password_reset.py:343-347`, and `CHECK_UNAVAILABLE_EVENT` at `views.py:93` and `password_reset.py:98`. Define it once.
- **B9, the account lookup:** `by_email(email).only('pk').first()` at `accounts/views.py:269,288`. Use one small function.
- **B11, the event name:** `password_reset.py:97` `IP_BLOCKED_EVENT = 'rejected'`, also used for the per-email limit at :224. Rename the constant to `REJECTED_EVENT`; the value stays the same.
- **C, stale comments** (comments only):
  - `eventlog/service.py:2-3,22-23`;
  - `signup/captcha.py:1,4` (add the reset; rewrap);
  - `mytreatplan/settings.py:101` (add the reset; rewrap);
  - `signup/limits.py:12` (rewrap).
- **D, tests:** a new `accounts/tests/conftest.py` and `accounts/tests/helpers.py`.
  - **Fixtures and helpers** now imported across modules from `test_login.py`, `test_login_code.py`, `test_lockout.py`, `test_sessions.py` and `test_remembered_device.py`:
    - fixtures `doctor`, `login_clock`, `lock_clock`, `idle_clock`;
    - helpers `_login`, `_login_with_code`, `_logged_in`, `_html`, `_deactivate`, `_erase`, `_lock_by_passwords`, `_during_the_view`, `_mark`.
  - **Merges:**
    - `records_clock` with `lock_clock`;
    - `_to_code`/`_post_code`/`_right_code` (four variants);
    - `_wrong_password`, `_failures`, `_messages`, and the event readers;
    - the `META`/`IP`/`AGENT`/`WRONG_PASSWORD` and URL constants.
  - **Use the root conftest's helper:** `_no_secrets` becomes `_assert_no_secrets(show_leak=True)`.
  - **Opt-in fixture:** keep signup's `clear_cache` opt-in, via `pytestmark` usefixtures.
- **Decision 2 targets** (signup names that login and reset also use):
  - `mytreatplan/settings.py:104-112` `SIGNUP_IP_LIMITS` and its env names;
  - the `signup:ip:` key prefix at `signup/limits.py:156`;
  - "Signup code send failed" at `signup/steps.py:336`;
  - `signup_code_state` at `signup/steps.py:220`.

## Tasks & Acceptance

**Execution:**
- [x] A1–A3, B5, B6, B8, B9, B11 -- the shared helpers and the renamed constant; callers unchanged in behaviour.
- [x] C -- the comments.
- [x] D -- the accounts test conftest and helpers module; the cross-module imports and `noqa` gone.
- [x] `accounts/login_guard.py` (new) from `accounts/views.py` (decision 1a), with test patch targets following; one Sentry reporter (decision 1c).
- [x] The comments on the signup-named items (decision 2).

**Acceptance Criteria:**
- Given the repo, when searched, then `refuse` is defined once and the mutex loop once, and no accounts test module imports from another test module.
- Given the full suite (browser tests included), when it runs, then it passes with the same behaviour, and the 3.1–3.10 tests are unchanged except their imports.

## Implementation Notes

- **A1:** `signup.forms.refuse(form, code, message=BLOCKED_MESSAGE)`, a function (the reset form is not a `StyledForm`); the three copies are gone and the callers call it.
- **B5:** `accounts/cache_mutex.py`: `acquire` (the one wait loop) and `cache_mutex` (the per-key `:mutex` context manager, raising its `CounterBusy(key)`). Each caller passes its own constants and its own `_sleep` (looked up at call time), so the `_sleep` copies stay (decision 1) and the tests that patch `login_failures._sleep` and `limits._sleep` are unchanged. `limits._wait_ip_lock` uses `acquire` on the same `:lock` key.
- **1a:** `accounts/login_guard.py` holds the records, the lockout and the IP limit (`record_login`, `account_locked`, `login_locked`, `record_failure`, `password_rejected`, `login_code_rejected`, `reserve_login_attempt`, `refund_login_attempt`) and their constants; `finish_login` stays in the views. The guard logs under the `accounts.views` logger name, so its log lines are unchanged (a test reads `login code rejected` by that name). Test patch targets follow: `login_guard.login_locked`, `login_guard.login_code_rejected`, `login_guard.get_user_model`.
- **B6, B8, B9, 1c:** in login_guard: `reserve_ip_attempt(request, counter, *, step, report)`, `record_unavailable_captcha(request, step)` and `CHECK_UNAVAILABLE_EVENT`, `_account_by_email`, `report_error(log, prefix, tag, action, error)`. The password reset imports them.
- **D:** `accounts/tests/conftest.py` (`doctor`, `staff`, `clock`, `login_clock`, `lock_clock`, `idle_clock`, and opt-in copies of signup's `capture_codes` and `clear_cache`) and `accounts/tests/helpers.py`. `META` now carries a user agent everywhere, so two lockout assertions expect `AGENT` instead of `''`. test_page_records keeps its own `doctor` and `staff` (different users), which override the conftest's in that module.
- **Acceptance criterion "tests unchanged except their imports":** not met to the letter. Test bodies changed for the merges (patch targets to `login_guard`, `extra=META`/`remember='on'` passed explicitly, `_code`→`_right_code`, `records_clock`→`lock_clock`, `_records(event=…)`, shared IP/AGENT values, `_records` ordered by created_at/id); none changes what is asserted about the code, except the user-agent coverage, restored by a new test in test_login_records.py (a failure posted with `REMOTE_ADDR` only is recorded with `'user_agent': ''`). signup/tests/conftest.py's `capture_codes`/`clear_cache` bodies are plain generators (`codes_captured`, `cache_cleared`) that both conftests reuse.

## Plan Change Log

## Review Triage Log

**Pass 1 (quick lens, 2026-10-22):** high 0, medium 0, low 5, false 3, maybe-false 0. Four patches; four rejected.

| # | Finding | Verdict | Route | Evidence |
|---|---------|---------|-------|----------|
| 1 | `accounts/tests/conftest.py` imports signup's `clock` fixture with `# noqa` | false | rejected | The criterion targets accounts test modules importing each other. This is cross-app fixture reuse from signup's conftest, as before the sweep; copying `clock` would add a copy. |
| 2 | "3.1–3.10 tests unchanged except their imports" not met: bodies changed for the merges | low | rejected | The only fix is to reword this plan. The changes are patch targets, explicit helper arguments, renamed merged fixtures and shared constants; none changes what is asserted, except finding 3, now restored. Recorded in Implementation Notes and raised with the user. |
| 3 | The shared `META` carries a User-Agent, so no test checked any more that a request without one records `'user_agent': ''` | low | patch | `test_a_failure_without_a_user_agent_is_recorded_with_an_empty_one`. |
| 4 | `test_lockout`'s and `test_page_records`' IP constants changed to the shared one | false | rejected | Harmless: the cache is cleared per test, and no assertion depends on the value. |
| 5 | The shared `_records` orders by `(created_at, id)`, where the reset's ordered by `pk` | false | rejected | Equivalent here: records are created in order within a test. |
| 6 | `from .login_guard import` out of alphabetical order in `password_reset.py` | low | patch | Moved. |
| 7 | `'rejected'` named twice (`IP_BLOCKED_EVENT` in the guard, `REJECTED_EVENT` in the reset) | low | patch | One `REJECTED_EVENT` in `login_guard`, imported by the reset. |
| 8 | `capture_codes`/`clear_cache` bodies copied from signup's conftest | low | patch | Signup's conftest exposes `codes_captured`/`cache_cleared`; its autouse fixtures and the accounts opt-in ones wrap them. |

After the patches (2026-10-22), on SQLite with Chromium (sandbox lifted): the full suite gives 1,271 passed (1,270 plus the restored user-agent test), 3 failed (the known PostgreSQL-only tests). `makemigrations --check`: no changes.

## Verification

**Commands:**
- The local suite command (test keys, SQLite), outside the sandbox for the browser tests. Expected: only the 3 known PostgreSQL-only tests fail.
- `manage.py makemigrations --check`, `manage.py check`. Expected: clean.

**Results (2026-10-22):** `DATABASE_URL=sqlite:///$TMPDIR/x.sqlite3 pytest -m "not browser"`: 1250 passed, only the 3 known PostgreSQL-only tests in `accounts/tests/test_crypto.py` failed (the same as the baseline at 6cd623d). `pytest -m browser` outside the sandbox: 20 passed. `makemigrations --check --dry-run`: no changes. `check`: no issues.
