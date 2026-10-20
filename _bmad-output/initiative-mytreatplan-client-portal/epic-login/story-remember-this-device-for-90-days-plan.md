---
title: 'Remember this device for 90 days'
type: 'feature'
ticket: '3'
created: '2026-10-20'
status: 'built'
baseline_revision: '86bc1e17a006789ecf7cdac23d8e0f208acc4655'
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

**Problem:** Since 3.2, every login needs a code. The spec (CAP-8) lets a Doctor tick "Remember me on this device/browser for 90 days" so that logins from that browser skip the code. It's the only "remember me": sessions keep their own idle limit (3.6).

**Approach:** The code page gets an unticked checkbox. When it's ticked and the right code is entered, the browser receives a mark for that account, valid 90 days from that moment. At the next login, a right password plus a valid mark for the same account completes the login at once, through 3.2's `finish_login`. Otherwise the second factor runs as now.

## Boundaries & Constraints

**Always:**
- The checkbox is on the code page, unticked by default, in EN/ES: "Remember me on this device/browser for 90 days".
- The mark is a cookie: `Secure` (when DEBUG is off), `HttpOnly`, `SameSite=Lax`, and `max_age` 90 days. Its content can't be forged or moved to another account: it's signed and bound to the account (decision 1).
- The mark is checked only after a right password and a passed CAPTCHA, and only for the account that just passed them. The decision-2 rule of 3.2 (no mobile, no login) still applies first.
- The mark lasts a fixed 90 days from the moment it was given. A later login doesn't extend it.
- Logout and the ends of a session (3.6) don't remove the mark. Each account in the same browser has its own mark.
- The skip goes through `finish_login`, the one completion. The pending login is created and ended as for a code login, so 3.4 can record both the same way.

**Never:**
- A "keep me logged in" or longer sessions.
- A device list or a revoke UI. These aren't in the spec.
- Personal values in the cookie or in logs. The cookie holds no email or phone.
- A new table or migration (decision 1).

**Decisions (2026-10-20):**
1. The mark is a signed cookie only (`django.core.signing`, with its own salt), carrying the account id, the issue time and the account's session auth hash. It's valid while the signature checks, the issue time is less than 90 days old, and the hash still matches the account. A password change or reset therefore forgets every remembered device of the account. Each account has its own cookie, named from a non-reversible digest of its id, so several accounts in one browser don't overwrite each other.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| Ticked | Right code with the box ticked | Logged in; mark set (90 days, flags as above) | — |
| Not ticked | Right code, box unticked | Logged in; no mark | — |
| Wrong code, ticked | Wrong code with the box ticked | No mark; still pending | — |
| Remembered | Same browser, right password and CAPTCHA, within 90 days | Logged in without the code page; redirected to the safe `next` or welcome | — |
| After logout | Logout, then log in again | Still no code | — |
| Expired | 90 days and a second later (clock moved) | Code required | — |
| Other browser | No mark | Code required | — |
| Other account | Mark of account A, logging in as B | Code required for B; A's mark untouched | — |
| Wrong password | Mark present, wrong password | Generic error; nothing skipped | — |
| Tampered | A modified or foreign cookie | Ignored; code required | — |
| Password changed | Password changed after the mark | Mark no longer valid; code required (decision 1) | — |
| Inactive / no mobile | Mark present | Generic error as in 3.1/3.2 | — |
| Spanish | `/es/` | Checkbox label in Spanish | — |

</frozen-after-approval>

## Code Map

- `accounts/views.py:86-93` -- `complete_login`.
- `accounts/views.py:98-131` -- `start_pending_login`, `end_pending_login`, `_account_unchanged`.
- `accounts/views.py:164-172` -- `finish_login`, the completion and the place to call for the skip.
- `accounts/views.py:~200-224` -- `login`. After `start_pending_login`, check the mark and call `finish_login`, else redirect to `login_verify`.
- `accounts/views.py:~255-295` -- `login_code`. On an approved code with the box ticked, set the cookie on the response that `finish_login` returns.
- `accounts/forms.py:98-106` -- `LoginCodeForm`. Add a `remember` BooleanField with the EN label. A remembered-device helper module or functions (sign, read, set) go in `accounts/` (e.g. `accounts/devices.py`).
- `accounts/templates/accounts/login_code.html:27-36` -- the code form; the checkbox goes under the code field.
- `mytreatplan/settings.py:377-379` -- the cookie flags pattern (`SESSION_COOKIE_SECURE` when DEBUG is off).
- Tests:
  - `accounts/tests/test_login_code.py` and `test_login.py` helpers (`_login`, `_login_with_code`);
  - the `clock` fixture (patches `signup.limits._now`, `signup.verification._now`); patch `accounts.views._now` too;
  - the browser test `accounts/tests/browser/test_login.py`.

