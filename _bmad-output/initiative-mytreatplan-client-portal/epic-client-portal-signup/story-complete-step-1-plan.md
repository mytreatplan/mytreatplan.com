---
title: 'Complete step 1'
type: 'feature'
ticket: '4'
created: '2026-10-13'
status: 'done'
baseline_revision: '1aa2a4073458dd0bf1c7c09fdf1ff5248711d1c6'
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

**Problem:** Step 1 asks only for an email and a password. `signup-fields.md` requires the full set of personal, contact and login fields, plus a password policy (breached-password check, similarity to name and email, a strength meter) and rejection of disposable email domains. Without these the wizard can't collect a Doctor's identity or contact details.

**Approach:**
- Add every step-1 field and rule from `signup-fields.md` (except the CAPTCHA, which is entry 9).
- Store the personal values encrypted on the pending registration, then copy them to the user at account creation. The mobile also gets a phone blind index.
- Check breached passwords with the Have I Been Pwned range API.
- Check disposable domains with Debounce's free API.
- Provide every string in English and Spanish.

## Boundaries & Constraints

**Always:**
- Decision (user, 2026-10-13): breached passwords are checked with the Have I Been Pwned range API (k-anonymity). Only the first 5 hex characters of the SHA-1 leave the server, the request sends `Add-Padding: true`, and a hash is breached when its suffix appears in the response.
- Decision (user, 2026-10-13): disposable domains are checked with `https://disposable.debounce.io/?email=check@<domain>`. Only the domain leaves the server, never the person's address. On a timeout or error the email is accepted and the event log records that the check was unavailable.
- Decision (user, 2026-10-13): countries come from `django-countries` (names in English and Spanish). Prefixes, mobile validation and E.164 storage come from `phonenumbers`. Both are pinned in `requirements.txt`.
- Decision (user, 2026-10-13): the strength meter uses vendored `zxcvbn-ts` (core plus the common dictionaries), loaded on step 1 only.
- Decision (user, 2026-10-13): if Have I Been Pwned times out or errors, the password is accepted when the other rules pass, and the event log records `check_unavailable`.
- Decision (user, 2026-10-13): the plan is kept whole, about 1,700 tokens.
- Both checks have a short timeout (3 s). Tests never reach the network, and the test settings fail any real call.
- The breached-password check is a password validator in `AUTH_PASSWORD_VALIDATORS`, so `createsuperuser` and later password changes (epic-login) use it too. The similarity check compares against the name, middle name, surnames and email.
- Personal values are encrypted with the 2.2 fields:
  - names;
  - the mobile, stored in E.164 form;
  - the "Others" text.

  `phone_index` is indexed but not unique; uniqueness checks are entry 5. Title, country and how-found-us are stored as codes.
- The event log keeps the new fields' values (encrypted), never the passwords. Disposable-email rejections use the policy reason `email disposable`.
- The strength meter is a hint only. The server-side rules decide.
- Every new label, choice and message has its Spanish translation in `locale/es`.

**Never:**
- CAPTCHA or rate limits (entry 9), duplicate mobile or pending checks and resume (entry 5), mobile verification (entry 7).
- Sending the full email address or the password (or its full hash) to any third party.
- Blocking signup because an external check is down.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| Valid form | All mandatory fields, no middle name, a valid mobile for the chosen prefix | Pending registration saved. A raw select shows ciphertext for names, mobile and email, plus `phone_index` | No error expected |
| Others selected | how-found = Others, empty text | Error on the Others field. The text is required only for Others | No error expected |
| Others not selected | Others text filled, another choice selected | The text is ignored and not stored | No error expected |
| Invalid mobile | A number that isn't valid for the prefix | Error on the mobile field | No error expected |
| Breached password | Its suffix appears in the HIBP response | Error on the password field. Event `rejected` with code `password_breached` | No error expected |
| Password like name | Password close to the surname | Similarity error on the password field | No error expected |
| HIBP down | Timeout or 5xx | The password is accepted if the other rules pass. Event `check_unavailable`, field `password1` | Caught |
| Disposable email | Debounce says disposable | Error asking for a professional email. Reason `email disposable` | No error expected |
| Debounce down | Timeout, error or unexpected body | The email is accepted. Event `check_unavailable`, field `email` | Caught |
| Spanish | `/es/` with invalid input | Every error and label is in Spanish | No error expected |
| Account created | Valid steps 1–2 | The user has title, names, country, mobile (encrypted) and how-found-us | No error expected |

