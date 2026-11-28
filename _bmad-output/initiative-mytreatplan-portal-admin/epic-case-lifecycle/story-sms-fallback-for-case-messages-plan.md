---
title: 'SMS fallback for case messages'
type: 'feature'
ticket: '3'
created: '2026-11-27'
status: 'done'
baseline_revision: '14a935002234779edf19d0d219769d5bd29f4849'
route: 'full'
route_source: 'auto'
risk: 'medium'
review: 'quick'
review_source: 'pinned'
lenses_ran: ['quick']
review_loop_iteration: 0
context:
  - '{project-root}/_bmad-output/initiative-mytreatplan-portal-admin/architecture-portal-admin/architecture-portal-admin.md'
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** When a case email from 10.2 cannot be sent (no address, or the mail server refuses or errors), the row ends `failed` and the doctor is never told (L9: SMS when the email cannot be sent).

**Approach:** When the email send ends `failed`, the same task writes an SMS message for the same work order and version and queues it after commit; its task sends one short SMS through Twilio's Messaging API to the doctor's mobile (verified at signup or on a profile change) and keeps the outcome. Development and tests use a fake sender that logs, selected by a setting, as the verification provider.

## Boundaries & Constraints

**Always:**
- SMS only after the email row is `failed`. A `sent` email never leads to an SMS.
- One SMS per (work order, version): replaying the move or rerunning either task sends nothing twice. One try, no retry (as 10.2).
- The patient by code only; text in the doctor's language through the `.po` catalogue.
- AD-23: task arguments are the row UUID; logs and stored reasons carry the row UUID, kind, outcome and Twilio's error code and HTTP status only. Never the mobile, email, body or credentials.
- Twilio credentials and the sender come from settings typed into settings.env on the server. Tests never reach Twilio (conftest blanks the settings and points the base URL at a `.invalid` host).
- A system check refuses the Twilio sender with its settings empty, as signup.E001.

**Never:**
- SMS for the order emails (9.20), assignment messages (10.10), or a doctor opt-out.
- Detecting bounces that arrive after the mail server accepted the email.
- WhatsApp, the Twilio SDK, or `requests` (urllib, as `mytp_core.twilio_verify.post_verify`).

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| Email sent | backend works | no SMS | — |
| Email refused | backend raises | email row `failed`; SMS sent to the mobile; SMS outcome `sent` | — |
| No login email | email empty | email `failed no_address`; SMS sent | — |
| No mobile | email fails, phone blank | SMS outcome `failed`, `no_mobile` | logged by UUID |
| Twilio refuses | HTTP 4xx/5xx | SMS `failed`, reason `twilio <code>` | logged by UUID, no retry |
| Twilio unreachable | one of FETCH_ERRORS | SMS `failed`, exception type | logged, no retry |
| Email task reruns | email row already `failed` | no second SMS | — |
| Spanish doctor | language `es` | SMS text in Spanish | — |

## Decisions

- Decision (user, 2026-11-27): the SMS is its own `Message` row (channel `sms`) for the same work order, version and kind; the email row keeps its own outcome.
- Decision (user, 2026-11-27): the sender is a Twilio Messaging Service, one setting `TWILIO_MESSAGING_SERVICE_SID` (sent as `MessagingServiceSid`). The user adds the alphanumeric sender "MyTreatPlan" and a fallback number in the Twilio console; countries are added there, not in code.
- Decision (user, 2026-11-27): the text is "MyTreatPlan:", the patient code, the stage and the patient page link (two segments in Spanish accepted).

</frozen-after-approval>

## Code Map

