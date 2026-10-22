---
title: 'Password reset'
type: 'feature'
ticket: '10'
created: '2026-10-21'
status: 'built'
baseline_revision: 'fb37e3e41c519a0363bf3e3e5f3d241f5805afe8'
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

**Problem:** A Doctor who forgets the password has no way back in (CAP-2). Signup's "Recover your password" link points at the URL name `password_reset`, which doesn't exist yet. The spec asks for:
- a request page with the CAPTCHA and per-IP limits, which gives the same answer for any email;
- an emailed link valid 1 hour and usable once;
- a new password that meets signup's policy;
- an end to every session of the Doctor, with no automatic login;
- EN/ES emails.

**Approach:** Django's password-reset flow, adapted:
- Users are found by the email blind index (`UserManager.by_email`), not `email__iexact`.
- `PASSWORD_RESET_TIMEOUT = 3600`. Django's token already becomes invalid once the password changes, which makes it single-use.
- The request page reuses 3.1's Turnstile and signup's IP limits (a new counter).
- The email goes through 3.9's mailer in the request's language.
- The new-password page reuses signup's password policy and strength meter.
- On success: `end_all_sessions(user)` (3.6), a record in the activity log (3.4), then the login page with a message. No `post_reset_login`.

## Boundaries & Constraints

