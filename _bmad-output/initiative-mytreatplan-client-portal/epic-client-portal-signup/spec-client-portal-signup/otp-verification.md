# OTP Verification

Applies to the email code (CAP-5), the mobile code (CAP-6) and the resume code (CAP-3). The login spec's second-factor code uses the same rules.

## Provider

Twilio Verify. It generates, stores and expires codes and counts attempts; our database never holds a code. One Verify service serves every purpose (signup, resume, login), so a contact's current code and its wrong-attempt count are shared across purposes.

| Contact | Channel |
|---|---|
| Email | Twilio Verify email channel (SendGrid) |
| Mobile | WhatsApp first; SMS when WhatsApp delivery fails or the user asks |

Verify does not switch channels by itself. The WhatsApp-to-SMS fallback is our logic. WhatsApp stays off (`TWILIO_WHATSAPP_ENABLED`) until Meta approves the sender; until then mobile codes go by SMS, and turning it on is a manual switch.

## Code rules

| Rule | Value |
|---|---|
| Format | 6 digits |
| Validity | 10 minutes from the first send |
| Resend within the validity | Repeats the same code; the page says "We have sent you the code again." (or "…again by SMS.") |
| Wrong attempts | 5 invalidate the code |
| Resend | Allowed 60s after the previous send |
| Sends | Max 4 per contact and per purpose (the first plus 3 resends) in a rolling hour: the next send is allowed 1h after the oldest of the last 4. Switching to SMS counts as a send |
| Languages | English and Spanish messages |

Rate limits per IP (code sends, fixed one-hour windows) apply on top, plus Twilio's SMS fraud protection (Fraud Guard).

## When a code doesn't arrive

The user can:

- resend it (within the limits above);
- for mobile, switch to SMS;
- go back and correct the email or phone, which re-runs the duplicate checks and sends a fresh code;
- see how to contact support, which shows when:
  - "no longer valid" follows 4 wrong codes on the same code within its 10 minutes (the 5th wrong attempt);
  - the hourly send limit is reached;
  - a send fails;
  - 2 checks in a row couldn't be made.

  A plain expiry ("no longer valid" with fewer wrong codes) doesn't show it.

## Prerequisites

Owned by the marketing department, which will also provide the Twilio account data. Needed before launch, not before development.

- Meta Business verification and an approved WhatsApp message template.
- EU data processing agreement signed with Twilio.