</frozen-after-approval>

## Code Map

- `signup/forms.py` -- `AccountForm`: email and passwords plus the `clean()` that runs Django's validators. Extend it in place: same `error_messages`/`code` pattern, and `FAILURE_REASONS` for the policy wording.
- `signup/steps.py` -- `AccountStep.save` and `log_fields` (now email only); `get_initial` refills the form from the pending row.
- `signup/models.py` -- `PendingRegistration`; copy the `set_blind_indexes`/`save` pattern for `phone_index`. Next migration is `signup/0006`.
- `signup/views.py:_create_account` -- builds the user from the pending row; copy the new fields across.
- `accounts/models.py` -- `User`: `first_name`/`last_name` are already encrypted. Add title, middle name, country, phone plus `phone_index`, and how-found-us with its Others text. Next migration is `accounts/0006`.
- `accounts/crypto.py` -- `EncryptedCharField`, `phone_index()`, `normalize_phone_for_index()` already exist.
- `mytreatplan/settings.py:199` -- `AUTH_PASSWORD_VALIDATORS`. Add the breached-password validator, plus settings for the two endpoints and the timeout.
- HTTP: there is no `requests`; use `urllib.request` with a timeout, as `mytp_publicsite/kms.py:127` does. Send a `User-Agent`.
- `signup/templates/signup/step_account.html`, `templates/includes/_form_field.html` -- one include per field; selects get the same classes (`FIELD_CLASSES`). Show the Others field only when Others is selected, while it stays a normal field without JavaScript.
- Front end: JS lives in `static/js/` (vendored, as with htmx, by a `package.json` script); `portal_base.html` loads scripts.
- `locale/es/LC_MESSAGES/django.po` -- add the translations. Deploy runs `compilemessages`.
- Tests: `signup/tests/test_wizard.py` and `test_event_log.py` post step 1 with only email and passwords, so update their helpers to post the full form. Root `conftest.py` is the place for the no-network guard.

## Tasks & Acceptance

**Execution:**
- [x] `accounts/models.py`, `accounts/password_validation.py`, migration -- new user fields; breached-password validator.
- [x] `signup/checks.py` -- disposable-email check (Debounce) with the safe failure.
- [x] `signup/models.py`, migration, `signup/forms.py`, `signup/steps.py`, `signup/views.py` -- the full step 1, saved, logged and copied to the user.
- [x] `signup/templates/signup/step_account.html`, `static/js/`, `package.json` -- fields, the Others toggle, the strength meter.
- [x] `mytreatplan/settings.py`, `requirements.txt`, `settings.env.example` -- validators, endpoints, timeout, new dependencies.
- [x] `locale/es/LC_MESSAGES/django.po` -- Spanish strings.
- [x] tests -- every matrix row, with both external checks mocked.

**Acceptance Criteria:**
- Given staging after the merge, when a person completes step 1 with real data, then the form saves, and a known breached password (e.g. `password12345`) is rejected by the live HIBP API.
- Given CI on PostgreSQL, when the suite runs, then all tests pass without network access.

## Implementation Notes