- `notifications/service.py` -- `case_message` (row + `enqueue_on_commit`), `send` (row lock, nested savepoint, `_outcome`). Extend `send` with the SMS branch; keep the savepoint pattern and the "never fails the caller" rule.
- `notifications/models.py` -- `Message`; unique (`work_order_id`, `work_order_version`, `channel`) already allows a second channel. Migration `0002` for any new choices/fields.
- `notifications/tasks.py` -- `send_message(message_id)`; reuse for the SMS row (dispatch on channel).
- `notifications/templates/notifications/email/*` -- copy the shape for `notifications/sms/<kind>.txt`.
- `mytp_core/twilio_verify.py:74-115` (`../mytp-core`) -- model for the HTTP call: Basic auth with the API key, `EXTERNAL_CHECK_TIMEOUT`, `_MAX_BODY`, `_error_code`/`_detail`. Copy the pattern into the portal; do not change mytp-core.
- `mytreatplan/settings.py:163-187` -- `VERIFICATION_PROVIDER` and `TWILIO_*`: add `SMS_SENDER` (dotted path, fake by default), `TWILIO_MESSAGING_SERVICE_SID` and `TWILIO_MESSAGING_BASE_URL` (default `https://api.twilio.com/2010-04-01`) next to them, with the same comment style; `settings.env.example`, `deploy/staging.env.example`.
- `conftest.py:49-58` -- blank the new Twilio settings and force the fake sender in tests.
- `signup/checks.py` -- `signup.E001` pattern for the new check (register in the notifications app).
- `accounts/models.py:107` -- `User.phone` (encrypted, E.164, blank allowed).
- `invoices/pdf.py` `language_of` -- language.
- `deploy/README.md` "Twilio Verify" section -- add the Messaging sender setup steps.

## Tasks & Acceptance

**Execution:**
- [x] `notifications/models.py` + migration -- channel `sms`; reasons `no_mobile`.
- [x] `notifications/sms.py` -- `FakeSmsSender` (logs row UUID only, records sends for tests) and `TwilioSmsSender` (POST `<base>/Accounts/<sid>/Messages.json`; `send(to, body)` raises `SmsFailed(detail)` on an HTTP error); `get_sender()` from the setting.
- [x] `notifications/service.py` -- on a `failed` email, get-or-create the SMS row (same work order, version, kind, recipient) and queue it; `send` handles the SMS channel.
- [x] `notifications/templates/notifications/sms/<kind>.txt` -- one short line per kind: "MyTreatPlan:", code, stage, link (no name).
- [x] `notifications/checks.py` + `apps.py` -- the settings check.
- [x] `mytreatplan/settings.py`, `settings.env.example`, `deploy/staging.env.example`, `conftest.py`, `deploy/README.md` -- settings, test defaults, setup steps.
- [x] `locale/es/LC_MESSAGES/django.po` -- Spanish SMS texts.
- [x] `notifications/tests/test_sms_fallback.py` -- one test per matrix row; the request Twilio gets (URL, form fields, auth) with the HTTP call patched; no mobile, email or name in logs.

**Acceptance Criteria:**
- Given a dev case whose doctor's email cannot be sent, when admindev sends `send_for_review`, then one SMS reaches the test mobile with the patient code only, and the SMS outcome is `sent`.
- Given the email works, when the same move is sent, then no SMS row exists.

## Design Notes

The email task, not the move, decides the fallback: the email outcome is only known after the send. Writing the SMS row inside `send`'s transaction and queueing it with `enqueue_on_commit` keeps the 10.2 guarantees (once per version, nothing sent inside a transaction that may roll back).

## Verification

**Commands:**
- `../.venv/bin/python -m pytest -q` -- expected: 0 failures besides the known WeasyPrint ones.
- `../.venv/bin/python manage.py makemigrations --check --dry-run` -- expected: no changes.

## Implementation Notes

