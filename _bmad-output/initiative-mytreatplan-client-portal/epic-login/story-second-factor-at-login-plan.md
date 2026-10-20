---
title: 'Second factor at login'
type: 'feature'
ticket: '2'
created: '2026-10-19'
status: 'done'
baseline_revision: 'bd6e1c87a2841a33305697782880ac1c1c8acaa9'
route: 'full'
route_source: 'auto'
risk: 'high'
review: 'quick'
review_source: 'pinned'
lenses_ran: ['quick']
review_loop_iteration: 0
context: []
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** After 3.1, a correct email and password log a Doctor straight in. The spec (CAP-7) asks for a second factor: a code sent through Twilio Verify to the account's email or mobile, whichever the Doctor chooses, so either one can be used if the other isn't available.

**Approach:** A correct password no longer logs in. It opens a short-lived pending login in the session, then two pages:
1. a choice of email or mobile;
2. the code page.

The code is sent through signup's verification provider and its sending helper (WhatsApp first, then SMS; the per-contact cooldown and hourly limits). Only an approved code completes the login, through 3.1's `complete_login`. The pages are in EN/ES. Development and tests use the fake provider.

## Boundaries & Constraints

**Always:**
- A pending login grants nothing. It holds the user pk, the user's session auth hash, its start time and the safe `next`, under its own session key. It lives 10 minutes (`CODE_TTL_SECONDS`). At completion the user must still be active and the hash must still match; the key is removed before `auth_login`.
- Codes use a new purpose `PURPOSE_LOGIN = 'login'`, so login gets its own per-contact cooldown and hourly counters (`signup/limits.py` keys by purpose).
- Sending reuses `signup.steps.send_code` and the WhatsApp→SMS fallback, with its outcomes (sent, cooldown, hourly limit, failed, blocked). The code page reuses `signup/_code_help.html`: resend, "Send by SMS instead", the WhatsApp hint, the support contact. A code check that can't be made shows `check_failed`, as at signup.
- A wrong code shows "This code is not correct…"; after 5 it shows "no longer valid" and offers a new code within the limits. The account lockout is entry 5, not here.
- Pages and messages never reveal more than the password step already did: no account data before the password.
- One place completes a login after the code: the seam where 3.3 skips the code for a remembered device. One place handles a wrong code: the hook 3.4's counters use.
- Every new text is in EN/ES.

**Never:**
- The remembered device, login records, the lockout, IP limits for login, or a session policy (entries 3–6).
- Changing signup's flow or messages. Signup's own auto-login after account creation stays as it is, since both contacts were just verified.
- Real Twilio in tests.
- The admin site's login (decision 4).

