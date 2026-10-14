---
title: 'Mobile verification, resend limits and send failures'
type: 'feature'
ticket: '7'
created: '2026-10-13'
status: 'built'
baseline_revision: '33a7e70ad2600f8ded9a2fa2f62593416a577eed'
route: 'full'
route_source: 'auto'
risk: 'medium'
review: 'quick'
review_source: 'pinned'
lenses_ran: ['quick']
review_loop_iteration: 0
context: []
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** An account is created as soon as the email code is correct. The mobile is never verified (CAP-6), and codes have no resend limits, no attempt limit, no answer to a failed send, and no way out when a code doesn't arrive (CAP-11, `otp-verification.md`).

**Approach:**
- Add a mobile code step after the email code. It sends by WhatsApp first and falls back to SMS. The account is created only when both contacts are verified.
- Every code (email, mobile, resume) gets the same handling:
  - our resend limits, kept in Django's database cache;
  - a retry message when the send fails;
  - a way to switch to SMS (mobile only), to edit the contact, and to contact support.
- The fake provider emulates Twilio Verify's code rules.

## Boundaries & Constraints

**Always:**
- `PendingRegistration.phone_verified_at`. A correct mobile code sets it. Saving step 1 with another mobile clears it, as the email mark is cleared today. A resume does not copy it, since the resume code proves only the email.
- The mobile code step is skipped (`is_done`) once the mobile is verified. When no step is left, the account is created. The submit button reads "Create account" only on the last step still to do; otherwise it reads "Continue".
- Mobile send:
  - WhatsApp first. When the provider says WhatsApp can't deliver to that number, the code goes by SMS at once and the page says so.
  - "Send by SMS instead" sends by SMS.
  - The event log `code_sent` records the channel used and whether it was a fallback.
- Resend limits (ours, per contact, for every purpose):
  - a new send is allowed 60 s after the previous one;
  - at most 3 resends per contact per hour;
  - switching to SMS counts as a resend;
  - only successful sends count;
  - limits are keyed by the contact's blind index, never the plain address or number;
  - storage is `CACHES['default']` = `DatabaseCache`.
- A blocked send says when the next one is allowed. A cooldown block is shown inline. The hourly block shows the support contact and logs `rejected` with reason `OTP rate limited`.
- Entering a code step sends a code, unless one went to that contact within the last 60 s. That earlier code is still valid, so Back then Continue does not use up the limits.
- A provider failure on send:
  - the person sees "We couldn't send the code" with a retry button;
  - the registration stays pending;
  - the failure is logged as `code_send_failed` with reason `OTP send failed`, and as a `logger.error` with no personal data (Sentry alerts on spikes).
- The code rules belong to the provider:
  - 6 digits, valid for 10 minutes;
  - 5 wrong attempts invalidate the code;
  - `check` reports approved, wrong, or no valid code (expired or invalidated).

  A code that's no longer valid gets its own message ("Send a new code") and the same reason, `OTP verification failed`.
- `FakeVerificationProvider` emulates these rules through the cache:
  - a send within 10 minutes of the last one reuses the same code, as Twilio does;
  - it counts wrong attempts;
  - it still writes the code only to its log line.

  Fixed test numbers simulate "no WhatsApp" and "send fails", so staging can exercise both paths.
- Edit contact: each code page has a "Change email" / "Change mobile number" link to step 1. Saving step 1 runs the duplicate checks again, and the changed contact gets a new code.
- The support contact appears when:
  - the hourly limit is reached;
  - a code is invalidated after 5 wrong attempts;
  - a send fails.
- English and Spanish for every label and message, including the language passed to the provider.
- Decision (user, 2026-10-13): two pages, the email code and then the mobile code, each with its own resend, SMS and change links.
- Decision (user, 2026-10-13): the support contact is `sayhi@mytreatplan.com`, in the setting `SIGNUP_SUPPORT_EMAIL`.
- Decision (user, 2026-10-13): "3 resends per hour" means the first send plus 3 resends, so 4 successful sends per contact per hour.
- Decision (user, 2026-10-13): the plan is kept whole, above the size guideline.
- Decision (user, 2026-10-14): the resend limits count per contact and per code type (signup or resume), so a resume code is not held back by a signup code's cooldown. An email can get at most 4 signup codes and 4 resume codes an hour.