- New `notifications/sms.py` (`FakeSmsSender` logs the row UUID only; `TwilioSmsSender` posts with urllib, `SEND_TIMEOUT` 10 s, raises `SmsFailed('twilio <code>' | exception type)`), `notifications/checks.py` (`notifications.E001`), migration `0002_sms_channel` (channel choices only), three SMS templates and one Spanish msgid. `service.send` dispatches on channel; a `failed` email calls `_queue_sms` in its own savepoint.
- Settings `SMS_SENDER` (fake by default), `TWILIO_MESSAGING_SERVICE_SID`, `TWILIO_MESSAGING_BASE_URL`; conftest forces the fake and a `.invalid` base URL; deploy/README.md "Twilio Messaging sender".
- Text: "MyTreatPlan: patient CODE, STAGE. URL".
- Verification after the patches: portal suite 4695 passed, 17 skipped, 11 failed (the known local WeasyPrint OSError in invoices/tests/test_pdf.py and test_pending_documents.py); `makemigrations --check` clean. A fresh checkout needs `compilemessages` for the Spanish tests (`.mo` not in git).

## Plan Change Log

## Review Triage Log

Pass 1 (quick lens, Opus): 6 findings — high 0, medium 1, low 4, false 1; patch 5, reject 1.

| # | Finding | Verdict | Route | Evidence / action |
|---|---------|---------|-------|-------------------|
| 1 | Fallback keyed on channel, not kind: a future 10.10 "assigned" email would get an SMS | false | reject | Every kind in `Message.KINDS` today is one of the three case kinds with a stage and an SMS template; no reachable path fails. Whether the assignment message falls back to SMS is 10.10's decision (the epic says it builds on both channels). |
| 2 | `FakeSmsSender.sent` class list grows in dev/staging workers, holding mobiles and codes | medium | patch | The fake is the default sender on servers. The fake now only logs; tests record sends through a fixture. |
| 3 | 3 s `EXTERNAL_CHECK_TIMEOUT` can store `failed` for an SMS Twilio accepted late | low | patch | Module constant `SEND_TIMEOUT = 10` s; request test asserts it. |
| 4 | `send_message` docstring says email only | low | patch | Now "email or SMS". |
| 5 | Unreachable-Twilio test misses "logged, no retry" | low | patch | Asserts the row UUID in the logs and that a rerun returns None. |
| 6 | Log privacy checked on one path only | low | patch | Refused-email and no-mobile tests assert no email, mobile or name in the logs. |

## Staging

- **Merge:** PR #158 → acb408a (after merging master: 8.6/8.13 strings in `django.po`, both kept). PR CI run 46124758572 passed; master run 46125814158 passed: tests, then dev and dev-ae deployed. Local suite: 4695 passed, 17 skipped, 11 local-only WeasyPrint failures.
- **Before the checks** the user created the Twilio Messaging Service (sender "MyTreatPlan" + fallback number) and typed `SMS_SENDER` and `TWILIO_MESSAGING_SERVICE_SID` into dev's settings.env (dev-ae stays on the fake). Test doctor: a new account (pk 30) with a second test mobile; test patient by reference only (EU-YE7YS2WX), manual work order 8e43c856.
- **Checks on dev (2026-11-28),** over SSH:
  1. Migration `notifications.0002_sms_channel` applied on dev and dev-ae; `manage.py check` clean on both. Passed.
  2. Moves run in a dev shell with the email send forced to fail (that process only; tasks run in-process after commit, real Twilio sender). First run (`request_appointment`, v4): email `failed SMTPRecipientsRefused`, SMS row `failed ModuleNotFoundError` because the typed `SMS_SENDER` had a trailing full stop (deferred: the check should catch it). After the user fixed it: `start_development`, `send_for_review` (v6): email `failed`, SMS row `sent` (accepted by Twilio). Passed.
  3. `send_for_approval` signed from admindev (v7, 200): email `sent`, no SMS row. Passed.
  4. Replay with the same idempotency key: same answer (v7), no new row. Passed.
- **Left on dev:** work order 8e43c856 cancelled (v8); the patient EU-YE7YS2WX of doctor pk 30 stays, with its 5 message rows; one SMS sent to the test mobile and one email to the test login.
