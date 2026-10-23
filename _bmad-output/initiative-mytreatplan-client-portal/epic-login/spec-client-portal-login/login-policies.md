# Login Policies

The detailed rules behind CAP-1 to CAP-9. Each row needs a test.

## CAPTCHA and per-IP limits (CAP-5)

| Rule | Value |
|---|---|
| CAPTCHA | Signup's (Turnstile, managed) on every login attempt and every reset request |
| CAPTCHA service unavailable | Login and reset request go through, recorded as `check_unavailable`. On that login a remembered device still asks for the code |
| Per-IP login limit | 60 password submissions per IP per fixed hour (`SIGNUP_IP_LIMITS`, tunable in `settings.env`) |
| Per-IP reset limit | 20 reset requests per IP per fixed hour (same setting) |
| Per-IP code sends | Login codes share signup's per-IP code-send limit (same setting); a send over it is refused, and its first block per IP and hour is recorded on the account |
| Reset mails per email | At most 3 per hour; beyond that nothing is sent and the page gives the same answer |
| Refusals | Generic error that reveals nothing; the first IP block in a window is recorded |

## Lockout (CAP-5)

| Rule | Value |
|---|---|
| Counts | Wrong passwords and wrong second-factor codes, apart, per account; an email with no account counts by its blind index |
| Wrong code | A code that is wrong or no longer valid (expired) counts |
| Run | Ends 24h after its last failure, or on a successful login (both counts reset) |
| Lock | 5 of either count lock for 10 minutes; the 5th wrong code locks before Twilio's own 5-attempt limit. After the lock a new run of 5 is needed |
| During a lock | Every attempt is refused with the generic error and recorded (`login_failed`, reason `blocked: locked`), not counted; it uses an IP-limit slot |
| Deactivated account | Its right password counts as a failed password, recorded `inactive_account`, so the lockout reveals nothing |
| Completed reset | Lifts the lock and clears both counts |

## Second factor (CAP-7, CAP-8)

| Rule | Value |
|---|---|
| Channels | Email (Twilio Verify's SendGrid channel) or mobile (WhatsApp when enabled, then SMS), the Doctor's choice |
| Account without a mobile | Can't log in to the portal: the generic error, no code |
| Pending login | The step between a right password and the code lasts 10 minutes, then "Your login has expired. Please log in again." |
| Codes | Signup's code rules (`otp-verification.md`), on the same Twilio Verify service |
| Remembered device | One signed cookie per account, no table and no revoke list. Valid 90 days from when it was given (a later login doesn't extend it). A password change or reset forgets every remembered device of the account. Logout and session ends leave it |

## Sessions (CAP-6, CAP-9)

| Rule | Value |
|---|---|
| Single session | Every login (portal, signup's auto-login, admin) ends the account's older session. That browser's next request goes to login with "You were logged out because your account was logged in on another device." |
| Idle | 15 minutes without a request (htmx included) ends the session, with "Your session ended after 15 minutes without activity. Please log in again." |
| Grace | The session row lives 1 hour unrenewed, so the message shows when the Doctor returns within the hour; then the daily `clearsessions` job deletes it |
| Scope | Every authenticated session, admin and staff included; anonymous sessions (the signup wizard, the pending login) are untouched |
| Reset pages | Never bounce to login; a session ended on them carries on anonymously |

## Login records (CAP-3)

| Recorded | Reason |
|---|---|
| Every completed login, with time, IP and User-Agent | — |
| Signup's auto-login | `signup` |
| The 2nd and later consecutive failures (wrong password or code) | `wrong_password`, `wrong_code`, `code_expired` |
| A deactivated account's password | `inactive_account` |
| An attempt during a lock | `blocked: locked` |
| A lock | `account_locked` |
| Reset requested, reset done, a mail over the per-email limit | `password_reset_requested`, `password_reset_done`, `rejected` |

## Page records (CAP-4)

| Recorded | Not recorded |
|---|---|
| GETs by a logged-in user of portal pages (signup and account routes under `/en/` or `/es/`) and `/admin/` | The public site, at any path |
| HTML pages with status 200 or an error status (4xx/5xx); a 404 only under a portal prefix | Redirects, other statuses, other methods |
| htmx full-page loads (boosted navigation, history restores) | htmx partial updates |
| The URL name and status; encrypted: the full path with its query string, IP and User-Agent | Static files, downloads and other non-HTML or streamed responses; request bodies |
| | Requests whose session the session limit ended |

## Password reset (CAP-2)

| Rule | Value |
|---|---|
| Entry points | Signup's recovery link and a "Forgot your password?" link on the login page |
| Link | `SITE_URL/<lang>/reset/#<uid>.<token>`: the secrets are in the fragment, so they never reach a server log, a redirect or a record. Opening it needs JavaScript |
| Validity | Django's token: 1 hour, and only until the password is set; a login in between also invalidates it |
| Mail | Plain text in the request page's language, over SMTP from noreply@mytreatplan.com, sent by a background job with no retry; the answer and its timing don't depend on the account |
| New password | Typed twice, signup's password policy and strength meter |
| After it | Every session ends, no one is logged in, remembered devices are forgotten, the lock is lifted; the login page shows that the password changed |

## Retention (CAP-3, CAP-4)

| Rule | Value |
|---|---|
| Activity records | Kinds `login`, `page` and `account`, typed unknown emails included, kept intact (IP and User-Agent too) for 5 calendar years after the attempt ends, then deleted by the daily retention job. Signup's 30-day anonymization and 2-year deletion don't apply to them |
| Erased accounts | Their activity is kept the same 5 years. Before the account is purged, one `account_purged` record (kind `account`) keeps its encrypted email, so the records can still be tied to the person |
