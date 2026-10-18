---
title: 'Refactor sweep'
type: 'refactor'
ticket: '13'
created: '2026-10-18'
status: 'done'
baseline_revision: '9af7b259d83bee443d2f873267a8fe22fa611d75'
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

**Problem:** The signup epic (entries 1–12, plus the account-erasure fix) left behind:
- dotted paths repeated across modules;
- stale comments that still describe staging on the fake provider;
- one dead function;
- test fixtures and helpers copied between modules;
- CI actions that GitHub flags as deprecated (Node 20) and a runner label that moves to Ubuntu 26 on 2026-10-19.

No review finding was deferred: every triage row is patched or rejected.

**Approach:** A behaviour-neutral cleanup. Name the constants once, correct the comments, remove dead code, move the shared test helpers into conftests, and update CI. The rejected findings stay where they are recorded. The one feature left for later (KMS service key rotation) gets a deferred-work entry.

## Boundaries & Constraints

**Always:**
- No change to behaviour, URLs, templates, texts, translations, settings names or defaults, migrations, logs or Sentry output.
- Each moved test helper keeps its semantics. Where the copies differ (password, assert message), keep the differing values at the call sites or as parameters.
- The full suite has the same test count minus only the tests removed with dead code, and passes. `check` and `makemigrations --check` stay clean.

**Never:**
- New dependencies.
- Rewriting modules beyond the listed items.
- Touching ticket files, other plans' frozen blocks, or `deploy/` scripts.
- Calling the real Twilio or KMS.
- The shared HTTP helper for Twilio (decision 1).
- Unifying the story/entry reference style (decision 1).

**Decisions (2026-10-18):**
1. Besides the core set, include three optional items:
   - (b) separate the `signup.E001` system check from the disposable-email code;
   - (c) merge the duplicated "first block, then log rejected field=ip" helpers and the cache lock/release pairs;
   - (d) move the signup test flow helpers out of `test_wizard.py`.

   Leave out (a), the shared HTTP helper (the Twilio path was just verified on staging), and (e), the reference style.
2. CI runner: pin `ubuntu-24.04`.
3. The rejected review findings stay in each plan's triage log. Nothing is copied to `deferred-work.md`.

</frozen-after-approval>

## Code Map

- `signup/verification.py:8-9,55,119` -- stale "staging until Twilio" comments. The constants home: `FAKE_PROVIDER` lives here.
- `signup/twilio_verify.py` -- add `TWILIO_PROVIDER` constant. The module docstring uses `FETCH_ERRORS`.
- `signup/checks.py:52`, `mytp_publicsite/checks.py:15-16,94`, `conftest.py:20,37`, `mytreatplan/settings.py:67,195` -- hand-typed provider paths. `settings.py` must not import app modules (settings load first), so it keeps its literal strings with a comment naming the constants.
- `accounts/crypto.py:49` -- `DEFAULT_KEY_PROVIDER`, to reuse in `mytp_publicsite/checks.py:94` and `conftest.py:37`.
- `mytreatplan/settings.py:62-64,162,391-392`, `mytreatplan/sentry.py:105`, `mytp_publicsite/kms.py:7-8`, `accounts/http.py:1-2` -- stale or incomplete comments. The fake keeps a nonce in the cache, so "stores nothing" is wrong.
- `signup/forms.py:89-93` -- `phone_is_registered` hand-writes the blind-index filter. Add `UserManager.by_phone` beside `by_email` (`accounts/models.py`).
- `signup/limits.py:148` `ip_allowed` -- used only by `signup/tests/test_abuse.py:308,383`. Production uses `ip_reserve`. Remove it and turn those tests to `ip_reserve`, or drop them if `ip_reserve` already covers the same.
- Test duplicates, to move to the root `conftest.py` or a `tests/helpers.py`:
  - `release_jobs` in `accounts/tests/test_erasure.py:359`, `signup/tests/test_retention.py:248` and `eventlog/tests/test_retention.py:131`;
  - `_CaptureTransport` and `sentry_capture` in `mytp_publicsite/tests/test_monitoring.py:173` and `eventlog/tests/test_service.py:173`, plus the `# noqa: F401` imports in `test_backups.py:22` and `test_kms.py:23`;
  - `_runjob_with` in `test_backups.py:390` and `test_kms.py:592`;
  - `_assert_no_secrets` in `test_backups.py:135`, `test_kms.py:140` and `signup/tests/test_twilio_verify.py:76`;
  - `_env_values` in `accounts/tests/test_crypto.py:472` and `test_kms_provider.py:444`;
  - `at_legal` in `signup/tests/test_cancel_idle.py:46` and `test_legal_step.py:45`, to go to `signup/tests/conftest.py`.
