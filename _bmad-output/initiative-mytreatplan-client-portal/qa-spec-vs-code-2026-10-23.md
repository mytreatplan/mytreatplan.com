# QA: specs vs code (2026-10-23)

This QA run is read-only. Three reviewers compared the signup spec (`epic-client-portal-signup/spec-client-portal-signup/`, with its companions) and the login spec (`epic-login/spec-client-portal-login/`) against the code on master (`ef7eebe`). The main claims were spot-checked in the code. Duplicates across reviewers are merged.

## A. Gaps nobody decided

| # | Spec says | Code does | Where |
|---|---|---|---|
| A1 | Terms and Conditions must be accepted (signup CAP-4) | Required checkboxes exist only for published documents. With no Terms or Conditions version published, the legal step passes and the account is created without acceptance. This only happens if the documents are unpublished in the admin. | `signup/forms.py:368-384`; account creation in `signup/views.py` |
| A2 | Every successful login leaves a record (login CAP-3) | Signup's auto-login after account creation leaves no login record. **Fixed by plan-qa-fix-records-and-privacy.** | `signup/views.py:401` |
| A3 | Login CAPTCHA, second factor, lockout and records (login CAP-1/3/5/7) | The admin login (`/admin/`) has none of them. The second factor and CAPTCHA are deferred to the admin epics; the lockout and records are not recorded anywhere. | `mytreatplan/urls.py:27` |
| A4 | Page records cover the registered area (login CAP-4) | Every HTML page shown to a logged-in user is recorded, including public marketing pages. **Fixed by plan-qa-fix-records-and-privacy.** | `accounts/page_records.py:58-68` |
| A5 | Failures from the 2nd onwards are recorded (login CAP-3) | The first failure after a lock, or after the 24 h window, is not recorded. Attempts during a lock are refused before they are counted, so only the IP's first block is recorded. **Fixed by plan-qa-fix-records-and-privacy.** | `accounts/login_failures.py:108-125`, `accounts/login_guard.py:173` |
| A6 | An erased account's activity is kept as proof of behaviour (login Constraints) | After the purge, records hold only the user pk, and nothing links it to a person. **Fixed by plan-qa-fix-records-and-privacy.** | `eventlog/models.py`, `accounts/erasure.py` |
| A7 | "Code not received" is logged as `OTP not delivered` (failure policies) | The reason is never logged. **Fixed by plan-qa-fix-records-and-privacy.** | none |
| A8 | "Page closed mid-wizard" is logged as `abandoned` (failure policies) | Nothing is logged when a page closes. `abandoned` is only an attempt outcome on a resume or a superseding signup; otherwise the attempt ends `timeout`. | `signup/views.py:598`, `signup/retention.py:42` |
| A9 | Every piece of signup data is logged, and blocks log their reason (CAP-10, failure policies) | An IP-limit block is logged once per IP, counter and hour. | `signup/steps.py:304-311`, `signup/limits.py:231` |
| A10 | Resend gives a new code; the UI says "We have sent you a new code" | A resend within 10 minutes repeats the same code, and its validity counts from the first send. | `signup/steps.py:414,468` |
| A11 | Support contact is shown after repeated failures (otp-verification) | It is shown at once on a plain 10-minute expiry, and never after repeated checks that can't be made. | `signup/steps.py:367-380` |
| A12 | "Blocked until the hourly window resets" (failure policies) | The window is rolling: the next send is allowed 1 h after the oldest of the last 4 sends. | `signup/limits.py:70-85` |
| A13 | The reset request needs the CAPTCHA (login CAP-5) | When the CAPTCHA can't be checked, the reset request goes through (only login's equivalent was decided, 3.1). | `accounts/password_reset.py:307-309` |
| A14 | A deactivated account gets the same message (login CAP-1) | Its right password is also counted and recorded as `wrong_password`, and can lock it. **Fixed by plan-qa-fix-records-and-privacy.** | `accounts/login_guard.py:160-166` |
| A15 | The reset link works once (login CAP-2) | It works until the password is set, and any login in between invalidates it: Django's token includes `last_login`. | `accounts/password_reset.py` |
| A16 | No personal data in Sentry | Emails and IPs are redacted from free text; phone numbers are not. **Fixed by plan-qa-fix-records-and-privacy.** | `mytreatplan/sentry.py:42-46` |
| A17 | How-found "e-mail"; no field lengths specified | The label reads "E-mail"; lengths are 254/150/32/200; "Others" stays visible without JavaScript. | `accounts/models.py:29`, `signup/forms.py`, `step_account.html:39` |

## B. Decided in story plans, spec text out of date