- HTTP goes through `accounts/http.py` (`get_text`: urllib, `EXTERNAL_CHECK_TIMEOUT`, our User-Agent). Both checks catch `FETCH_ERRORS` (OSError, HTTPException, ValueError) and return "unknown".
- HIBP: a suffix counts as breached only with a count above 0, since `Add-Padding` entries have count 0. An empty range response is treated as unavailable.
- `check_unavailable`: the form lists `unavailable_checks` as (field, check) pairs, and the view logs each one before `rejected`/`step_saved`. The validator reports through a contextvar (`collect_unavailable_checks()`) because Django caches validator instances.
- Prefix: the select's values are region codes ("ES", shown as "Spain (+34)"). The mobile must parse for that region's calling code, be valid, and be MOBILE or FIXED_LINE_OR_MOBILE. `PendingRegistration.phone_region` keeps the chosen region so step 1 can be refilled. It is not copied to the user (the E.164 number is enough).
- New `User` fields are all `blank=True`, so `createsuperuser` and the admin keep working. The admin fieldsets are unchanged.
- Tests: in root `conftest.py`, `external_checks` fakes the two fetch functions (nothing is breached or disposable by default), and `no_network` refuses HTTP(S) connections, `socket.create_connection`, and name lookups other than loopback and the DB host. The endpoint settings point at `.invalid` hosts.
- `test_data_migration_encrypts_existing_rows_and_reverses` now reads the user with `.only()`, because the current model has columns that don't exist at 0005.
- Spanish choices to review: Ms → "Srta.", Mrs → "Sra.", Dr. → "Dr./Dra.", Prof. → "Prof./Prof.ª", Eng. → "Ing.".

- Added after the build at the user's request (2026-10-13): the user admin shows title, middle name, country, mobile and how-found-us (with the Others text). The mobile is read-only, since signup step 1 validates it and stores it in E.164 form. `accounts/admin.py`, test `test_admin_shows_the_step1_details`, and the Spanish label "Registro". The user confirmed the Spanish titles (Sr., Sra., Srta., Dr./Dra., Prof./Prof.ª, Ing.).

## Plan Change Log

## Review Triage Log

**Pass 1 (quick lens, 2026-10-13):** high 0, medium 0, low 2, false 0, maybe-false 0. Both patched.

| # | Finding | Verdict | Route | Evidence |
|---|---------|---------|-------|----------|
| 1 | The 3 s timeout is per socket operation, not per call. DNS lookup has none, and the two checks run one after the other | low | patch | Real: `urlopen(timeout=)` limits each connect and each read. Both checks still fail open, so a slow call delays the signup but never blocks it. A hard deadline per call would need threads, so the patch only makes the settings and `accounts/http.py` comments state the actual behaviour. |
| 2 | `parse_mobile` accepts an extension, and E.164 formatting silently drops it | low | patch | Reproduced by the reviewer (`'612345678 ext 5'` → `+34612345678`). Fix: reject when `parsed.extension` is set, with both inputs added to the invalid-mobile test. |

## Design Notes

- **Stored on the user too:** login, profile and onboarding need these details, and the pending row is deleted at account creation.
- **Event log:** step 1's `log_fields` grows to every new field except the passwords. Personal values stay inside the encrypted `data` column.

## Verification

**Commands:**
- `DATABASE_URL="sqlite:///$TMPDIR/x.sqlite3" ../.venv/bin/python -m pytest -q` (with test keys) -- expected: only the known PostgreSQL-only tests fail.
- `manage.py makemigrations --check` and `manage.py compilemessages` -- expected: no changes; compiles.

Done locally (2026-10-13) after the review patches, on SQLite: `pytest` 507 passed (after the admin addition), 6 skipped (PyYAML), 3 failed only because those tests use PostgreSQL-only raw SQL. The implementer's run on a throwaway PostgreSQL passed all 507 tests before the patches. `makemigrations --check` reports no changes; `check` reports no issues (1 silenced); `compilemessages` is up to date. CI on PostgreSQL 16 (PR #15): 516 passed.

Staging (2026-10-13): the merge deployed and ran migrations `accounts.0006` and `signup.0006`. On https://dev.mytpdso.com a person completed step 1 with real data and moved on to the code step. The live HIBP API refused `password12345` as breached, and the live Debounce API refused a `@mailinator.com` address as disposable. At `/es/signup/` every label and error was in Spanish. After the signup, the user admin showed the title, country, mobile and how-found-us.

**Manual checks:**
- Fill step 1 in `/es/signup/` with invalid input: every message is in Spanish.

