---
title: 'Admin 2FA sign-in'
type: 'feature'
ticket: '4'
created: '2026-11-02'
status: 'built'
baseline_revision: 'adebc3aab9d3eec89265e02028fdcb7186e7db2a'
route: 'full'
route_source: 'auto'
risk: 'high'
review: 'quick'
review_source: 'pinned'
lenses_ran: ['quick']
review_loop_iteration: 0
context: [] # code repo: mytp-admin (branch from origin/master adebc3a; the local checkout may be on an old story branch). mytp-core v0.5.0 is on GitHub (vendor it). Portal reference (read-only): mytreatplan.com accounts/views.py, accounts/forms.py, accounts/login_guard.py, signup/forms.py CodeForm.
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** A2: the admin site signs staff in with a password only (3.1). Staff must pass a second factor. The portal's sign-in protections must apply too: remembered devices, lockout and single session, now in mytp-core 0.5.0 (3.2, 3.3).

**Approach:** In mytp-admin, vendor mytp-core 0.5.0 and replace Django's LoginView with a two-step sign-in, mirroring the portal's flow without importing portal code:
1. Email and password.
2. A 6-digit code sent through mytp-core's `send_code`, by email or SMS, from the admin's own Twilio Verify service.

Remembering the device skips step 2 for 7 days. The portal's lockout rule applies and alerts Sysadmins. A second sign-in ends the first session. Counters live in a database cache. The login page is branded and shows only a "contact support" message, with no signup and no reset link.

## Boundaries & Constraints

**Always:**
- **Policy:** `mytp_core.policy.ADMIN`, with a notifier: 7-day marks, 5 failures in 24 h lock for 10 min, idle 20 min, grace 1 h, channels email and SMS. Ignore its `fresh_code` flag here; that's entry 5.
- **Flow, mirroring the portal's `accounts/views.py`:**
  - The order is: lock check, then credentials, then remembered device, then code.
  - A pending login is kept in the session (user UUID and time, never the code or the contact), with a 10-minute TTL, and dropped if the account changes or locks.
  - `finish_login` is the single completion point, and `next` is safe (same host only).
  - Errors are one generic invalid-login error, plus the code errors `invalid_code`, `code_expired` and `check_failed`.
- **Codes:** `send_code(contact, PURPOSE_LOGIN, channel, hooks=AdminHooks())`. The hooks apply the portal's send limits (a 60 s cooldown and 4 per hour per contact) and set `send_failed_logger` to a logger that reaches Sentry, with no personal data. The check uses `get_provider().check(...)`. A send or check failure never signs anyone in.
- **Lockout:** both counters use `LoginFailures(ADMIN.with_notifier(alert))`, and an unknown email counts by its blind index. Lockout errors never break sign-in, as in `login_guard`. Every log line uses UUIDs and counters only, never an email or phone.
- **Single session:**
  - `staff.User` gains `SingleSessionUserMixin`, with migration `0003`.
  - `SingleSession(ADMIN, …)` uses the admin's messages, and `logout`, `healthz` and `robots_txt` pass through.
  - The `SessionLimitMiddleware` subclass goes after `MessageMiddleware`.
  - `StaffConfig.ready` connects `user_logged_in`, with a `dispatch_uid`.
- **Cache:** `CACHES` uses DatabaseCache `django_cache`, as the portal does. release.sh already runs `createcachetable`, and the README notes it for local development.
- **Settings:**
  - `VERIFICATION_PROVIDER` defaults to the core fake. Also `TWILIO_ACCOUNT_SID`, `TWILIO_API_KEY_SID`, `TWILIO_API_KEY_SECRET`, `TWILIO_VERIFY_SERVICE_SID`, `TWILIO_VERIFY_BASE_URL`, `TWILIO_WHATSAPP_ENABLED=False`, `EXTERNAL_CHECK_USER_AGENT` and `EXTERNAL_CHECK_TIMEOUT`.
  - The fake writes codes on `mytp_core.verification`, which is logged at INFO to the console and kept out of Sentry (`MYTP_SENTRY_IGNORED_LOGGERS`).
- **Admin system checks, matching the provider by class** (`issubclass` on the imported class, which settles the deferred-work item for the admin):
  - Twilio provider with a missing credential: an error.
  - The fake provider with DEBUG off: an error, unless `ALLOW_FAKE_VERIFICATION=True`.
  - admindev sets the real provider, so this only bites a misconfigured deploy.
