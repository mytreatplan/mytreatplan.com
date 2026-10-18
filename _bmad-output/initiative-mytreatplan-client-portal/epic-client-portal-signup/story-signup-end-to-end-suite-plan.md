---
title: 'Signup end-to-end suite'
type: 'feature'
ticket: '14'
created: '2026-10-18'
status: 'done'
baseline_revision: '103bc816f8d2108bd3494801c166abe1d6d293fe'
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

**Problem:** The signup epic's done-when 1 asks that the end-to-end suite prove a Doctor can complete the four steps on staging and land logged in. CAP-11 asks that every row of `registration-failure-policies.md` has a test. Today every row is covered by Django test-client tests, but only one runs in a browser: "connection lost" (`signup/tests/browser/test_connection.py`).

**Approach:** A Playwright suite in `signup/tests/browser/` with the fake verification provider (epic decision: real delivery is checked by entry 11). It covers:
- the spec's Success signal, in English and Spanish;
- every policy row, driven through the real pages: htmx, the vendored JS and the built CSS.

It reuses the existing browser setup, moved into the browser conftest.

## Boundaries & Constraints

**Always:**
- Marker `browser`. Skip as now when Playwright or Chromium is missing. CI runs the suite in its existing `pytest` step.
- Codes come from the fake's `current_code` (shared test DB), never from parsing pages.
- No network: Turnstile is the existing JS stub, and Debounce and Have I Been Pwned are the `external_checks` fakes.
- Each test is independent, with its own browser context, so tests run in any order.
- Assertions check what the person sees (text, URL, field values). The store, log or cookie state is also checked where the row's policy is about data (purge, anonymization, store cleared).

**Never:**
- Product code changes, except a test hook the plan names. None is planned.
- Real Twilio, KMS or Sentry.
- `time.sleep` waits. Use Playwright's auto-waiting, `expect`, and the `clock` fixture or backdated rows for time.
- Duplicating the test-client tests' internal assertions (event payload details, limit internals).

**Decisions (2026-10-18):**
1. "Staging-like" is CI: `live_server` with DEBUG off, compiled messages and built CSS. CI's database moves from PostgreSQL 16 to **17**, the version staging runs (17.11), so the whole suite runs against staging's major version.
2. Rows 2, 7, 10 and 13 run in the browser with in-process controls: the `clock` fixture, backdated rows plus `call_command` of the purge and retention commands, IP limits lowered through `settings`, and the Turnstile FAIL secret.
3. Playwright and Chromium are installed in the local venv by the user (`pip install -r requirements-dev.txt`, `python -m playwright install chromium`), so the browser tests run locally during implementation and review. Running Chromium may need the command sandbox lifted, with the user's approval.
4. (2026-10-18, at the matrix audit) Row 10's "4th send within the hour" means the spec's limit (`otp-verification.md`: max 3 resends per contact per hour). The first send and 3 resends are allowed; the 4th resend, the 5th send, is refused with the support contact.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| Success EN | New email and mobile, all steps, both codes | Lands on welcome logged in; T&C required, TPS and commercial consent unticked by default | — |
| Success ES | Same from `/es/` | Spanish pages and messages; welcome | — |
| Row 1 Cancel | Cancel at step 2, confirm | Store row gone; empty step 1 | — |
| Row 2 24h | Pending row backdated 24h, purge command run | Row gone; the same browser's next request starts fresh. After the attempt ends and 30 days pass, the retention command anonymizes the log | — |
| Row 3 / Row 4 Registered | An existing user's email (case variant) / mobile (other format) | Blocked with the recovery link | — |
| Row 5 Pending email | Another pending row with that email | Warning; correct resume code restores it; wrong code reveals nothing | — |
| Row 6 Page closed | Close the context mid-wizard, new context, same email | Resume after a code restores the data | — |
| Row 7 Idle | No request for 31 min (clock) | Session ended; resume via code restores data | — |
| Row 8 Not received | Resend after the cooldown; `+34699000001`; change email/mobile | New code works; SMS fallback shown; new contact gets the code | — |
| Row 9 Wrong codes | 5 wrong codes | "No longer valid"; a new code then works | — |
| Row 10 Resend limit | 4th send within the hour (clock) | Refused with the support contact | — |
| Row 11 Connection | (existing test) | Banner, input kept, retry | — |
| Row 12 Send fails | Mobile `+34699000002` | Retry page with support; registration stays pending | — |
| Row 13 Blocked | Turnstile FAIL secret; IP limit lowered | Generic error, typed values kept | — |
| Row 14 Disposable | `external_checks.disposable` | Professional-email message | — |

</frozen-after-approval>

## Code Map