**Never:**
- Twilio itself (entry 11). Rate limits per IP and the CAPTCHA (entry 9). Cancel and idle timeout (entry 8).
- Storing a code, or a plain contact, in our database or cache.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| Both codes | Email code correct, then mobile code correct | Account created only after the mobile code; `phone_verified_at` and `email_verified_at` set | No error expected |
| Email only | Email code correct | Mobile code page shown, code sent by WhatsApp; no user exists | No error expected |
| No WhatsApp | Fake "no WhatsApp" number | Code sent by SMS; page says SMS; `code_sent` channel `sms`, fallback true | No error expected |
| Switch to SMS | "Send by SMS instead" after 60 s | SMS sent; counts as a resend | No error expected |
| Cooldown | Resend within 60 s | Not sent; "You can ask for a new code in N seconds" | No error expected |
| Hourly limit | Fourth resend within an hour | Not sent; support contact shown; `rejected`, `OTP rate limited` | No error expected |
| Wrong attempts | 5 wrong codes | Fifth and later: "no longer valid, send a new one" plus support; even the right code now fails | No error expected |
| Send fails | Fake "send fails" number, or provider error | "We couldn't send the code" plus retry; `code_send_failed`, `OTP send failed`; not counted | No error expected |
| Edit mobile | "Change mobile number", new number, Continue | Duplicate checks run; `phone_verified_at` cleared; code sent to the new number | No error expected |
| Back and Continue | Re-entering a code step within 60 s | No new send; the earlier code works | No error expected |
| Resume | Resumed signup | Email skipped (verified); mobile code asked before the account | No error expected |
| Resume limits | Resume code resent beyond the limits | Same cooldown and hourly rules on the resume page | No error expected |

</frozen-after-approval>

## Code Map

