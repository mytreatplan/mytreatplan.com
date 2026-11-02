---
title: 'Remembered devices, lockout and single session in mytp-core'
type: 'refactor'
ticket: '3'
created: '2026-11-02'
status: 'built'
baseline_revision: '64783fbb853bf93f552dcf689bed77b6556bb406'
route: 'full'
route_source: 'auto'
risk: 'high'
review: 'quick'
review_source: 'pinned'
lenses_ran: ['quick']
review_loop_iteration: 0
context: [] # code repos: mytp-core (new v0.5.0, from 879bd20 = v0.4.0) and mytreatplan.com (portal); baseline_revision is the portal commit. Pattern and lessons: story-verification-provider-and-codes-in-mytp-core-plan.md (same folder).
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** A2: the admin 2FA sign-in (entry 4) needs the portal's remember-this-device, failure lockout and single session. These live in portal modules (`accounts.devices`, `accounts.login_failures`, `accounts.cache_mutex`, `accounts.sessions`).

**Approach:** Move their logic into mytp-core 0.5.0 as configurable classes with a per-site policy: remember period, channels, lockout notifier, idle limit and grace, and a fresh-code flag. No models or migrations are added. The portal vendors 0.5.0 and keeps every module name as a thin subclass or re-export, so its behaviour is exactly as before. The approach repeats 3.2's.

## Boundaries & Constraints

**Always:**
- **No behaviour change in the portal.** No portal test file changes, and the full suite equals the baseline. Keep exactly:
  - the device cookie prefix `mtp_device_`, `SIGNING_SALT` and `NAME_SALT`, so live 90-day marks stay valid;
  - the cache keys `login:failures:…`, `login:lock:…`, the `:mutex` suffix and `session-replaced:…`, so live counters survive the deploy;
  - the `accounts.sessions.SessionLimitMiddleware` path in settings;
  - the portal's translated messages and labels, which stay defined in the portal and are passed into core, because core has no locale.
- **Seams.** Core classes call overridable methods (`_now`, `_sleep`, `lock`, `_clear`, `_delete_replaced`, `is_locked`). The portal subclasses route them to the module-level names its tests patch: `accounts.login_failures._now/_sleep/lock/_clear/is_locked/_key`, `accounts.sessions._now/_delete_replaced`, and `accounts.devices.issue/is_valid/cookie_name/SIGNING_SALT`.
- **Core holds no data.** Devices are a signed cookie, and counters and locks are in the cache. The single session reads a configurable user field (default `current_session_key`). Core ships an abstract mixin with that field for the admin, and the portal's `accounts.User` doesn't use it in 3.3. The admin's primary key is a UUID: core never assumes an integer pk.
- **Core connects no signal.** The portal's `accounts.apps` hookup (`dispatch_uid accounts.one_session`) stays as it is.
- **Policy values:**
  - portal: remember 90 days, lockout 5 failures in 24 h lock for 10 min, idle 15 min, grace 1 h, channels email/mobile with WhatsApp then SMS, fresh code off, notifier a no-op (`login_guard` already logs `account_locked`);
  - admin (defined in core as a ready policy, used in entry 4): remember 7 days, the same lockout rule (epic decision 2026-10-30), channels email and SMS, fresh code on, idle limit 20 minutes and grace 1 hour (decision, user 2026-11-02);
  - the notifier is called as `(subject, counter, count)` when a lock starts.
- Decision (user, 2026-11-02): the admin's idle limit is 20 minutes and its grace period 1 hour; the portal keeps 15 minutes and 1 hour.
- **Unknown emails** are counted by email blind index on both sites, as the portal does today.
- `cache_mutex` moves to core, and `accounts.cache_mutex` re-exports it, because `signup.limits`, `mytp_publicsite.contact` and `password_reset` import it.
- **Release:** core tests on SQLite and PostgreSQL cover both policies (90-day and 7-day marks), the lockout rule (the 5th failure locks, the lock expires after 10 min, the window is 24 h, the notifier is called once per lock), the single session (a second sign-in ends the first, idle expiry, grace), and a UUID-pk test user. Version 0.5.0, README, a local tag, a clean-clone check, then vendor.

