# Registration Failure Policies

One policy per failure scenario (CAP-11). Each row needs a test.

Wizard progress is saved server-side at each step. Once a registration is cancelled or expires, its personal data is gone; the log keeps only the failure reason and field type/name (CAP-9).

| Scenario | Policy | Failure reason logged |
|---|---|---|
| User cancels (Cancel Registration) | Confirm, then delete personal data and reset the form; keep the log without personal data (CAP-8) | cancelled |
| Registration unfinished after 24h | Purge automatically (CAP-9) | timeout |
| Email already registered | Block; show a link to password recovery (CAP-2) | email exists |
| Mobile already registered | Block; show a link to password recovery (CAP-2) | phone exists |
| Email or mobile in a pending registration | Warn and offer to resume after a code sent to the matched contact; the new attempt replaces the old one (CAP-3) | email pending / phone pending |
| User closes the page mid-wizard | Within 24h, re-entering the same email or mobile starts the resume flow, which restores the saved data | abandoned |
| Idle for 30 minutes | Session ends; data stays saved; resume via code until the 24h purge | idle timeout |
| Code not received | Resend, switch to SMS (mobile), edit contact, or contact support (`otp-verification.md`) | OTP not delivered |
| Wrong code entered | 5 wrong attempts invalidate the code; the user requests a new one within resend limits | OTP verification failed |
| Resend limit reached | Resend blocked until the hourly window resets; support contact shown | OTP rate limited |
| User loses connectivity | Open form keeps its input; "connection lost" banner; submit retries when back online | |
| Twilio or our server fails while sending a code | Error message with retry; registration stays pending (24h); failure logged; ops alerted when failures spike | OTP send failed |
| CAPTCHA or rate limit blocks the attempt | Show a generic error that reveals nothing | blocked: captcha / rate limit |
| Disposable email domain | Reject with a message asking for a professional email | email disposable |
