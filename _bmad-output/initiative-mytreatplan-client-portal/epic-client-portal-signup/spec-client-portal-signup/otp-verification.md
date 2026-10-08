# OTP Verification

Applies to the email code (CAP-5), the mobile code (CAP-6) and the resume code (CAP-3).

## Provider

Twilio Verify. It generates, stores and expires codes and counts attempts; our database never holds a code.

| Contact | Channel |
|---|---|
| Email | Twilio Verify email channel (SendGrid) |
| Mobile | WhatsApp first; SMS when WhatsApp delivery fails or the user asks |

Verify does not switch channels by itself. The WhatsApp-to-SMS fallback is our logic.

## Code rules

| Rule | Value |
|---|---|
| Format | 6 digits |
| Validity | 10 minutes |
| Wrong attempts | 5 invalidate the code |
| Resend | Allowed 60s after the previous send |
| Resends | Max 3 per contact per hour |
| Languages | English and Spanish messages |

Rate limits per IP (code sends) and per contact apply on top, plus Twilio's SMS fraud protection (Fraud Guard).

## When a code doesn't arrive

The user can:

- resend it (within the limits above);
- for mobile, switch to SMS;
- go back and correct the email or phone, which re-runs the duplicate checks and sends a fresh code;
- after repeated failures, see how to contact support.

## Prerequisites

Owned by the marketing department, which will also provide the Twilio account data. Needed before launch, not before development.

- Meta Business verification and an approved WhatsApp message template.
- EU data processing agreement signed with Twilio.