**Signup spec:**
- **Step 1:** email first, with a live check when the field is left.
- **Wizard:** 4 pages (account, legal, email code, mobile code); welcome isn't a step.
- **External checks:** Turnstile managed mode shows a visible widget. The CAPTCHA, breached-password and disposable-email checks let the request through when the service is down.
- **Code sends:** 4 per contact and per purpose. One Twilio Verify service, so codes and the 5-wrong count are shared across purposes.
- **WhatsApp:** off until Meta approves the sender; the switch to SMS is manual.
- **Event log:** CAP-10 excludes passwords, codes and the Turnstile token; every event also stores IP and User-Agent.
- **Duplicates:** rechecked at account creation; a completed signup clears other pending registrations with the same email.
- **Legal step:** skippable once the current versions are accepted; a new version published mid-step asks again.
- **Purge:** runs hourly (up to about 25 h); a resume keeps the first `created_at`.
- **Account erasure:** in the admin, with a retention period. Not in the spec.
- **CAP-7:** login now needs a second factor.

**Login spec:**
- **CAPTCHA:**
  - when it is unavailable, the login goes through;
  - a remembered device then still asks for the code.
- **Per-IP limits:**
  - 60 logins and 20 reset requests per hour, in `SIGNUP_IP_LIMITS`;
  - 3 reset mails per email per hour, with no "refused" message.
- **Lockout:**
  - a run of failures lasts 24 h;
  - unknown emails lock too;
  - an expired code counts as a failure;
  - the 5th wrong code locks before Twilio's own 5-attempt limit;
  - a reset clears the lock.
- **Second factor:**
  - an account without a mobile is refused;
  - the pending login lasts 10 minutes;
  - a password change forgets remembered devices (one cookie per account, no revoke list).
- **Sessions:**
  - a 1 h grace after the 15 minutes idle;
  - specific messages for each kind of end;
  - admin sessions follow the same rules.
- **Page records:** include admin pages, error pages, full query strings and htmx boosted loads; not downloads or redirects.
- **Password reset:**
  - the link carries its secrets in the fragment, which needs JavaScript;
  - a "Forgot your password?" link on the login page;
  - the reset mail is sent in the background, with no retry.
- **Email:**
  - reset mail goes through the company's mail service;
  - login codes go through Twilio Verify's SendGrid channel.
- **Retention:** activity records (including typed unknown emails) are kept intact for 5 years. The encryption policy's Logs row still says 30 days.
- **Stale wording:** "Today the job anonymizes everything", "today the console backend".

## C. Operations and production

- **C1:** nightly backups keep signup and log data up to 30 days after it is purged.
- **C2:** Nginx and journald access logs keep client IPs under OS defaults, with no retention set.
- **C3:** disk encryption is unconfirmed (accepted for staging; required for production).
- **C4:** the KMS master key can't be rotated (deferred). Rotating the blind-index key causes a lookup outage until `reencrypt_fields` finishes.
- **C5:** the Sentry "failed sends" alert, Twilio Fraud Guard and the WhatsApp flag are manual setup, to repeat in production.

## Plan of action (user: "go ahead", 2026-10-23)

1. Bug-fix story for A1, A2, A10 and A14.
2. User decisions on A3–A9, A11–A13 and A15.
3. Spec updates for group B and the decisions in step 2 (`bmad-spec`, including the encryption policy's Logs row).
4. C1–C5 on the production story 1.12.

## User decisions (2026-10-23)

- **A3:** defer the admin login's lockout and records to the admin epics, with the second factor and CAPTCHA (deferred-work entry).
- **A4:** page records cover only the portal (registered area) and `/admin/`, not the public site.
- **A5:** record every attempt refused during a lock (`blocked: locked`); otherwise keep "from the 2nd failure".
- **A6:** the lawyer says keep a minimal identity; the encrypted email is OK. The purge of an erased account keeps it, so the 5-year records can be tied to the person.
- **A7:** log `OTP not delivered` when the Doctor asks for a resend or switches to SMS.
- **A8:** update the spec: `abandoned` when a later signup supersedes the attempt, otherwise `timeout`.
- **A9:** keep one IP-block record per IP, counter and hour; update the spec.
- **A11:** support shows after 5 wrong codes, the hourly limit, a failed send, or 2 consecutive checks that couldn't be made; not after a plain expiry.
- **A12:** keep the rolling window; update the spec.
- **A13:** the reset request goes through when the CAPTCHA is unavailable, as login does; update the spec.
- **A15:** keep Django's token: valid until the password is set, and a login in between invalidates it; update the spec.
- **A16, A17:** fix them (phone numbers redacted in Sentry; the "e-mail" label; the "Others" field hidden without JavaScript).

**Fix stories:**
- **QA fix 1, records and privacy:** A2, A4, A5, A6, A7, A14, A16.
- **QA fix 2, signup flow:** A1, A10, A11, A17.

Spec-only updates (with group B): A8, A9, A12, A13, A15.

A10, A11 and A17: fixed by `plan-qa-fix-signup-flow.md` (2026-10-23). A1: fixed by the signup retrospective's S1 (`plan-fix-legal-unpublished.md`).