- **Login page:** branded with the MyTreatplan logo and the "MyTreatplan Admin" title, then the message "Contact your MyTreatplan administrator." (no address). No signup, no reset link and no "forgot password".
- **Docs:** the README and `deploy/staging.env.example` get the admin Twilio runbook (the portal's "Twilio Verify" section, adapted), with names only.

- Decisions (user, 2026-11-02):
  - The code goes by email by default, with a "send it by SMS instead" link when the user has a mobile. Staff without a mobile use email only.
  - The login page says "Contact your MyTreatplan administrator." with no address.
  - Until the inbox (entry 11), a lock on a known account raises a Sentry warning ("staff account <uuid> locked"), and a lock on an unknown email is logged only.
  - The send limits are the portal's: a 60 s cooldown and 4 per hour per contact.
  - The admin uses its own Twilio API key, with the same account and SendGrid template (README runbook).

**Never:**
- No portal imports (the `test_settings.py` guard). No captcha, no event log, no WhatsApp, no per-IP limit.
- No fresh-code checks (entry 5), no inbox (entry 11), and no change to the portal or mytp-core.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| Password ok, new device | right email (any case) and password | code sent by email, with a "send it by SMS instead" link when the user has a mobile; code page with masked contact | send failure: an error page asking to try again; a Sentry log line without personal data |
| Code ok | right code | signed in, redirected to `next` or `/` | — |
| Remember | code ok with the box ticked; a new sign-in after 6 days | no code asked | — |
| Remember expired | the same after 8 days, or after a password change | code asked | — |
| Wrong password | 5 times in 24 h | locked 10 min; notifier called once; same generic error | — |
| Locked | right credentials while locked | refused with the lock message; no code sent | — |
| Wrong code | 5 wrong codes | locked; the pending login is dropped | — |
| Pending expired | code page after 10 min | back to step 1 | — |
| Unknown email | any password | the same generic error; counted by blind index | — |
| Inactive user | right credentials | the same generic error | — |
| Second sign-in | A, then B | A's next request ends with the replaced message | — |
| Idle | more than 20 min without a request | ended with the idle message | — |
| Login page | GET `/login/` | branding plus the support message; no signup or reset link | — |
| Fake in deploy | DEBUG off, fake provider | `check` error | — |

</frozen-after-approval>

## Code Map

All in `mytp-admin/`. Branch from origin/master (adebc3a), because the local checkout may still be on `story/3.6-permissions`.
- `mytp_admin/urls.py:16-18` -- the Django `LoginView` (replace it with the two step views, both `login_not_required`); `logout` :22, `healthz` :27.
- `layout/templates/layout/login.html` -- the bare AdminLTE box. Turn it into the branded page plus a code-step template.
- `mytp_admin/settings.py`:
  - MIDDLEWARE :146-159 (`LoginRequiredMiddleware` :156, `MessageMiddleware` :157); `MYTP_SENTRY_IGNORED_LOGGERS = ()` :86; LOGGING :274-285; `LOGIN_URL` :241.
  - The out-of-date comment at :198-201.
  - No `CACHES` or `TWILIO_*` yet.
- `staff/models.py:58` -- `User(AbstractBaseUser)`, UUID pk, optional `phone` :77. Add `SingleSessionUserMixin` and migration `0003`. `staff/apps.py` has no `ready()` yet.
- `scripts/vendor-mytp-core.sh v0.5.0` -- the v0.5.0 tag is on GitHub (use `MYTP_CORE_REPO=../mytp-core` if cloning needs SSH). It rewrites `requirements.txt:30`. `layout/tests/test_deploy.py:57` pins the wheel name.
- mytp-core 0.5.0 API:
  - `verification.send_code/get_provider/SendHooks` (`send_failed_logger`, `send_failed_message`), `PURPOSE_LOGIN`, `EMAIL`/`SMS`, `APPROVED`/`WRONG`/`NO_CODE`, `CheckFailed`;
  - `twilio_verify` reads `settings.TWILIO_*`, `EXTERNAL_CHECK_*` (no defaults) :79-91,133;
  - `policy.ADMIN.with_notifier` :68;
  - `devices.RememberedDevices(policy).is_remembered/remember`;
  - `lockout.LoginFailures(policy).is_locked/record_failure/reset`;
  - `sessions.SingleSession`, `SessionLimitMiddleware`, `on_user_logged_in`;
  - `models.SingleSessionUserMixin`.
- Portal references to mirror (read-only, never import):
  - `accounts/views.py` — `start_pending_login` 110-125, `_pending_login_view` 163-189, `finish_login` 200-211, `login` 218-283, the code step 323-370, safe next 77-95;
  - `accounts/forms.py` (generic error, masking);
  - `signup/forms.py` ~438-480 (code error codes);
  - `accounts/login_guard.py` 161-275 (lockout reporting);
  - `signup/limits.py:39-40` (60 s cooldown, 4 per hour);
  - the `deploy/README.md` "Twilio Verify" section (~879).
- Tests that change:
  - `staff/tests/test_sign_in.py` :24-31, :35-45 and :61. A right password now leads to the code step. Add a helper that signs in through both steps with the fake provider.
  - `test_deploy.py:57` and :177-203 (new env keys).
  - `force_login` users in `test_pages.py` and `permissions/tests` keep working.
- `conftest.py` -- the fake provider, empty `TWILIO_*`, an invalid `TWILIO_VERIFY_BASE_URL` and a locmem cache for tests (the portal's `conftest.py:52-58`).
- README :9-11, 58-70, 72-88, 228, 309-311; `settings.env.example`; `deploy/staging.env.example`.

## Tasks & Acceptance

**Execution:**
- [x] vendor mytp-core 0.5.0; settings (cache, Twilio, logging, Sentry ignore, middleware); `staff` mixin plus migration 0003, and the signal in `ready()`
- [x] `staff/signin.py` (or a views module): the pending login, the two steps, resend, remember, finish_login, the hooks, the lockout reporting and notifier; URLs
- [x] branded login and code templates; the admin system checks
- [x] tests for every matrix row (fake provider, a frozen clock through the core seams); the updated 3.1 and 3.6 tests; README and env examples

**Acceptance Criteria:**
- Given admindev with the admin Verify service set (user), when the Sysadmin signs in with password and an emailed code and ticks "remember", then they're signed in, and a new sign-in on the same browser asks no code.
- Given admindev, when a wrong password is typed 5 times, then the account is locked for 10 minutes, and a Sentry warning names the locked account's UUID.

## Verification

**Commands:**
- mytp-admin suite (CI env), `check`, `makemigrations --check` -- pass. CI on the PR (PostgreSQL) -- green.

**Manual checks (if no CLI):**
- admindev (user): the acceptance criteria above, plus the login page shows no signup or reset link.

## Implementation Notes

- Branch `story/3.4-admin-2fa` in mytp-admin (from adebc3a). The vendored wheel is byte-identical to the portal's 0.5.0 wheel.
- Modules: `staff/signin.py` (policy with the notifier, devices, `AdminLoginFailures` and `AdminSingleSession` on the module clock `_now`, `AdminHooks`, `send_login_code`, the lock and counter reports, `SessionLimitMiddleware`), `staff/forms.py` (`LoginForm`, `LoginCodeForm`, masks), `staff/views.py` (`login`, `login_code`, the pending login, `finish_login`), `staff/checks.py` (E001 Twilio credential missing, E002 fake with DEBUG off, E003 unknown provider). Templates `layout/login.html`, `layout/login_code.html` and three partials; the logo copied to `static/img/`.
- The provider checks are ordinary checks (not deploy-only), so `migrate` in the release command refuses a misconfigured deploy. CI's test job sets `ALLOW_FAKE_VERIFICATION: 'True'` (it runs `check` with DEBUG off). admindev must have the Twilio settings before this release deploys, or the deploy rolls back.
- The code page shows the outcome of the last send: sent, cooldown (a code went less than a minute ago), hourly limit, or failed (try again). A "send it by email instead" link appears on the SMS page.
- A new sign-in within 60 s of the last send meets the cooldown: the earlier code is reused if still valid, else the user waits for a resend (as the portal).

## Plan Change Log

## Review Triage Log

### Pass 1 (quick, security focus, 2026-11-02): high 1, medium 0, low 4, false 0, maybe-false 0

| # | Finding | Verdict | Route | Evidence / action |
|---|---------|---------|-------|-------------------|
| 1 | `LoginForm.refuse()` on a locked account ran `full_clean()` (via `add_error`), so `authenticate` ran and the page told a right password from a wrong one, uncounted — a password oracle during every lock | high | patch | Real: `_errors` was None when refusing. Patched as the portal's `refuse()` (`_errors = ErrorDict()`, `cleaned_data = {}`); tests prove `authenticate` is never called while locked and only the lock message shows, for right and wrong passwords. |
| 2 | Empty email/password with `novalidate` gave field errors the template never renders (blank page) | low | patch | Patched: missing values give the generic message (`incomplete`), no `authenticate`, not counted; test. |
| 3 | Empty or over-long code showed Django's required/max_length texts | low | patch | Patched: anything but 6 digits is `invalid_code`, not counted, no provider call; test. |
| 4 | `staff.E003` passed any importable non-provider class | low | patch | Patched: fires unless the class is (a subclass of) the Twilio or fake provider; tests. |
| 5 | A failed account lookup in `password_rejected()` counted nothing | low | patch | Patched: counts by the email's blind index after reporting; test. |

Verified after the patches: mytp-admin suite 321 passed (SQLite, CI env), `check` clean, `makemigrations --check` no changes.
