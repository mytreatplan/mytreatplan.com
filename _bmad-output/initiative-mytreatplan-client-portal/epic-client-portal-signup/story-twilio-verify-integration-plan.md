---
title: 'Twilio Verify integration'
type: 'feature'
ticket: '11'
created: '2026-10-15'
status: 'done'
baseline_revision: '95d9a4cbec53ea7815688a1e281615120de583ac'
route: 'full'
route_source: 'auto'
risk: 'high'
review: 'quick'
review_source: 'pinned'
lenses_ran: ['quick']
review_loop_iteration: 0
context: []
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** Every signup code (email, mobile, resume) still goes through the fake provider, which writes it to the server log. No real person can receive a code, so signup can't launch (CAP-5, CAP-6, CAP-11).

**Approach:**
- A `TwilioVerifyProvider` behind entry 1's `VerificationProvider` interface, selected with `VERIFICATION_PROVIDER`:
  - email goes through Verify's email channel (SendGrid), mobile through WhatsApp or SMS;
  - Twilio generates, stores, expires and counts the codes (6 digits, 10 minutes, 5 wrong attempts);
  - the credentials come only from `settings.env`.
- The existing callers keep their logic: WhatsApp first with the SMS fallback, our limits, and the send-failure handling and its `logger.error` that Sentry turns into the ops alert.
- A person sets the credentials and the Sentry alert rule on staging.

## Boundaries & Constraints

**Always:**
- **HTTP client:** the Verify REST API (`https://verify.twilio.com/v2/Services/<sid>/Verifications` and `/VerificationCheck`) through stdlib urllib, as in `accounts/http.py` and `mytp_publicsite/kms.py`. No Twilio SDK and no `requests`.
  - Basic auth with an API key (`TWILIO_API_KEY_SID`/`TWILIO_API_KEY_SECRET`), plus `TWILIO_ACCOUNT_SID` and `TWILIO_VERIFY_SERVICE_SID`.
  - Timeout `EXTERNAL_CHECK_TIMEOUT`. The endpoint base is a setting, so the tests point it at `*.test.invalid`.