## Tasks & Acceptance

**Execution:**
- [x] `accounts/devices.py` (new) -- issue, verify and set the signed remembered-device cookie (decision 1).
- [x] `accounts/forms.py`, `accounts/views.py` -- the checkbox, setting the mark on an approved ticked code, and the skip in `login`.
- [x] `accounts/templates/accounts/login_code.html` -- the checkbox.
- [x] `locale/es/LC_MESSAGES/django.po` -- Spanish.
- [x] `accounts/tests/test_remembered_device.py` (new) -- every matrix row; the browser test ticks the box and logs in again without a code.

**Acceptance Criteria:**
- Given the browser suite, when a Doctor ticks the box, logs out and logs in again, then no code page appears and they land on welcome.
- Given the full suite, when it runs, then the 3.1/3.2 login tests pass.

## Implementation Notes

- `accounts/devices.py`: `cookie_name` (`mtp_device_` + the first 24 hex of a SHA-256 `salted_hmac` of the pk, its own salt), `issue`, `is_valid`, `is_remembered`, `remember`. The value is `signing.dumps({'u', 't', 'h'})` with salt `accounts.devices.remembered`; the age is checked against the caller's `now` (not `signing.loads(max_age=...)`), so the tests' clock (`accounts.views._now`) moves it. Valid while `0 <= now - issued < 90 days`.
- `Secure` follows `SESSION_COOKIE_SECURE`, which the settings turn on exactly when DEBUG is off; this also lets the browser tests' `plain_http` fixture work over HTTP.
- `login`: after `start_pending_login`, a valid mark calls `finish_login` with that pending login (so the skip creates and ends it as a code login). The skip sets no new cookie, so the 90 days stay fixed.
- `login_code`: the mark is set on `finish_login`'s response only when the code was approved, the box ticked, and the login actually completed (`request.user.is_authenticated`; not when `finish_login` sent them back because the account changed).
- After the review: the skip runs only when the CAPTCHA result is `PASSED`. With `UNAVAILABLE` the login goes on to the code pages as in 3.2 (`test_unavailable_captcha_skips_nothing`).

## Plan Change Log

## Review Triage Log

**Pass 1 (quick lens, 2026-10-20):** high 0, medium 1, low 0, false 0, maybe-false 0. One patch.

| # | Finding | Verdict | Route | Evidence |
|---|---------|---------|-------|----------|
| 1 | The remembered-device skip ran on any CAPTCHA result but FAILED, so with UNAVAILABLE (siteverify down, bad secret) a marked browser logged in with the password alone, against "a right password and a passed CAPTCHA" | medium | patch | `login` now calls `devices.is_remembered` only when the result is `captcha.PASSED`; UNAVAILABLE goes to `login_verify` as in 3.2. Test `test_unavailable_captcha_skips_nothing`. |

The reviewer also confirmed:
- the cookie flags (`Secure` as `SESSION_COOKIE_SECURE`, HttpOnly, Lax, 90 days);
- the fixed validity window;
- the binding to the account id and session auth hash, with its own salt;
- the mark set only on an approved, ticked code;
- the skip through `finish_login`, after the no-mobile check.

After the patch (2026-10-20), on SQLite with Chromium (sandbox lifted): the full suite gives 1,090 passed, 3 failed (the known PostgreSQL-only tests). `makemigrations --check`: no changes.

## Verification

Done locally (2026-10-20) on SQLite with the test keys: `pytest -m "not browser"` 1,070 passed, 3 failed (the known PostgreSQL-only tests in `accounts/tests/test_crypto.py`); `pytest -m browser` outside the sandbox: 19 passed (one new). `makemigrations --check`: no changes; `compilemessages` OK; `check` clean.

**Commands:**
- The local suite command (test keys, SQLite), outside the sandbox for the browser tests. Expected: only the 3 known PostgreSQL-only tests fail.
- `manage.py makemigrations --check`, `manage.py compilemessages`. Expected: no changes, OK.
