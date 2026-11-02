---
title: 'Verification provider and codes in mytp-core'
type: 'refactor'
ticket: '2'
created: '2026-11-02'
status: 'built'
baseline_revision: '4edd042f1f4562918ae4d484ff423dfbb274e28d'
route: 'full'
route_source: 'auto'
risk: 'high'
review: 'quick'
review_source: 'pinned'
lenses_ran: ['quick']
review_loop_iteration: 0
context: [] # code repos: mytp-core (new v0.4.0) and mytreatplan.com (portal); baseline_revision is the portal commit, mytp-core starts at 99850e3 (v0.3.0)
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** A2: the admin's 2FA (entry 4) must reuse the portal's code verification. Today that code lives in the portal (`signup.verification`, `signup.twilio_verify`, and the send orchestration in `signup.steps`), tangled with portal-only limits and the event log.

**Approach:** Move the provider interface, the fake and Twilio Verify providers, code check, and send orchestration into mytp-core as v0.4.0. Portal-only limits and event logging become per-site hooks. The portal vendors 0.4.0, keeps its modules as thin subclasses or re-exports with the same names, and behaves exactly as before.

## Boundaries & Constraints

**Always:**
- **No behaviour change in the portal.** The portal's existing tests pass without edits. These must keep working:
  - `monkeypatch.setattr('signup.verification._now', …)`, `'signup.twilio_verify.post_verify'` and `limits.*`;
  - the logger name `signup.verification` (caplog helpers, LOGGING, the Sentry ignore list, and reading the dev code from the log);
  - the log text `Signup code send failed:`;
  - the cache prefix `signup:fake-otp:`;
  - the `VERIFICATION_PROVIDER` dotted paths already in settings.env files (`signup.twilio_verify.TwilioVerifyProvider` and the fake).
- **Core has no portal knowledge.** It never imports a portal package. Its logger name and fake cache prefix are class attributes with core defaults (`mytp_core.verification`, `mytp-core:fake-otp:`), and the portal's subclasses set the old values.
- **Overridable seams.** Core providers call `self._now()` and `self._post_verify(...)`. The portal subclasses route these to the module-level `signup.verification._now` and `signup.twilio_verify.post_verify`, so the existing patches still apply.
- **Hooks are an object passed to the orchestrator.** Core ships a no-op default, which admin entry 4 can use. Hook methods: `acquire_lock`/`release_lock(contact, purpose)`, `check_send(contact, purpose) -> SendCheck`, `reserve() -> reservation|None` (None = blocked), `refund(reservation)`, `record_send(contact, purpose)`, `event(kind, **data)` for rejected, ip_blocked, send_failed and sent. The portal's hooks wrap `signup.limits` and `eventlog`, closing over the request, attempt, step and `login_record`.
- **What moves to core:** `SendOutcome` and its statuses. Session/UI state (`_store_outcome`, `code_state`, `CODE_STATE_SESSION_KEY`), `CodeForm`, captcha, `login_guard`, `signup.limits`, `eventlog`, and the system checks E001/E006 stay in the portal.
- **Release:** core gets tests with the fake provider (send, check, expiry, attempts) and Twilio (`_post_verify` stubbed), on SQLite and PostgreSQL. The version is 0.4.0 in `pyproject.toml` and `__init__`, with the README sections updated, and it's tagged locally. Verify from a clean clone of the tag, then vendor with `scripts/vendor-mytp-core.sh v0.4.0`.

**Never:**
- No change to mytp-admin (it stays on 0.3.0 until entry 4).
- No remembered devices, lockout or single session (entry 3). No admin sign-in (entry 4).
- No push of mytp-core or its tag before the user's review. The user pushes.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| Send OK | core `send_code` with fake provider + recording hooks | SENT; hooks: lock, check_send, reserve, record_send, event sent, unlock | — |
| Cooldown / hourly | `check_send` returns not allowed | COOLDOWN or HOURLY with wait; no provider call; event rejected | lock released |
| IP blocked | `reserve()` returns None | BLOCKED; event ip_blocked; no provider call | — |
| WhatsApp unavailable | provider raises `ChannelUnavailable` on whatsapp | falls back to SMS; outcome fallback=True | — |
| Send fails | provider raises `SendFailed` | FAILED; `refund` called; event send_failed; error logged on the provider's logger | — |
| No-op hooks | default hooks | SENT, no limits | — |
| Check | right / wrong / expired / too many | APPROVED / WRONG / NO_CODE as today | `CheckFailed` propagates |
| Portal unchanged | the portal suite on vendored 0.4.0 | the same pass count as before, apart from the 3 known PostgreSQL-only failures | — |

</frozen-after-approval>

## Code Map