**Always:**
- The request page shows the same message whether or not the email has an account. The answer and its timing don't depend on it: the mail is sent only for an active account, and otherwise nothing.
- Only active accounts get a link. Inactive and erased accounts get the same answer and no mail.
- The link is `https://<SITE_URL>/<lang>/reset/<uidb64>/<token>/`, built from `SITE_URL`, never from the request's Host header.
- The link works once and within 1 hour. A used, expired or tampered link shows one "this link is no longer valid" page, with a link to ask for a new one.
- The new password passes `AUTH_PASSWORD_VALIDATORS` (the breached-password check included), entered twice, with signup's strength meter.
- A completed reset ends every session of the account (`end_all_sessions`). It doesn't log the Doctor in, and invalidates 3.3's remembered devices, since the password fingerprint changes. Then the login page shows "Your password has been changed. Please log in."
- The pages are on `portal_base.html`, with the CAPTCHA on the request, in EN/ES. The emails are in the language of the request page.
- Records in the event log (kind `login`, kept 5 years): `password_reset_requested`, with an account or with the typed email (as 3.4's unknown emails), and `password_reset_done` with the account. Never the token or the password.
- The secret parts of the link (token, uidb64) never reach logs or Sentry. The confirm view follows Django's own redirect that hides the token from the URL.

**Never:**
- Reset by SMS or a code, or account recovery without the email.
- Changing the login flow. The only exceptions are the success message on the login page and decision 2's handling of counters.
- New dependencies.
- An HTML version of the email (decision 3).

**Decisions (2026-10-21):**
1. Limits on reset requests:
   - per IP, 20 an hour in signup's hourly window (`SIGNUP_IP_LIMITS['reset_requests']`, env `SIGNUP_IP_LIMIT_RESET_REQUESTS`, default 20);
   - per email, 3 mails an hour, counted by the email's blind index in the database cache.

   Over either limit, the request gets the same "check your email" page and no mail. The first block of an IP in a window is recorded.
2. A completed reset clears the account's 3.5 lock and both 3.4 failure counts, so the Doctor can log in at once with the new password.
3. The email is plain text only: a short message with the link and its 1-hour validity, in EN or ES.
4. (2026-10-22, at review) The emailed link carries its secrets in the URL fragment: `https://<SITE_URL>/<lang>/reset/#<uidb64>.<token>`. Browsers never send a fragment, so the uid and token never reach Nginx, Gunicorn, a redirect's `next` or a page record. The reset page's script reads the fragment, POSTs it (CSRF) to the server, and removes it from the address. The server checks it, keeps it in the session as before, and shows the new-password page. The reset pages never bounce to login: a session ended by 3.6 on these pages carries on anonymously. Without JavaScript, the page says it's needed.
5. (2026-10-22, at review) The login page links to the request page with "Forgot your password?" (EN/ES), under the password field. This is the one change to the login flow besides the message and decision 2.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| Request, account | Active account's email (any case), CAPTCHA passes | Same "check your email" page; one mail with the link, in the page's language; recorded | — |
| Request, no account | Unknown email | Same page; no mail; recorded with the typed email | — |
| Request, inactive / erased | Such an email | Same page; no mail | — |
| CAPTCHA fails | Turnstile FAIL | `BLOCKED_MESSAGE`; no mail | — |
| Limits | 21st request from an IP in the hour, or 4th mail to an email | Same "check your email" page; no mail; the IP's first block recorded | — |
| Link opened | Valid link within 1 h | The new-password form (twice, with the meter) | — |
| Weak / breached / mismatched | Each | Field errors as at signup | — |
| Reset done | Valid new password | Password changed; all sessions ended; not logged in; login page with the message; recorded | — |
| Reuse | The same link again | "No longer valid" page | — |
| Expired | Link older than 1 h (clock moved) | "No longer valid" page | — |
| Tampered | Altered token or uid | "No longer valid" page | — |
| Old session | Another browser logged in before the reset | Its next request goes to login | — |
| Remembered device | Marked browser | Needs the code again after the reset | — |
| Recovery link | Signup's "already registered" note | Its link now resolves to the request page | — |
| Mail fails | SMTP error on send | Same "check your email" page | Sentry error without values |
| Spanish | `/es/` request | Spanish pages and email | — |
| Counters | Account locked by 3.5, then a reset done | Lock and both counts cleared; the new password logs in at once (decision 2) | — |

</frozen-after-approval>

## Code Map

- `django.contrib.auth.views` `PasswordResetView`, `PasswordResetDoneView`, `PasswordResetConfirmView`, and `django.contrib.auth.forms` `PasswordResetForm`, `SetPasswordForm`:
  - subclass them in `accounts/`;
  - override `PasswordResetForm.get_users` to use `UserManager.by_email(email)` filtered on `is_active` and a usable password;
  - send through the default mailer;
  - `PASSWORD_RESET_TIMEOUT = 3600` in `mytreatplan/settings.py`.
- `accounts/urls.py` -- add `password-reset/` (`password_reset`), `password-reset/sent/`, and `reset/<uidb64>/<token>/` (`password_reset_confirm`), with plain names.
- `signup/templates/signup/_recovery_note.html:5` -- `{% url 'password_reset' as reset_url %}`; it shows once the URL exists.
- `signup/captcha.py` `verify_turnstile`, `widget_context`, and `templates/includes/_turnstile.html` -- the CAPTCHA, as in `accounts/views.py` `login`.
- `signup/limits.py` (`ip_reserve`, `first_block`, `IP_COUNTERS`, `SIGNUP_IP_LIMITS` in `mytreatplan/settings.py:104-110`) -- add a `reset_requests` counter (decision 1), plus `signup/tests/test_abuse.py`'s counter-set test (as 3.5 did).
- Per-email limit (decision 1): a cache counter by the email blind index, as `accounts/login_failures.py` does.
- `signup/forms.py:146-305` `AccountForm` -- `password1`/`password2`, the validators, and the mismatch handling. `signup/templates/signup/step_account.html:5-30` -- the strength meter (zxcvbn-ts scripts, `#password-strength`), to reuse on the new-password page.
- `accounts/sessions.py:123` `end_all_sessions`.
- `accounts/login_failures.py:152-170` `reset`, `lock`, `is_locked` (decision 2).
- `eventlog.service.record_activity`, `Attempt.LOGIN`.
- `accounts/views.py` -- the login page's messages; `INVALID_LOGIN_MESSAGE`, `BLOCKED_MESSAGE`.
- `mytreatplan/settings.py` `SITE_URL` -- the link's origin.
- Email templates: `accounts/templates/accounts/email/password_reset_subject.txt` and `password_reset.txt` (plain text only, decision 3), EN/ES via `{% translate %}` and `translation.override`.
- Tests:
  - `accounts/tests/test_login*.py` helpers;
  - the `mailoutbox` fixture (pytest-django, locmem);
  - the root conftest's `turnstile`, `external_checks` (breached passwords) and `sentry_capture`;
  - `clock` and `freezegun`-free time moves (patch the token generator's `_now`);
  - `signup/tests/test_duplicates.py` (the recovery link).

## Tasks & Acceptance

**Execution:**
- [x] `accounts/password_reset.py` (new) or `accounts/views.py`/`forms.py` -- the request, sent, confirm and complete views; the forms; the limits; the records; the end of sessions; decision 2.
- [x] `accounts/urls.py`, `mytreatplan/settings.py` (`PASSWORD_RESET_TIMEOUT`, the IP limit), `signup/limits.py`.
- [x] `accounts/templates/accounts/password_reset_*.html`, the email templates, `locale/es/LC_MESSAGES/django.po`.
- [x] `deploy/staging.env.example`, `settings.env.example` -- the new limit's env line.
- [x] `accounts/tests/test_password_reset.py` (new) -- every matrix row; one browser test: request, open the link from the outbox, set the password, log in with it.

**Acceptance Criteria:**
- Given a Doctor with a session on browser A, when they reset their password on browser B through the emailed link, then A is logged out, B isn't logged in, and the new password then logs in (with the code).
- Given the full suite, when it runs, then the 3.1–3.9 tests pass.

## Implementation Notes

- Everything is in `accounts/password_reset.py`: `RequestView`, `SentView`, `ConfirmView` (subclasses of Django's views), `ResetRequestForm` (`PasswordResetForm` with `get_users` on `by_email`, active, usable password) and `NewPasswordForm`.
- Decision 4 (after review): the mail's link is `<SITE_URL>/<lang>/reset/#<uidb64>.<token>`. `reset/` (`password_reset_confirm`, `ConfirmView` with `opening`) serves `password_reset_link.html`, whose script `static/js/password-reset-link.js` reads the fragment, clears it (`history.replaceState`) and POSTs it with CSRF. A valid value goes into the session and redirects to `password-reset/new/` (`password_reset_new`); any other value gets the "no longer valid" page. No route takes the uid or token. A `<noscript>` note says JavaScript is needed. `accounts.sessions.RESET_PAGES` (the four URL names) makes `_to_login` let an ended session carry on anonymously on them. The earlier Sentry URL redaction was removed: nothing puts the secrets in a URL any more.
- After review: the request does the same work for any email (the lookup and one `password_reset_requested` record; reason `mail_queued` for an active account). The per-email count, the token, the rendering (in `translation.override` with the captured language and SITE_URL) and the send run in the background job `mail_job`. A mail over the per-email limit is recorded there as `rejected`, field `email`, with the account. The thread closes its own database connections. Tests run the job inline.
- Decision 5: `login.html` has a "Forgot your password?" link to `password_reset` under the password field (EN/ES).
- `NewPasswordForm` names its fields `password1`/`password2`, as signup's, so `static/js/signup-account.js` drives the meter unchanged (empty `data-user-inputs`; the server's similarity check uses the account).
- Decision 1: the per-email counter keeps the times of the mails in the last hour under `password-reset:mails:<email blind index>`, under a `cache.add` mutex (as `accounts.login_failures`). Requests over either limit, an unknown email, and an inactive account all redirect to the same sent page. Counter and lookup errors are reported without values and never change the answer.
- Decision 2: `login_failures.unlock` (new) lifts the account's lock and its email blind index's lock; `reset` clears both counts.
- Records (kind `login`): `password_reset_requested` with `reason` `mail_queued`, `unknown_email` (data: the normalized email) or `inactive_account`; `rejected` field `email` (the per-email limit); `password_reset_done`; `rejected` (field `ip`, the first block); `check_unavailable` (CAPTCHA, breached-password check). A failed CAPTCHA isn't recorded (as at login).
- A completed reset also logs out the browser that made it, when logged in (`auth_logout`), before `end_all_sessions`.
- The browser test is `accounts/tests/browser/test_password_reset.py`, not in `test_password_reset.py`: it needs that package's conftest (plain HTTP, legal documents, Turnstile stub). It uses the real background thread.
- `signup/tests/test_duplicates.py` and `signup/tests/browser/test_failure_policies.py` no longer add a stand-in `password_reset` route: the recovery link now points at `/en/password-reset/`.

## Plan Change Log

## Review Triage Log

**Pass 1 (quick lens, 2026-10-21/22):** high 0, medium 3, low 0, false 0, maybe-false 0. Three patches, two after user decisions.

| # | Finding | Verdict | Route | Evidence |
|---|---------|---------|-------|----------|
| 1 | The uid and token reached the access logs (gunicorn `--access-logfile -`, nginx's default log) on the first GET of `reset/<uidb64>/<token>/`, against "never reach logs" | medium | patch (decision 4) | The link is now `/<lang>/reset/#<uidb64>.<token>`. The fragment is never sent; `static/js/password-reset-link.js` POSTs it and clears it. No route takes the uid or token, and a test checks the routes and the flow's URLs. |
| 2 | An idle-ended session (3.6) bounced the link to `/login/?next=/en/reset/<uid>/<token>/`, with the secrets in the query and the link unusable on first open | medium | patch (decision 4) | `SessionLimitMiddleware._to_login` exempts `RESET_PAGES`, so the session ends and the page carries on anonymously. With the fragment, `next` would no longer carry secrets anyway. Test: an idle-ended session opening `/en/reset/` gets the reset page. |
| 3 | The request did `reserve_mail` and `build_message` inline for active accounts only, so timing revealed real accounts | medium | patch | Both now run in the background `mail_job` with the send. The request does the lookup and one record for any email. A test asserts that neither runs in the request. |

Also from the user at review: decision 5, a "Forgot your password?" link on the login page (EN/ES), tested on `/en/login/` and `/es/login/`.

Known and accepted: the link needs JavaScript (decision 4), and a worker restart during the background job can lose a mail, with no retry (fewer than 10 resets a week; the Doctor asks again).

After the patches (2026-10-22), on SQLite with Chromium (sandbox lifted): the full suite gives 1,270 passed, 3 failed (the known PostgreSQL-only tests). `makemigrations --check`: no changes.

## Verification

**Commands:**
- The local suite command (test keys, SQLite), outside the sandbox for the browser tests. Expected: only the 3 known PostgreSQL-only tests fail.
- `manage.py makemigrations --check`, `manage.py compilemessages`. Expected: no changes, OK.

**Results (2026-10-21):** `DATABASE_URL=sqlite:///$TMPDIR/x.sqlite3 pytest` outside the sandbox: 1260 passed; only the 3 known PostgreSQL-only tests in `accounts/tests/test_crypto.py` failed (`test_password_reset.py`: 42 passed; the browser test passed). `makemigrations --check --dry-run`: no changes. `compilemessages`: OK. `check`: no issues.