- `signup/verification.py`:
  - `CHANNELS` becomes email, whatsapp and sms.
  - `send` raises `ChannelUnavailable` (WhatsApp can't deliver) or `SendFailed` (provider or network error).
  - `check` returns `APPROVED` / `WRONG` / `NO_CODE`.
  - The fake keeps a per-(contact kind, contact index, purpose) record in the cache: a nonce, the time sent, and wrong attempts. The code is HMAC over the nonce. Its docstring keeps the "one place a code is written" rule.
  - The fake test numbers are module constants. The code for a mobile is shared across WhatsApp and SMS.
- New `signup/limits.py`:
  - `check_send(contact) -> (allowed, wait_seconds, hourly_blocked)` and `record_send(contact)`;
  - cache keys use `email_index` / `phone_index` from `accounts.crypto`.
- `signup/steps.py`:
  - pull the sending out of `EmailCodeStep` (`_send`, `resend`, `enter`) into a `CodeStep` base;
  - `MobileCodeStep` (slug `verify-mobile`) adds `actions = {'resend', 'sms'}`;
  - `STEPS` becomes `[Account, Legal, EmailCode, MobileCode]`;
  - `AccountStep.save` clears `phone_verified_at` on a new `phone_index`.
- `signup/forms.py`:
  - `EmailCodeForm` and `ResumeCodeForm` map the check result to the `invalid_code` / `code_expired` errors;
  - new `MobileCodeForm`;
  - `FAILURE_REASONS` adds `code_expired`, `rate_limited` and `send_failed`.
- `signup/views.py`:
  - `_render` passes `is_last` (no later step left to do);
  - `_send_resume_code` uses the same limits and failure handling;
  - `_create_account` is unchanged, because the step loop already gates it.
- `signup/models.py`: `phone_verified_at` plus migration `0009`. `copy_to_new_row` doesn't set it.
- Templates:
  - new `step_verify_mobile.html`;
  - `step_verify_email.html` and `resume.html` get the shared partial `_code_help.html` (resend, SMS, change contact, cooldown text, support note);
  - the submit label comes from `is_last`.
- `mytreatplan/settings.py`:
  - `CACHES` (`DatabaseCache`, table `django_cache`). `deploy/release.sh` already runs `createcachetable`.
  - `SIGNUP_SUPPORT_EMAIL`.
- `locale/es/LC_MESSAGES/django.po`.
- Tests:
  - the flow helpers in `test_wizard.py`, `test_event_log.py`, `test_step1.py`, `test_duplicates.py` and `test_legal_step.py` add a `_mobile_code` step;
  - new `signup/tests/test_mobile_code.py` and `test_limits.py`. Clear the cache between tests in `conftest.py`.

## Tasks & Acceptance

**Execution:**
- [x] `signup/verification.py`, `signup/limits.py` -- provider results and errors, fake emulation, limits.
- [x] `signup/models.py`, migration -- `phone_verified_at`.
- [x] `signup/steps.py`, `signup/forms.py`, `signup/views.py` -- the mobile step, shared code handling, resume sends.
- [x] Templates -- the mobile page, the help partial, labels.
- [x] `mytreatplan/settings.py`, `locale/es/LC_MESSAGES/django.po`.
- [x] Tests -- every matrix row; update the flow helpers.

**Acceptance Criteria:**
- Given staging, when a signup reaches the mobile step with a normal number, then the journal shows a WhatsApp code, and the account exists only after it is entered.
- Given staging and the fake "no WhatsApp" number, when the mobile step opens, then the code goes by SMS.
- Given CI on PostgreSQL, when the suite runs, then every test passes.

## Implementation Notes

- Limits are keyed per contact (blind index) **and purpose** (signup / resume). A shared key would make the resume code wait for the cooldown of the signup code sent to the same email moments before, and the "earlier code is still valid" skip on entering a step only holds for a code of the same purpose.
- `check_send(contact, purpose)` / `record_send(contact, purpose)` / `seconds_since_last_send(contact, purpose)`; the contact kind is told by `@`.
- The shared sending lives in `signup.steps.send_code` (used by `CodeStep` and `views._send_resume_code`). The page state (last channel, fallback, failed, one-time cooldown/hourly notice) is kept in the session under `signup_code_state`, per page; never a contact or a code.
- The send-failure error is logged on `signup.steps` ("Signup code send failed: channel=… purpose=… error=…"), not on `signup.verification`, which is kept out of Sentry.
- Fake: an approved code is used up (as Twilio); a code is valid 10 min from its first send, and a resend within that time repeats it without resetting the wrong attempts. Malformed codes count as wrong attempts. Test numbers: `FAKE_NO_WHATSAPP_NUMBER = +34699000001`, `FAKE_SEND_FAILS_NUMBER = +34699000002`. `FakeVerificationProvider.current_code()` exists for tests.
- Resume page: its existing "Use different details" link is its change-email link; the partial adds resend, notices, failure retry and support.
- The send-failure Sentry path is covered in `mytp_publicsite/tests/test_monitoring.py`.
- Review fixes: cache `MAX_ENTRIES=100000` (no culling of live rows); a `cache.add` send lock per contact and purpose (held lock = COOLDOWN); a failed send records the channel tried (retry uses it, SMS offered after a failed WhatsApp); pages say a code was sent only if one went within the code TTL, else "Ask for a new code below to continue."; the resume `sent` flag is set only on SENT or the cooldown skip; Back skips code steps already done.

## Plan Change Log

## Review Triage Log

**Pass 1 (quick lens, 2026-10-14):** high 0, medium 5, low 3, false 0, maybe-false 0. Five patches (one is the orchestrator's own finding); three findings rejected.

| # | Finding | Verdict | Route | Evidence |
|---|---------|---------|-------|----------|
| 1 | `DatabaseCache` keeps Django's default `MAX_ENTRIES=300`, so culling deletes live limit and fake-code records after a few hundred contacts in an hour | medium | patch | Real: `_cull` drops a third of the keys in key order once the table passes 300, whatever their age. Fix: `MAX_ENTRIES` 100000, and the settings test is updated. |
| 2 | Check, send and record are not atomic: a double click sends two codes inside the cooldown | medium | patch | Real: `check_send` and `record_send` sit on either side of the provider call. Fix: a per-contact-and-purpose `cache.add` lock (30 s at most), released in `finally`. A held lock answers COOLDOWN. Test added. |
| 3 | After a failed send, "Send by SMS instead" is hidden and "Try again" uses the last successful channel | medium | patch | Real: the button sits inside `{% if not code.failed %}`, and the failure stored no channel. Fix: the channel tried is stored and the retry uses it. SMS is offered when the failed channel was not SMS. Test added. |
| 4 | The intro says "We have sent a code" when the hourly limit refused the send, and the resume page sets `sent` on HOURLY | medium | patch | Real: the templates checked only `failed`, and the resume view treated anything but FAILED as sent. Fix: `sent_recently` (a successful send within the code TTL), otherwise "Ask for a new code below". The resume page sets `sent` only on SENT or the cooldown skip. Tests added. |
| 5 | Back from the mobile page goes to the verified email page, whose code is used up (orchestrator finding) | medium | patch | Real: `back_url` was `index - 1`. Fix: `_back_index` skips earlier code steps that are done, never the account or legal step. Test added. |
| 6 | A code invalidated by 5 wrong attempts within the last 60 s is not resent on Back and Continue | low | rejected | Needs 5 wrong codes then Back and Continue within a minute. The wait is under 60 s and then a resend works. A fix needs the provider's state on every entry. |
| 7 | Support shows for any `code_expired`, not only an invalidated code | low | rejected | Showing support one more time is harmless. The provider interface (Twilio's 404 too) cannot tell expired from invalidated. |
| 8 | "Try again" after a failed first send is logged as `resend: true` | low | rejected | Only the event log's resend flag is off, and only after a provider failure. A fix adds a branch for a rare case. |

## Verification

**Commands:**
- `DATABASE_URL="sqlite:///$TMPDIR/x.sqlite3" ../.venv/bin/python -m pytest -q` (with test keys) -- expected: only the 3 known PostgreSQL-only tests fail.
- `manage.py makemigrations --check`, `manage.py compilemessages` -- expected: no changes; compiles.

Done locally (2026-10-14) after the review patches, on SQLite: `pytest` 638 passed, 6 skipped (PyYAML), 3 failed only because those tests use PostgreSQL-only raw SQL. `makemigrations --check` reports no changes; `compilemessages` is up to date. Pending: CI on PostgreSQL and the staging check (a WhatsApp code in the journal for a normal number, SMS for `+34699000001`, the account created only after the mobile code).
