# Registration Failure Policies

One policy per failure scenario (CAP-11). Each row needs a test.

Wizard progress is saved server-side at each step. Once a registration is cancelled or expires, its personal data is gone from the registration store at once; the log keeps its personal values until 30 days after the attempt ends, then only the failure reason and field type/name (CAP-9, CAP-10).

| Scenario | Policy | Failure reason logged |
|---|---|---|
| User cancels (Cancel Registration) | Confirm, then clear the registration store at once and reset the form; the log keeps the reason and field name, and its personal values are anonymized 30 days after the attempt ends (CAP-8, CAP-10) | cancelled |
| Registration unfinished after 24h | Purge automatically (CAP-9) | timeout |
| Email already registered | Block; show a link to password recovery (CAP-2) | email exists |
| Mobile already registered | Block; show a link to password recovery (CAP-2) | phone exists |
| Email in a pending registration | Warn and offer to resume after a code to that email; the code also verifies it. A mobile in a pending registration is allowed; the other registration is removed when an account is created (CAP-3) | email pending |
| User closes the page mid-wizard | Within 24h, re-entering the same email starts the resume flow, which restores the saved data | abandoned |
| Idle for 30 minutes | Session ends; data stays saved; resume via code until the 24h purge | idle timeout |
| Code not received | Resend, switch to SMS (mobile), edit contact, or contact support (`otp-verification.md`) | OTP not delivered |
| Wrong code entered | 5 wrong attempts invalidate the code; the user requests a new one within resend limits | OTP verification failed |
| Resend limit reached | Resend blocked until the hourly window resets; support contact shown | OTP rate limited |
| User loses connectivity | Open form keeps its input; "connection lost" banner; submit retries when back online | |
| Twilio or our server fails while sending a code | Error message with retry; registration stays pending (24h); failure logged; ops alerted when failures spike | OTP send failed |
| CAPTCHA or rate limit blocks the attempt | Show a generic error that reveals nothing | blocked: captcha / rate limit |
| Disposable email domain | Reject with a message asking for a professional email | email disposable |
