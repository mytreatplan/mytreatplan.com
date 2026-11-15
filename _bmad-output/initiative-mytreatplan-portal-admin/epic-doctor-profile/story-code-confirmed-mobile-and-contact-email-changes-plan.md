---
title: 'Code-confirmed mobile and contact email changes'
type: 'feature'
ticket: '3'
created: '2026-11-15'
status: 'ready-for-dev'
baseline_revision: 'e0460f6'
route: 'full'
route_source: 'auto'
risk: 'high'
review: 'quick'
review_source: 'pinned'
lenses_ran: []
review_loop_iteration: 0
context: []
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** A doctor cannot change their mobile or contact email (D2). The mobile is read-only on the personal-data page, and the alternative email shipped by 6.2 can be changed without any confirmation.

**Approach:** Add two change flows under the settings area. Each takes effect only after a code sent to the new value (mytp-core codes, 3.2). A new mobile must not belong to another account. After a mobile change remembered devices are forgotten and other sessions end, since the mobile is a 2FA channel.

**Decisions (user, 2026-11-15):**
- "Contact email" is the existing alternative email, moved behind a code: read-only on the personal-data page with a "Change" link. The login email stays read-only (support changes it).
- Before a change starts the doctor types their current password.
- A mobile already used by another account is refused with a plain message, as signup does.
- No two accounts may share a mobile: a database rule, with a migration that stops and lists the accounts if duplicates already exist, and the same check in the portal's staff admin.
- After a mobile change every remembered device is forgotten, this browser's too; the doctor stays signed in.
- An email goes to the login email after each change.
- A change code dies after 5 wrong tries; the account is not locked.
- The mobile code goes out as signup's does (WhatsApp when enabled, otherwise SMS, with "send by SMS").
- Merge and staging are delegated for 6.3. The code steps are checked on the server with the fake provider; the user may also try a real change in a browser.
- A signed-in account can send only a limited number of change codes per hour, whatever the numbers or addresses.

## Boundaries & Constraints

**Always:** A new value is written only after its code was accepted, by one service function beside `update_personal_data` that locks the user row, re-checks the duplicate, writes, and emits `client.updated` once. The pending new value is kept encrypted in a pending-change row that lives 10 minutes; the session holds only its id. The password check counts on the password lockout counter. Codes use a purpose of their own and the existing send limits, plus the per-account limit. The duplicate rule ignores erased accounts (blank index). The notice email names what changed, never the new value in full. Every new string has its Spanish translation (formal "usted"). The personal-data form no longer accepts the alternative email.