- `signup/checks.py` -- holds the Debounce disposable-email code (`fetch_disposable`, `is_disposable_domain`, `email_domain`, `DISPOSABLE_EMAIL_CHECK`) plus `signup.E001` (`check_twilio_credentials`, `TWILIO_CREDENTIALS_MISSING`, registered from `SignupConfig.ready()` in `signup/apps.py`). The disposable-email code moves to `signup/disposable.py`, and `checks.py` keeps only E001 (Django's convention). To update:
  - the importers: `signup/forms.py:16`, `signup/tests/test_step1.py:19`;
  - the monkeypatch targets `'signup.checks.fetch_disposable'` in `conftest.py:127` and `test_step1.py:272,408,474`;
  - the comments naming `signup.checks`: `settings.py:301`, `signup/captcha.py:11`.
- `signup/views.py:327` `_log_ip_blocked` and `signup/steps.py:308` `_ip_blocked` -- both run "`limits.first_block` → `eventlog.record(..., 'rejected', field='ip', reason=BLOCKED_RATE_LIMIT, data={..., 'counter': counter})`". Keep one shared recorder (taking the attempt or a getter, and extra data). `_ip_blocked` keeps its outcome handling.
- `signup/limits.py:91-99` (send lock) and `167-173` (IP lock) -- the same `cache.add(key, 1, timeout)` / `cache.delete(key)` pair. Share a private `_lock` / `_unlock(key)`. Public names and timeouts stay unchanged.
- `signup/tests/test_wizard.py` -- defines the flow helpers and constants (`_step1`, `_legal`, `_email_code`, `_current_code`, `_code_from_log`, `STEP1`, `PASSWORD`, `NORMALIZED`, `MOBILE_E164`, `EMAIL_ADDRESS`, `ENCRYPTED`, `FIRST_NAME`, `LAST_NAME`, …). 8 modules import them `from .test_wizard import ...`: `test_step1`, `test_twilio_verify`, `test_mobile_code`, `test_event_log`, `test_abuse`, `test_duplicates`, `test_cancel_idle`, `test_legal_step`. Move them to `signup/tests/helpers.py`.
- `.github/workflows/ci.yml:12,41,43,49,86,99,101` -- `ubuntu-latest`, `actions/checkout@v4`, `setup-python@v5`, `setup-node@v4`.
- `_bmad-output/initiative-mytreatplan-client-portal/deferred-work.md` -- append-only.

## Tasks & Acceptance

**Execution:**
- [x] `signup/verification.py`, `signup/twilio_verify.py` -- define `FAKE_PROVIDER` / `TWILIO_PROVIDER`, and correct the stale docstrings -- one source for the paths.
- [x] `signup/checks.py`, `mytp_publicsite/checks.py`, `conftest.py`, the tests with literal provider paths -- import the constants and `DEFAULT_KEY_PROVIDER`.
- [x] `mytreatplan/settings.py`, `mytreatplan/sentry.py`, `mytp_publicsite/kms.py`, `accounts/http.py` -- correct the comments only.
- [x] `accounts/models.py`, `signup/forms.py` -- `UserManager.by_phone(phone)`, used by `phone_is_registered`. Add one test.
- [x] `signup/limits.py`, `signup/tests/test_abuse.py` -- remove `ip_allowed` and adjust its 2 tests.
- [x] Test modules above, root `conftest.py`, `signup/tests/conftest.py` -- move the shared fixtures and helpers, and delete the copies and `noqa` imports.
- [x] `signup/disposable.py` (new), `signup/checks.py`, its importers and patch targets -- move the disposable-email code, leaving only E001 in `checks.py` -- decision 1b.
- [x] `signup/views.py`, `signup/steps.py` -- one shared IP-block recorder -- decision 1c.
- [x] `signup/limits.py` -- private shared lock/unlock helpers -- decision 1c.
- [x] `signup/tests/helpers.py` (new), `signup/tests/test_wizard.py` and the 8 importers -- move the flow helpers and constants -- decision 1d.
- [x] `.github/workflows/ci.yml` -- `actions/checkout@v5`, `setup-python@v6`, `setup-node@v5`, and `runs-on: ubuntu-24.04` in both jobs -- decision 2.
- [x] `deferred-work.md` -- append an entry for KMS service key rotation (source: `story-kms-key-provider-plan.md`).

**Acceptance Criteria:**
- Given the branch, when the full suite runs on PostgreSQL 16 in CI, then it passes, with no Node 20 deprecation annotation.
- Given the repo, when `grep` searches app and test code for `'signup.verification.FakeVerificationProvider'`, `'signup.twilio_verify.TwilioVerifyProvider'` or `'accounts.crypto.EnvKeyProvider'`, then they appear only at their constant definitions and in `settings.py`.
- Given the repo, when the test directories are searched for `def release_jobs`, `class _CaptureTransport`, `def _runjob_with`, `def _env_values` or `def at_legal`, then each is defined once, and no module imports from `test_wizard`.
- Given the repo, when `signup/checks.py` is read, then it contains only system checks, and `signup.E001` still fires as before.

## Implementation Notes

- `UserManager` lives in `accounts/managers.py` (not `accounts/models.py`); `by_phone` went there. Its test is in `accounts/tests/test_user.py`.
- `signup/disposable.py` keeps the logger name `signup.checks`, so the disposable-email log lines (and their Sentry events) are unchanged.
- `signup/checks.py` now imports `CREDENTIAL_SETTINGS` and `TWILIO_PROVIDER` from `signup.twilio_verify` at module level (it was a lazy import); it is only loaded from `SignupConfig.ready()`.
- The root `conftest.py` imports `FAKE_PROVIDER` and `DEFAULT_KEY_PROVIDER` inside `pytest_configure`, so it loads no app module before Django is set up.
- The shared IP-block recorder is `signup.steps.log_ip_blocked(request, get_attempt, step, counter, data=None)`; `views.py` imports it. `_ip_blocked` keeps its outcome handling.
- `ip_allowed` had no test of its own: two assertions used it. The one after `ip_reserve(...) is None` was redundant and was dropped; the other now asserts `ip_reserve` still succeeds. Test count unchanged by this item.
- Shared helpers in the root `conftest.py`: `release_jobs`, `sentry_capture` / `_CaptureTransport`, `_runjob_with`, `_assert_no_secrets(secrets, *texts, show_leak=False)` (the Twilio tests pass `show_leak=True` for their assert message), `_env_values`. `at_legal` is in `signup/tests/conftest.py`. `_FakeSentry` stays in `test_monitoring.py` and is still imported from there (without `noqa`).
- Moving `_CaptureTransport` out of `test_monitoring.py` put a test IP within the 5 source lines Sentry sends around `_boom`'s `raise`; a comment block now separates them.
- Flow helpers and constants (and `ENCRYPTED`) are in `signup/tests/helpers.py`; the browser test and `accounts/tests/test_erasure.py` also imported from `test_wizard` and were switched too.
- Beyond the AC list, `test_kms_provider.py` now uses `crypto.KMS_PROVIDER`; the other `'accounts.crypto.KMSKeyProvider'` literals in tests were left.

## Plan Change Log

## Review Triage Log

**Pass 1 (quick lens, 2026-10-18):** high 0, medium 0, low 3, false 2, maybe-false 0. Three patches; two rejected.

| # | Finding | Verdict | Route | Evidence |
|---|---------|---------|-------|----------|
| 1 | AC 1 (CI on PostgreSQL 16, no Node 20 annotation) not shown met | false | rejected | Not a defect in the diff. The bumps are right (`ci.yml` has only `checkout@v5`, `setup-python@v6`, `setup-node@v5`, `ubuntu-24.04`); CI runs after the push, and the PR records the result. |
| 2 | `settings.py:62-63` says the fake keeps "only a random nonce", but its cache record also holds `sent_at` and `wrong` | low | patch | `signup/verification.py:125,145` confirm it. The comment now reads "a random nonce, the send time and the wrong attempts in the cache, never the code". |
| 3 | `test_settings_staging.py:418-422`: three blank lines left after removing the literals | low | patch | Now one blank line, as elsewhere in the file. |
| 4 | `test_backups.py:22`, `test_kms.py:23`: `from conftest import` between two `mytp_publicsite` imports | low | patch | Moved to its alphabetical place among the first-party imports, as in `accounts/tests/test_crypto.py`. |
| 5 | `deploy/staging.env.example:13-18` still selects the fake and says it "stores none" | false | rejected | "Writes each code … and stores none" means it stores no code, which is true. Selecting the fake first is the documented setup order for a fresh staging (README "Twilio Verify", Order: staging stays on the fake until the Verify service exists, then switches). Changing the default would make a fresh staging fail `signup.E001` without credentials. |

After the patches (2026-10-18), on SQLite: `pytest` 939 passed, 7 skipped, 3 failed (the known PostgreSQL-only tests). `check` reports no issues; `makemigrations --check` reports no changes; no template, locale, migration or `deploy/` file in the diff.

## Verification

**Commands:**
- The local suite command with test keys on SQLite. Expected: only the 3 known PostgreSQL-only tests fail.
- `manage.py makemigrations --check` and `manage.py check`. Expected: no changes, no issues.
- `git diff --stat` -- expected: no template, locale, migration or `deploy/` file.

Done locally (2026-10-18), on SQLite: `pytest` 939 passed, 7 skipped (PyYAML, and the browser test without Playwright), 3 failed (the known PostgreSQL-only tests). The baseline was 938 passed; +1 is the `by_phone` test. `check`: no issues; with the Twilio provider and empty credentials, `signup.E001` still fires. `makemigrations --check`: no changes. The grep acceptance checks pass. CI on PostgreSQL 16 and the Node 20 annotation: not yet run (needs a push).

CI on PostgreSQL 16 (PR #26): 949 passed, with no annotations (the Node 20 deprecation is gone). Merged 2026-10-18 as `1264d38`; the user reported that the staging deploy, the first on `ubuntu-24.04` with the new action versions, completed ok. Nothing to set up on staging.