- `signup/tests/browser/test_connection.py:41-112` -- autouse fixtures `plain_http`, `legal_documents` and `turnstile_script`, plus `_saved` and `_fill_step1`. Move them to `signup/tests/browser/conftest.py` (or `browser/helpers.py`) and keep `test_connection_lost_then_back` as row 11. Keep the module markers: `browser`, `django_db(transaction=True, serialized_rollback=True)`, `usefixtures('portal_urls')`. Keep `DJANGO_ALLOW_ASYNC_UNSAFE`.
- `signup/tests/browser/conftest.py:9-29` -- the Playwright/Chromium skip. Keep it.
- `signup/tests/helpers.py:11-19,75` -- constants and `_current_code(channel, contact, purpose)`. The Django-client helpers don't apply in a browser.
- `signup/verification.py:61-62,172` -- fake special numbers, `FakeVerificationProvider.current_code`.
- `signup/tests/conftest.py:16,45` -- the autouse `clear_cache` and `clock` (patches `signup.limits._now` and `signup.verification._now`; works under `live_server`, same process).
- `signup/views.py:62` `IDLE_TIMEOUT`, `signup/views.py:60` -- last-request time, for row 7 (move the clock, or patch the view's time source if it doesn't use `clock`).
- Root `conftest.py` -- `external_checks` (`.disposable`), `turnstile` and the `TURNSTILE_*` constants (FAIL secret), and `portal_urls`. `settings.SIGNUP_IP_LIMITS` (`settings.py:104`) is overridden through the `settings` fixture for row 13.
- `signup/management/commands/purge_pending_signups.py`, `eventlog/management/commands/eventlog_retention.py` -- run with `call_command` after backdating, for row 2 and the Success signal's anonymization.
- Existing client tests to mirror per row (texts and URLs): `test_wizard.py::test_happy_path`/`test_spanish_wizard`, `test_legal_step.py`, `test_duplicates.py`, `test_cancel_idle.py`, `test_mobile_code.py`, `test_abuse.py`, `test_step1.py::test_disposable_email_is_refused`, `test_retention.py`.
- `.github/workflows/ci.yml:16` -- `image: postgres:16`, to become `postgres:17` (decision 1). `ci.yml:58,64,67,79` already installs Chromium, compiles messages, builds CSS and runs `pytest`; those stay as they are.

## Tasks & Acceptance

**Execution:**
- [x] `signup/tests/browser/conftest.py` (+ `helpers.py` if cleaner), `test_connection.py` -- move the shared fixtures and helpers; `test_connection` still passes.
- [x] `.github/workflows/ci.yml` -- `postgres:17` service image -- decision 1.
- [x] `signup/tests/browser/test_success.py` -- Success EN and ES.
- [x] `signup/tests/browser/test_failure_policies.py` -- one test per matrix row 1–10, 12–14, each docstring naming its policy row.
- [x] `signup/tests/browser/__init__.py` docstring or module docstrings -- map each policy row to its test.

**Acceptance Criteria:**
- Given CI, when `pytest` runs, then every browser test runs (none skipped) and passes on PostgreSQL 17 with DEBUG off.
- Given the suite, when run 3 times in a row locally with Playwright installed, then it passes each time.
- Given `registration-failure-policies.md`, when its rows are compared with the browser tests' docstrings, then each row names at least one test.

## Implementation Notes

- Shared setup: the fixtures (`plain_http`, `legal_documents`, `turnstile_script`) and `DJANGO_ALLOW_ASYNC_UNSAFE` are in `browser/conftest.py`; the stub and the person's actions (`fill_step1`, `press`, `follow`, `enter_code`, `to_email_step`, ...) in `browser/helpers.py`. `press`/`follow` mark the page's h1 first and wait until it is replaced (swap or full load), so no step acts on the page being replaced.
- Row 7: the views' idle clock (`signup.views._now`) is not patched by `clock`; the test patches it with the same clock.
- Row 10: the limit allows the first send plus 3 resends (`HOURLY_SENDS = 4`); the test refuses the 4th resend (the 5th send), as `test_mobile_code.py` does.
- Rows 3 and 4 use a module URLconf with a `password_reset` route (as `test_duplicates.py`) so the recovery link shows.
- No product code changed.

## Plan Change Log

## Review Triage Log

**Pass 1 (quick lens, 2026-10-18):** high 0, medium 0, low 3, false 0, maybe-false 0. Two patches; one rejected.

| # | Finding | Verdict | Route | Evidence |
|---|---------|---------|-------|----------|
| 1 | AC 1 not shown met; nothing makes CI fail if the browser tests are skipped (`importorskip`, the Chromium skip) | low | rejected | The CI run is checked after the push: its log must show the browser tests passed, as each story's CI is. A silent skip is unlikely: `pytest-playwright` comes from the pinned `requirements-dev.txt`, and a failed `playwright install --with-deps chromium` fails its own CI step. Enforcing it would add a CI-only guard branch. |
| 2 | Row 8: after "Send a new code" the resent code is never entered, so "New code works" is unproven | low | patch | Reordered: change the email first, then after the cooldown resend to the new address and enter that code. The fake repeats a still-valid code (as Twilio does; `otp-verification.md`), so the test proves a code entered after a resend works, not that it differs. |
| 3 | Row 10's docstring says the resend is refused "until the window resets", which the test doesn't show | low | patch | Clause dropped. The matrix asks only for the refusal with the support contact. The "in 56 minutes" message stays checked. |

After the patches (2026-10-18), locally on SQLite with Chromium (sandbox lifted): `pytest -m browser` 16 passed on 3 runs in a row (about 40 s each). The full suite: 961 passed, 3 failed (the known PostgreSQL-only tests), none skipped. Only `signup/tests/browser/`, `ci.yml` and this plan changed.

## Verification

**Commands:**
- The local suite command (test keys, SQLite), with Playwright and Chromium installed if possible. Expected: only the 3 known PostgreSQL-only tests fail. Browser tests skip only if Chromium is unavailable in the sandbox; say so if they do.
- `pytest -m browser` 3 times. Expected: passes each time.

Done locally (2026-10-18), on SQLite with the test keys, Chromium run outside the command sandbox (it cannot start inside it): `pytest` 961 passed, 3 failed (the known PostgreSQL-only tests in `accounts/tests/test_crypto.py`), none skipped. `pytest -m browser` 3 times: 16 passed each time (~40 s); once more in reverse order: 16 passed. CI on PostgreSQL 17: not yet run (needs a push).

CI on PostgreSQL 17.11 (PR #27): 964 passed, none skipped, so all 16 browser tests ran. Merged 2026-10-18 as `a8c3d89`. The user reported that the staging deploy completed ok. Nothing to set up on staging.