Portal (`mytreatplan.com/`):
- `signup/verification.py` (205 lines) -- constants L29–64, errors L67–82, `contact_kind` and `contact_index` L85–95 (built on `mytp_core.crypto` email_index/phone_index), `VerificationProvider` L102, `FakeVerificationProvider` L123 (cache plus SECRET_KEY, module `_now`), and `get_provider()` L203 (`settings.VERIFICATION_PROVIDER`). This becomes a re-export of core plus a `FakeVerificationProvider` subclass that keeps `_now`, the logger and the cache prefix.
- `signup/twilio_verify.py` (190 lines) -- `post_verify` L82, `TwilioVerifyProvider` L131 (`TWILIO_WHATSAPP_ENABLED` gate L136), `CREDENTIAL_SETTINGS`, `TWILIO_PROVIDER`. It uses `accounts.http.FETCH_ERRORS` (L18). Keep the module-level `post_verify` as a wrapper over core's, plus a subclass whose `_post_verify` calls it.
- `signup/steps.py` `send_code` L280 to `_send` L403 -- the generic flow that moves. Hook calls today: `limits.acquire_send_lock`/`release_send_lock` (L294/303), `limits.check_send` (L308), `limits.ip_reserve`/`ip_refund` (L318/329), `limits.record_send` (L390), `eventlog.record(...)` (L312/383/397), and `_ip_blocked`/`log_ip_blocked` (L333–360, `limits.first_block`, `eventlog.record_activity`). Keep `send_code`'s signature (with `login_record=`) as the portal entry point. It builds the portal hooks and calls core.
- Callers that must keep working: `signup/steps.py` CodeStep (L477+), `signup/views.py` L49/L577, `accounts/views.py` L52–54 and L302–360 (login code), `signup/forms.py` `CodeForm.clean_code` L463–483, and the constant imports in `accounts/forms.py:8`, `signup/checks.py:5`, `mytp_publicsite/checks.py:8-9`, `conftest.py:36` and `settings.py:117`.
- Tests that constrain names (don't edit): `signup/tests/conftest.py:59-60`, `test_twilio_verify.py:73,485`, `accounts/tests/test_login_code.py:650,758`, `mytp_publicsite/tests/test_monitoring.py:677`, `signup/tests/helpers.py:70`, `test_verification.py:35`, `test_abuse.py:76`, `test_lockout.py:367,437`.
- `scripts/vendor-mytp-core.sh`, `requirements.txt:31`, `vendor/`.

mytp-core (`mytp-core/`):
- New `mytp_core/verification.py` (constants, errors, interface, fake, `get_provider`, `SendOutcome`, `SendCheck`, hooks base, `send_code`, `FETCH_ERRORS`) and `mytp_core/twilio_verify.py`. `mytp_core/crypto.py` L434/438 index helpers.
- `tests/settings.py` (force-tracked) -- add `VERIFICATION_PROVIDER`, `TWILIO_*`, `EXTERNAL_CHECK_*` and a cache. `pyproject.toml`, `mytp_core/__init__.py:12`, README ("What moved", "Use it", "Compatibility rules"), and `.github/workflows/ci.yml` (it asserts the tag equals the version).
- Precedent: `epic-platform/story-mytp-core-package-with-field-encryption-and-kms-plan.md` and `story-shared-operations-code-in-mytp-core-plan.md` (shims, clean-clone check, re-tag before push, sandbox off for `../mytp-core`).

## Tasks & Acceptance

**Execution:**
- [x] mytp-core: the two modules, `tests/test_verification.py` and `tests/test_twilio_verify.py` covering every matrix row except the last, the version bump, the README, and a local commit with tag `v0.4.0`
- [x] portal: vendor 0.4.0, then turn `signup/verification.py` and `signup/twilio_verify.py` into core subclasses and re-exports, and `signup/steps.py` `send_code` into portal hooks plus a core call
- [x] the portal full suite, with no test file changed

**Acceptance Criteria:**
- Given the vendored 0.4.0, when the portal suite runs, then the result equals the baseline, and `git diff --stat` shows no change under any `tests/` directory.
- Given dev after deploy, when the user signs in with a code (the code read from the log as today), then the sign-in works.

## Verification

**Commands:**
- in mytp-core: `.venv/bin/python -m pytest -q` and `MYTP_CORE_TEST_DB=postgres …` -- pass. Clean clone of the tag builds the wheel.
- portal: the suite command -- the same as the baseline (3 known failures). `python manage.py check`, `makemigrations --check`.

**Manual checks (if no CLI):**
- dev (user): log in with a code. Signup step 2 sends a code.

## Implementation Notes

- mytp-core: commit `879bd20` on its branch `story/3.2-verification`, tagged `v0.4.0` locally (not pushed). New `mytp_core/verification.py` (constants, errors, `contact_kind`/`contact_index`, `FETCH_ERRORS`, `VerificationProvider` with `logger_name` and classmethod `_now()`, `FakeVerificationProvider` with `cache_prefix`, `get_provider`, `SendOutcome` and statuses, `SendCheck`, `SendHooks`, `send_code`) and `mytp_core/twilio_verify.py` (`post_verify`, `TwilioVerifyProvider._post_verify`). Tests: `tests/test_verification.py`, `tests/test_twilio_verify.py`; `tests/settings.py` gains `VERIFICATION_PROVIDER`, a locmem cache, `TWILIO_*` and `EXTERNAL_CHECK_*`. README: module list, "Use it in a site" step 7, compatibility rules, the tests note.
- Core `send_code(contact, purpose, channel, language=None, *, provider=None, hooks=None)`. Hook events carry: `rejected` (status, wait_seconds, channel), `ip_blocked` (channel), `send_failed` (channel, fallback, error), `sent` (channel, fallback). A held lock answers COOLDOWN with `hooks.lock_wait_seconds` and no event (as the portal did).
- Decision (matrix row "Send fails ... error logged on the provider's logger" vs. "no behaviour change"): the portal's failure line must stay on `signup.steps` (Sentry alerts on it; `signup.verification` is Sentry-ignored, and `test_failed_code_send_reaches_sentry_without_personal_data` checks it). So `SendHooks` has `send_failed_logger` (None = the provider's logger, the core default the matrix names) and `send_failed_message`; the portal hooks set `signup.steps` and `Signup code send failed: ...`. Admin entry 4 should set `send_failed_logger` to a logger that reaches Sentry, because the provider's logger is the one kept out of Sentry.
- Portal: `signup/verification.py` re-exports core and subclasses the fake (`logger_name='signup.verification'`, `cache_prefix='signup:fake-otp:'`, `_now()` routed to the module `_now`). `signup/twilio_verify.py` re-exports core's constants/helpers, keeps `TWILIO_PROVIDER`, `logger`, `FETCH_ERRORS` (re-exported from `mytp_core.verification`) and a module-level `post_verify` wrapper; its subclass's `_post_verify` calls that wrapper. `signup/steps.py` `send_code` keeps its signature, builds `_SendHooks` (limits + eventlog, login block record) and calls core; `SendOutcome`/statuses are core's, re-exported from `signup.steps`; `log_ip_blocked` unchanged; `_ip_blocked` became `_log_code_send_blocked` (the outcome is now built by core). `signup.limits` unchanged (its own `SendCheck` has the same fields).
- Vendored with `MYTP_CORE_REPO=../mytp-core scripts/vendor-mytp-core.sh v0.4.0` (the tag is not on GitHub yet). After the user pushes the tag, re-running the script against GitHub must give the same wheel (SOURCE_DATE_EPOCH from the tag's commit). If the tag is re-made, re-vendor.
- Verification: core 382 passed on SQLite and on a local PostgreSQL 17, also from a clean clone of `v0.4.0` (wheel builds); portal full suite on the vendored wheel 1578 passed, 3 failed (the 3 known PostgreSQL-only tests), no file under any `tests/` changed; `manage.py check` clean, `makemigrations --check` no changes.
- The shared `../.venv` now has the 0.4.0 wheel installed (mytp-admin pins 0.3.0; 0.4.0 only adds modules).

## Plan Change Log

## Review Triage Log

### Pass 1 (quick, 2026-11-02): high 0, medium 0, low 5, false 0, maybe-false 0

| # | Finding | Verdict | Route | Evidence / action |
|---|---------|---------|-------|-------------------|
| 1 | Portal checks E001 (`signup/checks.py:14`) and E006 (`mytp_publicsite/checks.py:137`) compare `VERIFICATION_PROVIDER` by string, so the core paths the README now offers escape them | low | defer | Real but unused today; the fix needs new tests, excluded by this story's "no test changes". Logged in deferred-work.md for entry 4. |
| 2 | `signup/twilio_verify.py` docstring and `FETCH_ERRORS` re-export named `accounts.http.FETCH_ERRORS`, not the tuple the core provider catches | low | patch | Fixed: re-exports `mytp_core.verification.FETCH_ERRORS`, docstring corrected. |
| 3 | core `test_check_failed_propagates_from_a_provider` ran no core code | low | patch | Deleted; CheckFailed is covered by the Twilio tests. |
| 4 | No core test for WhatsApp→SMS fallback followed by an SMS failure | low | patch | Added `test_sms_failing_after_the_whatsapp_fallback_is_failed` (outcome, hook order, refund, log line). |
| 5 | Shared local `../.venv` now has mytp-core 0.4.0 while mytp-admin pins 0.3.0 | low | rejected | Local environment only; 0.4.0 is additive and mytp-admin's CI installs its pinned wheel. |

After the patches: commit amended to `879bd20`, local tag `v0.4.0` moved, portal re-vendored. Core 382 passed; portal full suite 1578 passed, 3 known PostgreSQL-only failures; no file under any `tests/` changed; `makemigrations --check` clean.
