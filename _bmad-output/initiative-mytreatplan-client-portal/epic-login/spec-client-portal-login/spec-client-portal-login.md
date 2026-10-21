---
id: SPEC-client-portal-login
companions:
  - ../../epic-client-portal-signup/spec-client-portal-signup/encryption-policy.md
sources:
  - ../epic-login.md
---

> **Canonical contract.** This SPEC and the files in `companions:` are the complete, preservation-validated contract for what to build, test, and validate. Source documents listed in frontmatter are for traceability — consult them only if you need narrative rationale or prose color this contract intentionally omits.

# Doctor login, password recovery and activity logging

## Why

Vision plus mandate. Signup (epic 2) creates verified Doctor accounts, but nobody can get back into the portal after the first session. Every later portal feature (billing, orders, patient uploads, support) needs a Doctor who can log in securely and recover a forgotten password. Logins and browsing in the registered area must be on record, for three reasons: security, support and debugging, and usage reporting. That record is personal data (IP, browser) of EU-based operations, so it falls under GDPR, and the law requires keeping it for 5 years. Signup stays on staging until login is ready, and both go live in production together.

## Capabilities

- **CAP-1**
  - **intent:** A Doctor created by signup logs in with email and password and lands on the welcome route.
  - **success:**
    - The right email (whatever its case), password and second factor (CAP-7) log the Doctor in and open the welcome screen.
    - A wrong password, an unknown email, or a deactivated or erased account gets the same message and no session.
- **CAP-2**
  - **intent:** A Doctor who forgot the password sets a new one through a link sent to the account's email.
  - **success:**
    - The signup recovery link (URL name `password_reset`) opens the request page.
    - Requesting a reset for any email gives the same answer.
    - The emailed link is valid for 1 hour and works once. It sets a new password that meets signup's password policy.
    - The reset ends all the Doctor's sessions and doesn't log them in; they then log in with the new password.
    - A used or expired link is refused.
- **CAP-3**
  - **intent:** Logins are recorded with their date and time, IP and browser information.
  - **success:**
    - Every successful login leaves one record with its time, IP and browser information, tied to the account.
    - Wrong passwords and wrong second-factor codes are counted separately, per account, and both counts reset after a successful login. The first failure leaves no record; the second and later consecutive failures each leave one.
    - Failures for an email with no account (a typo, confusion or an attack) are recorded by the same rule, with no account attached.
- **CAP-4**
  - **intent:** Every full page shown to a logged-in Doctor is recorded with the page and its date and time.
  - **success:**
    - Opening a registered-area page leaves one record of that page and time for the account.
    - Partial updates (htmx) and static files leave none.
- **CAP-5**
  - **intent:** Login and the reset request are protected against abuse.
  - **success:**
    - Every login attempt needs the CAPTCHA, as the reset request does.
    - Requests beyond signup's per-IP limits are refused with a generic error that reveals nothing.
    - 5 wrong passwords, or 5 wrong second-factor codes (each on its own count), lock the account for 10 minutes.
- **CAP-6**
  - **intent:** A logged-in Doctor logs out.
  - **success:** After logging out, the session is ended, and registered-area pages send the Doctor to login.
- **CAP-7**
  - **intent:** Login needs a second factor: a code sent through Twilio Verify to the account's email or mobile (WhatsApp first, then SMS, as at signup), whichever the Doctor chooses, so that either one can be used if the other isn't available.
  - **success:**
    - After a correct password, the Doctor chooses email or mobile and receives a code.
    - Only the right code completes the login.
    - Twilio Verify's rules apply: 6 digits, valid 10 minutes, invalid after 5 wrong attempts.
- **CAP-8**
  - **intent:** A Doctor can choose "Remember me on this device/browser for 90 days" to skip the second factor there. It's the only "remember me": there's no "keep me logged in".
  - **success:**
    - On that device and browser, logins within 90 days need no code.
    - Another device or browser, or the same one after 90 days, needs the code again.
- **CAP-9**
  - **intent:** A Doctor has one active session at a time, and an idle session ends.
  - **success:**
    - The same account can't be logged in on two devices at once: a new login ends the older session.
    - A session with no request for 15 minutes ends, and expired sessions are cleaned up.

## Constraints

- Email and phone are encrypted, so accounts are found by the email's blind index. This is the encryption policy's row "Uniqueness and lookups"; `accounts.backends.EmailBackend` already logs in this way.
- A new password meets signup's password policy (`AUTH_PASSWORD_VALIDATORS`, including the breached-password check) and is stored as an Argon2id hash.
- Second-factor codes use the Twilio Verify integration built for signup (story 2.11), and its fake provider in development and tests.
- Activity is recorded through signup's log service (`eventlog`), not a separate store.
- Activity records, IP and User-Agent included, are kept intact for 5 years, a legal requirement, with no anonymization step. An erased account's activity is kept for the same 5 years as proof of behaviour, so it must outlive the account's deletion. Today the log's retention job anonymizes every attempt at 30 days and deletes it at 2 years, so it must treat activity records separately.
- Activity tracking is covered by the Terms and Conditions and the service conditions; there is no separate notice.
- The reset email is sent over SMTP through the company's own mail service, from noreply@mytreatplan.com (low volume: fewer than 10 resets a week). The final provider is reevaluated when the whole portal is finished. Today the default mail backend is the console.
- Login redirects to the welcome route created by epic 2. `LOGIN_URL` points at login once it exists.
- Deactivated and erased accounts can't log in. Erasing an account deactivates it (`accounts.erasure`).
- Login, reset and second-factor messages never reveal whether an account exists.
- Browser information is the request's User-Agent string.
- No personal data reaches Sentry, as for signup.
- The pages are in English and Spanish, under `/en/` and `/es/`, as the rest of the portal.
- Signup and login go live in production together. Both are served only while `PORTAL_ENABLED` is on.

## Non-goals

- Signup (epic 2).
- Profile, billing and onboarding content, including what the welcome screen shows.
- A separate privacy notice for the activity tracking.

## Success signal

- A Doctor created by signup logs in with email, password and a code to the email or mobile of their choice, ticks "remember this device", and next time logs in without a code. They log out, recover a forgotten password through the emailed link from signup's recovery link, and log in with the new one. Their logins and the pages they opened are on record with time, IP and browser. Signup and login are live in production together.
