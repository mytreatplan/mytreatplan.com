# Registration Failure Policies

One policy per failure scenario (CAP-11). Each row needs a test.

Wizard progress is saved server-side at each step. Once a registration is cancelled or expires, its personal data is gone from the registration store at once; the log keeps its personal values until 30 days after the attempt ends, then only the failure reason and field type/name (CAP-9, CAP-10).

| Scenario | Policy | Failure reason logged |
|---|---|---|
| User cancels (Cancel registration) | Confirm, then clear the registration store at once and reset the form; the log keeps the reason and field name, and its personal values are anonymized 30 days after the attempt ends (CAP-8, CAP-10) | cancelled |
| Registration unfinished after 24h | Purge automatically by the hourly job, so within about 25h; a resume keeps the first start time (CAP-9) | timeout |
| Email already registered | Block; show a link to password recovery (CAP-2) | email exists |
| Mobile already registered | Block; show a link to password recovery (CAP-2) | phone exists |
| Email in a pending registration | Warn and offer to resume after a code to that email; the code also verifies it. A mobile in a pending registration is allowed; the other registration is removed when an account is created (CAP-3) | email pending |
| Attempt superseded | A resume, or a later signup with the same email, replaces the attempt | abandoned (attempt outcome) |
| User closes the page mid-wizard | Not detected. Within 24h, re-entering the same email starts the resume flow, which restores the saved data; otherwise the attempt ends at the purge | timeout (or abandoned, if superseded) |
| Idle for 30 minutes | Session ends; data stays saved; resume via code until the 24h purge. A cancel sent after the idle timeout still cancels | idle timeout |
| Code not received | Resend, switch to SMS (mobile), edit contact, or contact support (`otp-verification.md`). Logged on the resend's or the SMS switch's send; delivery itself is not tracked | OTP not delivered |
| Wrong code entered | 5 wrong attempts invalidate the code; the user requests a new one within resend limits; support contact shown when the code ends that way (`otp-verification.md`) | OTP verification failed |
| Resend limit reached | Resend blocked until 1h after the oldest of the last 4 sends (rolling window); support contact shown | OTP rate limited |
| Terms or Conditions not published | Step 2 refuses with "Signup is not available right now. Please try again later."; nothing is saved; the server log names the missing documents | rejected |
| User already logged in | The wizard sends them to the welcome screen | |
| User loses connectivity | Open form keeps its input; "connection lost" banner; submit retries when back online | |
| Twilio or our server fails while sending a code | Error message with retry; support contact shown; registration stays pending (24h); failure logged; ops alerted when failures spike | OTP send failed |
| CAPTCHA or rate limit blocks the attempt | Show a generic error that reveals nothing. An IP-limit block is logged once per IP, counter, hour and flow (signup or login) | blocked: captcha / rate limit |
| CAPTCHA, breached-password or disposable-email service unavailable | The request goes through without that check | |
| Disposable email domain | Reject with a message asking for a professional email | email disposable |