**Never:**
- No change to mytp-admin (entry 4 adds its field, its policy wiring and the Sysadmin alert).
- No fresh-code behaviour (entry 5 builds it, from the flag only).
- No new tables. No push of mytp-core before review; the user pushes.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| Remember (portal policy) | issue a mark, then check at 89 and at 91 days | valid, then invalid | a tampered or other-user cookie is invalid |
| Remember (admin policy) | the same at 6 and at 8 days | valid, then invalid | — |
| Password change | a mark issued, then the password changes | invalid | — |
| Lockout | 5 failures within 24 h | locked for 600 s; the notifier is called once | the 6th failure while locked doesn't call it again |
| Window | 4 failures, then a 5th after 24 h | not locked | — |
| Single session | sign in on A, then on B | A's next request ends with the replaced message | — |
| Idle | no request for more than the idle limit (portal 15 min, admin 20 min) | session ended with the idle message | — |
| UUID user | the test user has a UUID pk | all of the above work | — |
| Portal unchanged | the portal suite on 0.5.0 | equals the baseline; no tests/ diff | — |

</frozen-after-approval>

## Code Map

Portal (`mytreatplan.com/`):
- `accounts/devices.py` (74 lines) -- `REMEMBER_DAYS=90` :20, `SIGNING_SALT` :23, `NAME_SALT` :24, `COOKIE_PREFIX` :25, `cookie_name/issue/is_valid/is_remembered/remember` :28–74. These become a core subclass plus the same module-level names.
- `accounts/login_failures.py` (165 lines) -- window :32, threshold :35, lock :36, `MUTEX_*` :41–43, `_key` :72, `_lock_key` :76, `record_failure` lock point :112–125 (notifier hook). It uses `email_index` from `mytp_core.crypto`.
- `accounts/cache_mutex.py` (38 lines) -- moves to core and is re-exported here. Importers: `signup/limits.py:34`, `mytp_publicsite/contact.py:44`, `accounts/password_reset.py:79`.
- `accounts/sessions.py` (252 lines) -- `SessionLimitMiddleware` :187, `on_user_logged_in` :110, `end_all_sessions` :132, the replaced key :87, `IDLE_LIMIT` :50, `SESSION_GRACE` :51, `SEEN_SESSION_KEY` :49, `RESET_PAGES` :60, `_login_url` :148 (hardcoded `admin:` names, which become a policy or resolver hook), `current_session_key` uses :78/79/124/137/144, and the messages :53–56 (translated in `locale/es/…/django.po:1134,1139`).
- These stay as they are: `accounts/login_guard.py` (event log, Sentry, captcha, limits; it records `account_locked` at :230), the `accounts/views.py` login flow (:200, :262–269, :352–353), `accounts/forms.py:99`, `accounts/password_reset.py:375–390`, `accounts/apps.py:17–20`, `mytreatplan/settings.py:186` (DatabaseCache) and :369 (middleware path).
- Tests that constrain names (don't edit): `accounts/tests/conftest.py:60,67`, `test_lockout.py:298–313,307,321,436,456,476,501,524`, `test_login_records.py:405–406`, `test_sessions.py:14,171`, `test_password_reset.py:598,628`, `test_remembered_device.py:77,79,244`, `helpers.py:6`, `browser/test_login_policies.py:22`, `mytp_publicsite/tests/test_i18n.py:175`.
- `scripts/vendor-mytp-core.sh`, `requirements.txt`, `vendor/`.

mytp-core (`mytp-core/`, master 879bd20 = v0.4.0):
- New modules for devices, lockout, sessions, cache_mutex, and the policy (with `PORTAL`/`ADMIN`-style ready policies, or one dataclass plus two instances). `mytp_core/verification.py` is the 3.2 hooks precedent.
- `MytpCoreConfig` (`name='mytp_core'`) already has the model `TaskEffect`. The abstract mixin adds no migration.
- Tests need a test-app user with a UUID pk and `current_session_key` (the test app uses `run_syncdb`) and a cache in `tests/settings.py`.
- Version in `pyproject.toml` and `__init__.py`, README sections, and CI asserts that the tag equals the version.

## Tasks & Acceptance

**Execution:**
- [x] mytp-core: the modules, the policies, the abstract mixin, tests covering every matrix row except the last, the version 0.5.0, the README, and a local commit with tag `v0.5.0`
- [x] portal: vendor 0.5.0; `accounts.devices`, `login_failures`, `cache_mutex` and `sessions` become core subclasses or re-exports with the seams routed
- [x] the portal full suite, with no test file changed

**Acceptance Criteria:**
- Given the vendored 0.5.0, when the portal suite runs, then it equals the baseline, and `git diff --stat <baseline> -- '*tests/*'` is empty.
- Given dev after deploy (user), when signing in with "remember this device" set before the deploy, then no code is asked; a second browser's sign-in ends the first session.

## Verification

**Commands:**
- mytp-core: `.venv/bin/python -m pytest -q` (SQLite) and with `MYTP_CORE_TEST_DB=postgres` -- pass. A clean clone of the tag builds.
- portal: the suite command, `check`, `makemigrations --check` -- the same as the baseline.

**Manual checks (if no CLI):**
- dev (user): a remembered device still skips the code after the deploy; a second sign-in replaces the first.

## Implementation Notes

- mytp-core: commit `20d4a67` (amended after review) on its branch `story/3.3-sessions`, tagged `v0.5.0` locally (not pushed). New modules: `mytp_core/policy.py` (`SitePolicy` frozen dataclass, `remember_seconds`, `with_notifier(fn)`; ready `PORTAL` and `ADMIN`; channels as `{EMAIL: (EMAIL,), MOBILE: (WHATSAPP, SMS)}` / `{EMAIL: (EMAIL,), MOBILE: (SMS,)}`), `mytp_core/devices.py` (`RememberedDevices(policy)`; class attributes `signing_salt`, `name_salt`, `cookie_prefix`, core defaults `mytp_core.devices.*` and `mytp_device_`), `mytp_core/lockout.py` (`LoginFailures(policy)`, `Failure`, `PASSWORD`/`CODE`/`COUNTERS`; key prefixes default to the portal's `login:failures` / `login:lock`), `mytp_core/cache_mutex.py` (moved unchanged), `mytp_core/sessions.py` (`SingleSession(policy, replaced_message, idle_message, pass_through_url_names, session_key_field)` with `on_user_logged_in`, `end_all_sessions`, `process`, and seam `login_url(request)` defaulting to `settings.LOGIN_URL`; `SessionLimitMiddleware` with a `single_session` class attribute, TypeError without one), and `SingleSessionUserMixin` (abstract, `current_session_key` CharField(40), null, blank, not editable) in `mytp_core/models.py`; `makemigrations mytp_core --check` reports no changes.
- Notifier rule (after review): when the policy has a notifier and the lock was set, `record_failure` claims a per-subject mark `login:lock:notified:<subject>` with an atomic `cache.add` (timeout `lock_seconds`, shared by PASSWORD and CODE) inside the counter's mutex, and calls `notifier(subject, counter, count)` only if the add succeeded, after the mutex is released. So one lock gives one call, even when both counters reach the threshold at once, and a slow notifier holds up no other failure. A failing add or notifier comes back in `Failure.errors` as `notify`. With no notifier (the portal) no mark is written, so the portal's cache calls are exactly as before. `SitePolicy.channels` is `field(hash=False)`, so `hash(PORTAL)` / `hash(ADMIN)` work.
- Core tests: `tests/test_devices.py`, `tests/test_lockout.py`, `tests/test_sessions.py` (both policies on every row; middleware set per test from `SESSION_MIDDLEWARE` so the older tests run without it). `tests/settings.py` gains `django.contrib.sessions` and `django.contrib.messages`, `AUTH_USER_MODEL = 'testapp.StaffUser'` (UUID pk, the core mixin), `LOGIN_URL` and `SESSION_MIDDLEWARE`; `tests/urls.py` gains login, page and a pass-through `password_reset` page; `tests/testapp/sessions.py` holds the test middleware subclass.
- Portal: `accounts/cache_mutex.py` is a re-export. `accounts/devices.py`, `accounts/login_failures.py` and `accounts/sessions.py` each define a core subclass whose seam methods call this module's functions (and `SIGNING_SALT`/`NAME_SALT` via properties), while the module's own functions call the core base method explicitly on the shared instance (`RememberedDevices.issue(_devices, ...)`, `LoginFailures.lock(_failures, ...)`, `core.SingleSession._delete_replaced(_sessions, ...)`), so a test's module patch applies and nothing recurses. Constants (`REMEMBER_DAYS`, `LOCK_THRESHOLD`, `LOCK_SECONDS`, `FAILURE_WINDOW_SECONDS`, `IDLE_LIMIT`, `SESSION_GRACE`) come from `PORTAL`; messages, `RESET_PAGES`, `_login_url` (the `admin:` names) and the mutex constants stay in the portal. `accounts.apps` untouched.
- Vendored with `MYTP_CORE_REPO=../mytp-core scripts/vendor-mytp-core.sh v0.5.0` (wheel sha256 `27b443ac…cd991a7`). After the user pushes the tag, re-running the script against GitHub must give the same wheel; if the tag is re-made, re-vendor. The shared `../.venv` now has the 0.5.0 wheel (mytp-admin pins 0.3.0; 0.5.0 only adds modules).
- Verification: core 434 passed on SQLite and on a local PostgreSQL 17, also from a clean clone of `v0.5.0` (wheel builds); portal full suite on the vendored wheel 1612 passed, 3 failed (the 3 known PostgreSQL-only tests), equal to the baseline taken before the change (1612 / 3); the same suite on a local PostgreSQL 17: 1615 passed; `git diff --stat 64783fb -- '*tests/*'` empty; `manage.py check` clean; `makemigrations --check` no changes.

## Plan Change Log

## Review Triage Log

### Pass 1 (quick, 2026-11-02): high 0, medium 1, low 2, false 0, maybe-false 0

| # | Finding | Verdict | Route | Evidence / action |
|---|---------|---------|-------|-------------------|
| 1 | core `LoginFailures.record_failure` called the lock notifier inside the counter's `cache_mutex` block: a slow notifier (admin's Sysadmin alert) makes concurrent failures hit CounterBusy, and past `mutex_seconds` the `finally` deletes another request's mutex | medium | patch | Real for any policy with a notifier (the admin's); the portal has none. Patched: decided inside, called after the block; test that it runs with the mutex released. |
| 2 | "Notified once per lock" was not atomic across the PASSWORD and CODE counters (separate mutexes, shared lock key) | low | patch | Patched: atomic `cache.add` of `login:lock:notified:<subject>` with the lock's timeout gates the notifier; the pre-lock read is gone; portal (no notifier) makes the same cache calls as before. Tests added. |
| 3 | `hash(PORTAL)` raised TypeError (`channels` MappingProxyType in a frozen dataclass) | low | patch | Patched: `channels = field(hash=False)`; hash test for both policies. |

After the patches: core commit amended to `20d4a67`, local tag `v0.5.0` moved, portal re-vendored. Core 438 passed (SQLite); portal full suite 1612 passed, 3 known PostgreSQL-only failures; no file under any `tests/` changed; `makemigrations --check` clean.