- **Send:** `Channel` = `email`/`whatsapp`/`sms`, `To` = the email or the E.164 number, `Locale` = `en`/`es` from `language`.
  - Twilio errors map to the interface's exceptions:
    - "channel not available for this number" (WhatsApp not reachable, a number SMS can't reach) raises `ChannelUnavailable`;
    - anything else raises `SendFailed`: HTTP or network failure, auth, Fraud Guard block (60410), too many sends (60203).
  - The message carries only the Twilio error code and HTTP status, never the contact, the code or the credentials.
- **Check:** `VerificationCheck` with `To` and `Code`.
  - `approved` returns `APPROVED`. `pending` (wrong code) returns `WRONG`. 404 (no pending verification: expired, used, or invalidated after 5 wrong attempts) and 60202 (max attempts) return `NO_CODE`.
  - A malformed code (not 6 digits) returns `WRONG` without calling Twilio.
- **Check failure (new):** a network, auth or 5xx failure raises a new `CheckFailed(VerificationError)`. `CodeForm.clean_code` catches it:
  - it shows "We couldn't check your code right now. Please try again.", in English and Spanish;
  - the code isn't counted as wrong;
  - it logs `logger.error('Signup code check failed: channel=%s purpose=%s error=%s')` on `signup.steps` (Sentry), with no personal values;
  - it records an event-log entry with reason `OTP check failed`.
  - The fake provider never raises it.
- **One Verify service for every purpose.** A resume code and a signup code to the same email share Twilio's pending verification, which is accepted (see Design Notes). `purpose` only reaches our limits and logs.
- **Logging:** the provider logs on `signup.verification`, which Sentry ignores, and never logs a code or a contact. Its errors reach Sentry through the callers' `signup.steps` `logger.error`.
- **Checks:**
  - Deploy check `mytp_publicsite.E006`: `check --deploy` fails while `DEBUG` is off and `VERIFICATION_PROVIDER` is the fake.
  - A normal check `signup.E001` (also run by `migrate`) fails when the Twilio provider is selected and any of the four credentials is empty. It names the setting only.
- **Tests and development:** they stay on the fake. Root `conftest.py` pins `VERIFICATION_PROVIDER` to the fake, as it does for the KMS keys. Twilio's tests fake the HTTP call, and the `no_network` fixture stays.
- **`deploy/README.md`:** "Twilio Verify". It covers which credentials go in `settings.env` (typed in by the person, never pasted anywhere else), the console settings (code length 6, Fraud Guard on, SMS geo permissions, the email integration and template, the WhatsApp sender), the Sentry alert rule, and the staging checks.
- Don't install or download anything outside the sandbox, and never call the real Twilio.

**Decisions (user, 2026-10-15):**
- Not everything on Twilio's side is ready yet. `TWILIO_WHATSAPP_ENABLED` (bool, default False): while off, the provider raises `ChannelUnavailable` for WhatsApp without calling Twilio, so mobile codes go by SMS through the existing fallback. Staging switches to the Twilio provider once the Verify service and the SendGrid email integration exist; WhatsApp is turned on once the sender is approved. The README says this order.
- WhatsApp that never arrives: no automatic fallback. The mobile code page, when the code went by WhatsApp, shows "Didn't get it on WhatsApp?" next to the existing "Send by SMS instead" button (English and Spanish).
- Spike alert: a Sentry alert rule on "Signup code send failed", 5 or more events in 10 minutes, emailing devops@mytreatplan.com (set by a person; the README gives the steps).
- Email languages: one SendGrid dynamic template that switches English and Spanish on the `locale` Verify passes; its id is set in the Verify service in the console, not in our settings.

**Never:**
- No code or contact in logs, errors, Sentry or the event log.
- No credential in the repo, the chat or a test.
- The end-to-end suite stays on the fake (epic notes).
- No change to our limits, the fallback rules or the screens, except the check-failure error and the WhatsApp hint.
- No webhooks or status callbacks.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected | Error handling |
|---|---|---|---|
| Email send | `email`, `es` | POST `Channel=email`, `Locale=es`, `To`=email | — |
| WhatsApp send | Reachable number | POST `Channel=whatsapp` | — |
| WhatsApp unreachable | Twilio "channel unavailable" error | `ChannelUnavailable` → caller falls back to SMS | — |
| Send refused | Fraud Guard / 60203 / 401 / 5xx / timeout | `SendFailed` with Twilio code + status only | Caller's retry page + Sentry |
| Check approved | Correct code | `APPROVED` | — |
| Check wrong | `pending` | `WRONG` | — |
| No code | 404 or 60202 | `NO_CODE` → "code expired" | — |
| Malformed code | `12ab` | `WRONG`, no HTTP call | — |
| Check failure | Timeout / 5xx / 401 | `CheckFailed` → form error, not counted | `logger.error` + event |
| WhatsApp off | `TWILIO_WHATSAPP_ENABLED` False, mobile send | `ChannelUnavailable`, no HTTP call → SMS | — |
| WhatsApp hint | Code sent by WhatsApp | "Didn't get it on WhatsApp?" by "Send by SMS instead"; not shown after SMS | — |
| Missing credential | Twilio selected, one setting empty | `signup.E001` names it | — |
| Fake in production | `DEBUG` off + fake | `mytp_publicsite.E006` | — |
| Secrets in errors | Any failure | No contact, code or key in message, log or `repr` | — |

</frozen-after-approval>

## Code Map

- `signup/verification.py`:
  - the interface (:51), with `ChannelUnavailable` (:26) and `SendFailed` (:30);
  - the constants `EMAIL`/`WHATSAPP`/`SMS` and `APPROVED`/`WRONG`/`NO_CODE`;
  - `get_provider()` (:151).
  - Add `CheckFailed` and `TwilioVerifyProvider` here, or in a new `signup/twilio_verify.py` imported by its path.
- `signup/steps.py`: `_send` (:319-347) holds the fallback and the send-failure `logger.error` and event. No change.
- `signup/forms.py`: `CodeForm.clean_code` (:422-432) gets the `CheckFailed` handling. Reasons are at :57-68.
- `accounts/http.py`: `FETCH_ERRORS`, the timeout and user-agent pattern. `mytp_publicsite/kms.py:133-160`: the error mapping without exposing the original text.
- `mytreatplan/sentry.py:22`: `signup.verification` is ignored by Sentry. Keep it that way.
- `mytreatplan/settings.py:64`: the Twilio settings go next to it, each `config(..., default='')`. Examples: `settings.env.example:29-33` and `deploy/staging.env.example:13-15`.
- `mytp_publicsite/checks.py`: E006 follows E003/E004. A new `signup/checks.py` registered in `signup/apps.py`.
- Tests:
  - new `signup/tests/test_twilio_verify.py`, which patches the provider's single HTTP function (as `signup.captcha.post_siteverify`);
  - the `CheckFailed` cases in `signup/tests/test_mobile_code.py` or `test_wizard.py`;
  - E006 in `mytp_publicsite/tests/test_settings_staging.py`;
  - the no-secrets check alongside `mytp_publicsite/tests/test_monitoring.py:322`.
- `signup/templates/signup/_code_help.html` and the "Send by SMS instead" label in `signup/steps.py`: the WhatsApp hint goes next to that button.
- `conftest.py` (root): pin `VERIFICATION_PROVIDER`.
- `locale/es/LC_MESSAGES/django.po`.

## Tasks & Acceptance

**Execution:**
- [x] `signup/verification.py` (+ `signup/twilio_verify.py`): `CheckFailed` and the provider, per Always.
- [x] `signup/forms.py`: handle `CheckFailed`. `signup/templates/signup/_code_help.html`: the WhatsApp hint.
- [x] `mytreatplan/settings.py`, both env examples, `signup/checks.py` + `signup/apps.py`, `mytp_publicsite/checks.py`: the settings and the checks.
- [x] `conftest.py`, `locale/es/LC_MESSAGES/django.po` (+ `compilemessages`), `deploy/README.md`.
- [x] Tests for every matrix row.

**Acceptance Criteria:**
- Given staging with the Twilio credentials, when a person signs up, then real codes arrive by email and WhatsApp, and by SMS for a number without WhatsApp. In Spanish, the messages are in Spanish.
- Given staging with a wrong `TWILIO_VERIFY_SERVICE_SID`, when the threshold's number of codes is requested, then the retry page shows and Sentry's alert rule emails devops@mytreatplan.com.

## Implementation Notes

- The provider is in `signup/twilio_verify.py`, with `post_verify(path, data)` as its only HTTP function. `TWILIO_ACCOUNT_SID` is required (`signup.E001`) but not sent: an API key identifies its account.
- `signup/checks.py` already existed (the disposable-email check); `signup.E001` was added there, registered from `SignupConfig.ready()`.
- The Twilio error codes mapped to `ChannelUnavailable` (60205, 60223, 63003) and to `NO_CODE` (404, 60202) come from the implementer's knowledge; they could not be checked against Twilio's docs from the sandbox. To confirm on staging.
- Staging's `check --deploy` reports E006 until it moves to Twilio; `release.sh` and CI run plain `check`, so deploys aren't blocked.

## Plan Change Log

- 2026-10-18 (staging): the spike alert is a Sentry **metric monitor** ("Number of Errors" on `message:"Signup code send failed"`, staging, 10 minutes, above 4), not the issue alert the README first described. That issue alert offered only a 15-minute window and never fired: it runs only when an issue changes state, and every failed send joins one existing issue. Boundaries' "5 or more in 10 minutes" is met as written. The README now describes the monitor.

## Review Triage Log

Quick review, pass 1 (2026-10-15): 7 findings. high 0, medium 0, low 5, false 2, maybe-false 0.

| # | Finding | Verdict | Route | Evidence |
|---|---|---|---|---|
| 1 | A check that times out after Twilio approved the code becomes "code no longer valid" on the retry | low | reject | Real but rare (the answer lost after Twilio processed it, within a 3 s socket timeout); the person sends a new code. A fix needs new state (remembering an in-flight check). |
| 2 | Every check 404 is `NO_CODE`, so a wrong service SID shows "code expired" on checks with no Sentry error | low | patch | Real: Twilio answers both with 404. Sends to a wrong SID fail and alert first; the README now says so. |
| 3 | Our per-purpose limits plus the WhatsApp→SMS double call can exceed Verify's per-`To` send limit (60203) on the shared service | low | reject | Real in principle, but it needs 5 sends to one address within 10 minutes; our cooldown is 60 s and the hourly limit 4 per purpose, and a synchronous WhatsApp refusal is rare. 60203 then shows the retry page and alerts, which is visible. |
| 4 | The callers' `logger.error` sends only the exception type to Sentry, so the alert can't tell 401 / wrong SID / Fraud Guard / 5xx apart | low | patch | Real: `type(error).__name__` is always `SendFailed`/`CheckFailed`. The provider's messages hold only the Twilio code and HTTP status, so the message is logged instead. |
| 5 | Acceptance criteria 1 and 2 not shown by the diff | false | reject | Staging checks after the merge, by design (hitl story). |
| 6 | No wizard-level test of a synchronous channel-unavailable answer falling back to SMS with WhatsApp on | low | patch | Real gap: only the provider mapping and the WhatsApp-off path were tested end to end. Test added. |
| 7 | The check-failure `rejected` event isn't proven free of the contact | false | reject | Code steps have `log_fields = ()` (`signup/steps.py:62`), so their events carry no data at all. |

## Design Notes

**One service.** Verify keeps one pending verification per `To` and service. A resume code sent while a signup code is still pending re-sends the same code. Either one proves control of the email, so sharing is safe. Two services would double the console setup.

**Codes are Twilio's.** Our code rules match the Verify defaults (6 digits, 10 min, 5 attempts), and the service is set to length 6 in the console.

## Verification

**Commands:**
- `DATABASE_URL="sqlite:///$TMPDIR/x.sqlite3" ../.venv/bin/python -m pytest -q` (with the test keys). Expected: only the 3 known PostgreSQL-only tests fail.
- `manage.py makemigrations --check`. Expected: no changes.
- `manage.py compilemessages`. Expected: OK.

Done locally (2026-10-15) after the review patches, on SQLite: `pytest` 938 passed, 7 skipped (PyYAML, and the browser test without Playwright), 3 failed only because those tests use PostgreSQL-only raw SQL. `makemigrations --check` reports no changes; `compilemessages` is up to date. No real Twilio was called. Pending: CI on PostgreSQL, then on staging (README "Twilio Verify"): the console setup, the credentials, the switch to the Twilio provider, the Sentry alert rule, and the acceptance criteria; WhatsApp once the sender is approved. CI on PostgreSQL 16 (PR #25): 948 passed.

Staging (2026-10-18): the merge deployed. The Twilio console was set up: the Verify service (code length 6, Fraud Guard on), the SendGrid email integration with one template switching on `locale`, and the API key. The credentials were typed into `settings.env` and the provider switched; `check` reports no issues, and `check --deploy` no `signup.E001` or `mytp_publicsite.E006`. The user reported these checks passed:
- the email code arrives in Spanish from `/es/` and in English;
- with WhatsApp off, the mobile code arrives by SMS;
- 5 wrong codes give "no longer valid";
- the journal holds no codes or contacts;
- with a wrong service SID, 5 requests showed the retry page, and the metric monitor emailed devops@mytreatplan.com (the issue alert tried first never fired; see the Plan Change Log).

SMS first failed with Twilio `21608` (HTTP 403), which the app handled as designed (retry page, Sentry error). Twilio's own message said the account needed an approved Primary Customer Profile (Trust Hub). Until then, SMS went only to a Verified Caller ID, which had to be verified by voice call because Twilio blocks SMS verification for Spain. The profile has since been approved, and SMS now reaches unverified numbers. The sender is Twilio's pool (`AUTHMSG`, `TWVerify`). A branded sender ID `mytreatplan` was approved and set as the default, best effort where no pre-registration is needed. The README "Twilio Verify" now has these steps.

Still open: WhatsApp waits for Meta to approve the sender (`TWILIO_WHATSAPP_ENABLED=True`, then the WhatsApp checks). Before production: SMS geo permissions, and a dedicated API key.