**Never:** No change of the login email or the country. No core release, no admin-site change. No sign-out of the current session after a change. No code or contact value in the session, the logs, the event log or page records. No new 2FA policy on the portal (the admin site's step-up is not reused). No password-change page.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| Start a mobile change | right password, new valid mobile not used by anyone | pending row created, code sent to the new mobile, code page shown | none |
| Wrong password | wrong current password | form error, nothing sent, counted on the password lockout | locked account: the lockout's own message |
| Mobile in use | new mobile belongs to another account | refused: "already used by another account"; no code sent | none |
| Same mobile | new mobile equals the current one | form error, no code sent | none |
| Invalid mobile | not a valid number for the prefix | form error | none |
| Right code | code accepted within 10 minutes | mobile stored, `client.updated` emitted once, remembered devices forgotten (a mark issued before no longer skips the login code), session kept, notice email to the login email, success message on the personal-data page | none |
| Wrong code | wrong code up to 5 times | form error each time; after the 5th the code is dead and a new one must be requested; account not locked | none |
| Expired | code entered after 10 minutes, or the pending row is gone | "expired, start again"; nothing written | none |
| Race | another account takes the mobile between send and check | refused at the check; nothing written | database rule as the last guard |
| Resend / SMS | "send again" or "send by SMS" on the code page | a new code within the cooldown and hourly limits | limit reached: the existing limit message |
| Per-account limit | more change codes in an hour than allowed, to different numbers | refused with the limit message | none |
| Contact email change | right password, new valid email different from the login email | code sent to the new address; after the right code the alternative email is stored, `client.updated` emitted, notice email sent; devices not forgotten | same code errors as above |
| Contact email same as login | new email equals the login email | form error | none |
| Personal-data form | POST carries `alternative_email` | ignored; the row shows the stored value read-only with "Change" | none |
| Staff admin | staff sets a mobile used by another account | form error | none |
| Migration | duplicates exist before the rule | the migration stops and lists the account ids; nothing changed | none |
| Signed out | GET a change page | redirect to login | none |
| Staff account | staff opens a change page | works; no event (not a client) | none |
| Spanish | `/es/settings/mobile/` | Spanish texts and messages | none |

</frozen-after-approval>

## Code Map

- State of the dependencies: 3.2 and 3.3 are `built` and live through the vendored core v0.26.0 (`mytp_core.verification`, `devices`, `sessions`, `lockout`; portal wrappers `signup/verification.py`, `signup/twilio_verify.py`, `accounts/devices.py`, `accounts/sessions.py`). 3.4/3.5 (step-up) exist only in mytp-admin (`staff/stepup.py`; core policy `PORTAL.fresh_code=False`).
- Sending: `signup/steps.py:277` `send_code(request, attempt, page, contact, purpose, channel, resend, login_record)`, hooks `_SendHooks` (:304); limits `signup.limits` (`COOLDOWN_SECONDS=60`, `HOURLY_SENDS=4` per contact and purpose), per-IP `CODE_SENDS` 120/hour (`settings.py:192`). Checking: `signup/forms.py:459` `CodeForm.clean_code` → `get_provider().check(channel, contact, purpose, code)`; subclasses `EmailCodeForm`, `MobileCodeForm`, `ResumeCodeForm`, `accounts/forms.py:98` `LoginCodeForm`. Core rules (`verification.py:51-59`): purposes `signup`, `resume`, `login`; 6 digits; 10 minutes; 5 wrong tries.
- Between send and check: session key `signup_code_state` (:232) holds channel, send time, counts, never a contact; signup keeps contacts in the encrypted `PendingRegistration`; login keeps `login_pending` (`accounts/views.py:118-134`, blind indexes only). `_account_unchanged` (:149) ends a pending login when `phone_index` changes.
- Fake provider: `signup.verification.FakeVerificationProvider` (`current_code`); the portal has no `ALLOW_FAKE_VERIFICATION`; `mytp_publicsite.E006` refuses the fake with DEBUG off. dev runs real Twilio Verify (SMS; email via Verify's email channel); `TWILIO_WHATSAPP_ENABLED=False` by default (`settings.py:160`). Portal mail over SMTP (`MAILERS`, `settings.py:647-670`).
- `accounts/models.py:98-108`: `email_index` unique; `phone_index` indexed, not unique (signup checks: `signup/forms.py:309-321` `clean_phone`, `phone_is_registered` :111; re-check under lock `signup/views.py:417-422`). Erased accounts have `phone_index=''`. `parse_mobile` (`signup/forms.py:142`), `phone_prefix_choices` (:131). The portal's staff admin edits `phone` with no duplicate check (`accounts/admin.py:104`). `plan-admin-unique-mobile.md` is the precedent for a constraint migration that stops on duplicates.
- `Profile.alternative_email` (`accounts/models.py:216`, encrypted): editable with no code in `PersonalDataForm` (`accounts/forms.py:202`, `clean_alternative_email` :273, `changes()` :312); travels in `client.updated` (core 0.26.0) to the admin record. Nothing sends mail to it. Tests `accounts/tests/test_personal_data.py:315-322`.
- Sessions: single session enforced (core `sessions.py:136-154`, middleware); `end_all_sessions(user)` (`accounts/sessions.py:114`) ends all incl. the current one (password reset `password_reset.py:382-390`). Device marks: signed cookie `{u, t, h=session auth hash}` (core `devices.py:40-63`), validity `accounts/devices.py:70`; only a password change invalidates them. No password-change page (`deferred-work.md:132`).
- 6.2 page: read-only rows `accounts/templates/accounts/personal_data.html:51-52` (mobile), :61-64 (login email), alternative email field :58; view `accounts/views.py:449`; service `accounts/profile.py:31-34` `EDITABLE_FIELDS`, `update_personal_data`. `PORTAL_PREFIXES` covers `settings/`; URL-name lists `mytp_publicsite/tests/test_settings_staging.py:222-228`, `urls_with_portal.py:24-28`; `in_settings` filter.
- `accounts/client_sync.py:46-62` `payload_of` carries `email`, `phone`, the six profile fields. Mail pattern: `accounts/password_reset.py` (`build_message`, `in_background`, templates `accounts/templates/accounts/email/`).
- Test helpers: `signup/tests/helpers.py:75` `_current_code`; `accounts/tests/helpers.py:38-72` (`_login_with_code`, `_login_remembering`, `_mark`); `signup/tests/conftest.py` `clock`, `capture_codes`.
- Open deferred items nearby (`deferred-work.md:10,25,34`): provider checks compare string paths; one Twilio Verify service for every purpose (a new purpose shares the pending verification with signup/login codes for the same contact, `signup/twilio_verify.py:24-30`).

## Tasks & Acceptance

**Execution:**
- [ ] `accounts/models.py` + migrations -- `PendingContactChange` (user, kind mobile or email, encrypted value, blind index, created; one live row per user and kind); `User.devices_valid_after`; a partial unique constraint on `phone_index` (not blank) whose migration first stops with the account ids if duplicates exist.
- [ ] `accounts/devices.py` -- a remembered-device mark is valid only when issued at or after `devices_valid_after`.
- [ ] `accounts/profile.py` -- `start_contact_change`, `confirm_contact_change` (lock, duplicate re-check, write, emit once, forget devices for a mobile, notice) and the read helpers the pages need; remove `alternative_email` from `EDITABLE_FIELDS`.
- [ ] `accounts/forms.py`, `accounts/views.py`, `accounts/urls.py`, templates -- routes `settings_mobile`, `settings_mobile_code`, `settings_email`, `settings_email_code` under `settings/`; start forms (password + new value, the signup phone widget), code forms on the existing `CodeForm`; send through `signup.steps.send_code` with the new purpose; resend and "send by SMS"; the per-account limit in `signup.limits`' style.
- [ ] `accounts/templates/accounts/personal_data.html`, `PersonalDataForm` -- mobile and alternative email rows read-only with "Change" links; the form drops the alternative email.
- [ ] `accounts/admin.py` -- the staff form refuses a mobile used by another account.
- [ ] notice email -- plain-text templates (EN, ES) on the password-reset mail pattern, sent in the background to the login email.
- [ ] `locale/es/LC_MESSAGES/django.po` -- Spanish for every new string.
- [ ] Tests in `accounts/tests/` (and the URL-name lists in `mytp_publicsite/tests/`) -- every matrix row; update the 6.2 tests of the alternative email; one browser test of a mobile change with the fake provider.
- [ ] `deploy/README.md` and `deferred-work.md` -- the flows, the duplicate migration's stop and how to resolve it, the staging recipe with the fake provider; deferred: which address future doctor messages use (epic-case-lifecycle).

**Acceptance Criteria:**
- Given a doctor with the right password and a free new mobile, when the code is accepted, then the mobile is stored, one `client.updated` is in the outbox, a remembered-device mark issued before no longer skips the login code, and the session is still signed in.
- Given a mobile used by another account, when a change is started or confirmed, then it is refused and nothing is written.
- Given a new contact email, when its code is accepted, then the alternative email is stored and one event emitted; before that the stored value is unchanged.
- Given five wrong codes, when a sixth try is made, then the code is dead and the account can still log in.
- Given the full suite, when it runs, then it passes with no new skip and `makemigrations --check` reports no change.

## Design Notes

Recommended shape (portal only, no core release): routes `settings/mobile/` and `settings/email/` with their code steps; a small encrypted pending-change table (user, kind, value, index, created, 10-minute life), the session holding only its id; a second service function beside `update_personal_data` (locks the user row, re-checks the duplicate, writes, emits `client.updated` once); `User.devices_valid_after` checked in `accounts/devices.py` to forget marks without a password change; a per-account limit on codes sent to new contacts (the per-contact limits do not stop many different numbers); a new code purpose for changes.

## Verification

Full suite; `makemigrations --check`.