**Decisions (2026-10-19):**
1. One Twilio Verify service for every purpose. A login code and a pending signup or resume code to the same contact share Twilio's pending code and its 5-attempt count; this is accepted and documented in `signup/twilio_verify.py`'s docstring and the README's "Twilio Verify" section.
2. An account without a mobile can't log in to the portal. After a right password it gets the same generic login error (`INVALID_LOGIN_MESSAGE`), so the page doesn't say why; no code is sent and no pending login is kept.
3. The choice and code pages show the contacts masked: the email as its first character, `•••`, then `@` and the domain (`a•••@example.com`); the mobile as its country code and last 3 digits (`+34 ••• ••• 374`).
4. The admin site (`/admin/`, Django's own login) stays out of scope. It will have its own epics (user, 2026-10-19), and a deferred-work entry records its missing second factor and CAPTCHA.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| Password ok | Right credentials, CAPTCHA passes | Not logged in; choice page | — |
| Choose email | Email chosen | Code sent to the email; code page | — |
| Choose mobile | Mobile chosen | Code by WhatsApp, or SMS when WhatsApp is off or unavailable; the page says which | — |
| Right code | The current code | Logged in; redirected to the safe `next` or welcome; pending key gone | — |
| Wrong code | A wrong code | "not correct"; still pending; not logged in | — |
| 5 wrong codes | 5 wrong | "no longer valid"; a new code (after the cooldown) works | — |
| Resend / SMS | Resend after 60 s; "Send by SMS instead" | New send within the limits; cooldown and hourly notices as at signup | — |
| Send fails | Mobile `+34699000002` | Retry and support shown; still pending | Sentry error, no contact |
| Check unavailable | Provider raises CheckFailed | `check_failed` message; still pending | Sentry error, no contact |
| Pending expired | Code page after 10 minutes | Back to login with a message; nothing sent | — |
| No pending | GET the choice or code page directly | Redirect to login | — |
| Account changed | Deactivated or password changed while pending | Code refused; back to login | — |
| Spanish | `/es/` | Spanish pages, codes in Spanish (`language`) | — |
| No mobile | Right password, account with a blank phone | The generic login error; not logged in; no code sent (decision 2) | — |
| Masked | Choice and code pages | Email and mobile masked (decision 3); never shown in full | — |

</frozen-after-approval>

## Code Map

- `accounts/views.py:34-84` -- `requested_next`, `redirect_after_login`, `complete_login` (:50), `login` (:57; :79 calls `complete_login` after the password). Replace it with: start the pending login, then redirect to the choice page. New views: choice, code (verify plus the resend and SMS actions), and the completion through `complete_login`.
- `accounts/forms.py` -- `LoginForm`. Add a channel-choice form and a `LoginCodeForm` on the pattern of `signup/forms.py:467-471` (`ResumeCodeForm` overrides only `purpose`), over `CodeForm` (:401-448).
- `accounts/urls.py` -- add `login/verify/` (choice) and `login/code/` (or similar), with plain names.
- `signup/verification.py:43-45` -- add `PURPOSE_LOGIN`. The fake keys its state and code by purpose (:180).
- `signup/steps.py:219-329` -- `send_code(request, attempt, page, contact, purpose, channel, resend=False)`, `SendOutcome`, `code_state`, `CODE_STATE_SESSION_KEY`. Reuse it with `page='login'` and `attempt=None` (no event-log attempt until 3.4). The Sentry text "Signup code send failed" is kept, so the alert rule covers login sends too.
- `signup/views.py:451-479` `_code_help`, `signup/templates/signup/_code_help.html` -- the context the help partial needs. Make that builder reusable (move it to `signup/steps.py` or a small shared helper), with `support_email = settings.SIGNUP_SUPPORT_EMAIL`.
- `signup/templates/signup/step_verify_email.html`, `step_verify_mobile.html` -- texts and data attributes to mirror (`[data-sms-fallback]`, `[data-send-failed]`). The login pages extend `portal_base.html`, not the wizard.
- `accounts/models.py:71-80` -- `phone` may be blank (users created in the admin or by `createsuperuser`).
- Twilio: one Verify service serves every purpose (`signup/twilio_verify.py:18-20`). A login code and a pending signup or resume code to the same contact share Twilio's pending verification and its 5-attempt count. See decision 1.
- Tests:
  - `accounts/tests/test_login.py`: every test that logs in now needs the code step; add a `_login_with_code` helper.
  - `accounts/tests/browser/test_login.py`.
  - `signup/tests/helpers.py` `_current_code(channel, contact, purpose)` and `_wrong`; `signup/tests/conftest.py` `clock` and `capture_codes`.
  - The root `conftest.py` forces the fake provider and `TWILIO_WHATSAPP_ENABLED=False`; set it to True for a WhatsApp case.

## Tasks & Acceptance

**Execution:**
- [x] `signup/verification.py` -- `PURPOSE_LOGIN`.
- [x] `signup/steps.py` / `signup/views.py` -- make the code-help context builder reusable; signup unchanged.
- [x] `accounts/views.py`, `accounts/forms.py`, `accounts/urls.py` -- the pending login, the choice and code views and forms, and completion through `complete_login`.
- [x] `accounts/templates/accounts/login_choose.html`, `login_code.html` (new) -- on `portal_base.html`, with `_code_help.html`.
- [x] `locale/es/LC_MESSAGES/django.po` -- Spanish.
- [x] `signup/twilio_verify.py` docstring, `deploy/README.md` "Twilio Verify" -- the shared pending code across purposes (decision 1).
- [x] `_bmad-output/initiative-mytreatplan-client-portal/deferred-work.md` -- append an entry: the admin site's login has no second factor and no CAPTCHA, for the admin epics (decision 4).
- [x] `accounts/tests/test_login_code.py` (new), and updates to `accounts/tests/test_login.py` and the browser test -- every matrix row.

**Acceptance Criteria:**
- Given the browser suite, when a Doctor logs in with a password and then the code to the chosen contact, then they land on welcome. The login browser test covers it.
- Given the full suite, when it runs, then signup's tests pass unchanged.

## Implementation Notes

- The code-help builder moved to `signup.steps.code_help_context`; `signup.views._code_help` is now an alias of it, so signup's pages and tests are unchanged.
- `accounts.views`: `start_pending_login` (session key `login_pending`: user pk, session auth hash, start time, safe `next`, the contact chosen), `login_verify` (choice), `login_code` (check, `resend`, `sms`), `finish_login` (the one completion seam for 3.3: re-checks the account, removes the key, then `complete_login`), `login_code_rejected` (the one wrong-code hook for 3.4; logs only the error codes). `complete_login` takes an optional, re-checked `next_url`.
- The pending login also ends when the account loses its mobile (decision 2). Expired and changed both show one message, "Your login has expired. Please log in again.", which says nothing of why.
- Choosing the same contact again within the cooldown sends nothing (the code is still valid), as signup's code steps do on entry. A new pending login (a new password) always tries to send, so a re-login within 60 s of a used code shows the cooldown notice.
- No mobile: `LoginForm.refuse_login()` gives the valid form the generic `invalid_login` error; the page is byte-identical to a wrong password's (tested).
- The code page's code state is kept under the page name `login` in signup's `signup_code_state`; it is cleared at a new pending login, at a new choice and at completion.

## Plan Change Log

## Review Triage Log

**Pass 1 (quick lens, 2026-10-19):** high 0, medium 3, low 3, false 0, maybe-false 0. Five patches; one rejected.

| # | Finding | Verdict | Route | Evidence |
|---|---------|---------|-------|----------|
| 1 | A mobile or email changed while the login is pending isn't detected; the code is checked and resent against the new contact | medium | patch | `_account_unchanged` compared only `is_active`, the auth hash and `bool(phone)`. The pending login now stores both blind indexes and refuses a change in either. Tests cover a changed mobile and a changed email. |
| 2 | An earlier pending login survives a later wrong password, failed CAPTCHA, no-mobile refusal or a GET of the login page | medium | patch | `login` now ends any pending login on every GET and POST before the outcome. `test_login_page_ends_an_earlier_pending_login` covers all four cases. |
| 3 | Non-code submissions (`action=sms` on the email page, an empty submit) and `check_failed` reach the rejected-code hook that 3.4 will count | medium | patch | The hook now runs only for `invalid_code` and `code_expired`. An unknown or inapplicable action redirects back to the code page. The two hook tests are updated. |
| 4 | After a re-login within the 60 s cooldown, the page says a code was sent when no valid code exists | low | patch | The pending login has a `sent` flag set only by a send that succeeded during it. Otherwise the page shows the cooldown notice and "Ask for a new code below". `test_relogin_within_the_cooldown_shows_the_wait_not_sent` covers it. |
| 5 | The mobile's failed-send intro reuses the email msgid | low | patch | Now `blocktranslate with phone=contact`. It reuses signup's existing msgid and its Spanish entry, so no new msgid; the `.po` references are updated. |
| 6 | `mask_email` would show an address with no `@` in full | low | rejected | Account emails are validated at signup and in the admin, so such an address can't reach the helper. A guard would add a branch for a state not shown to be reachable. |

After the patches (2026-10-19), on SQLite with Chromium (sandbox lifted): the full suite gives 1,066 passed, 3 failed (the known PostgreSQL-only tests), no errors. `makemigrations --check`: no changes. `compilemessages`: OK.

## Verification

Done locally (2026-10-19) on SQLite with the test keys: `pytest -m "not browser"` 1,041 passed, 3 failed (the known PostgreSQL-only tests in `accounts/tests/test_crypto.py`); signup's tests unchanged and passing. `pytest -m browser` outside the sandbox: 18 passed. `makemigrations --check`: no changes; `compilemessages` OK; `check` clean.

**Commands:**
- The local suite command (test keys, SQLite), outside the sandbox for the browser tests. Expected: only the 3 known PostgreSQL-only tests fail.
- `manage.py makemigrations --check`, `manage.py compilemessages`. Expected: no changes, OK.

CI on PostgreSQL 17 (PR #29): 1069 passed. Merged as `df11b94`. On staging, with real Twilio codes (mobile by SMS while WhatsApp is off), the user reported (2026-10-20) these checks passed: the choice page shows the email and mobile masked; the email code arrives, in Spanish from `/es/`, and logs in on welcome; the SMS code works; a wrong code says "not correct" and "Send a new code" works after a minute; after 10 minutes, submitting shows "Your login has expired".
