---
title: 'Duplicate checks and resume'
type: 'feature'
ticket: '5'
created: '2026-10-13'
status: 'built'
baseline_revision: '52bce13188b3852d37f79770f8a51c42ba8a22f5'
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

**Problem:** Step 1 only refuses a registered email, and shows no recovery link. A registered mobile gets through. A second signup with the email or mobile of a pending registration starts a duplicate, and a Doctor who closed the page can't get their saved data back (CAP-2, CAP-3, failure policies).

**Approach:**
- Before step 1 is saved, block a registered email or mobile with a link to password recovery.
- For a match in an active pending registration, warn and offer a resume code. The code goes to the matched contact (email, or mobile via the fake provider).
- A correct code restores the saved data in a new pending registration that replaces the old one.
- The same flow covers the page-closed case, and entry 8 reuses it.

## Boundaries & Constraints

**Always:**
- Reasons, in the policy wording: `email exists`, `phone exists`, `email pending`, `phone pending`. The replaced attempt ends with outcome `abandoned`.
- Registered checks win over pending ones. Email is checked before mobile.
- Pending checks run only when the rest of step 1 is valid, and never match the session's own pending row.
- A pending row counts as active for 24 h from `created_at`, through one queryset that entry 10 reuses.
- Without the correct code, nothing from the matched registration is shown, and no data is copied.
- Resume codes have their own purpose (`resume`), so a signup code doesn't work for resume.
- The session holds only the matched row's id and the channel, never a contact. The page reads the contact from the row.
- The data typed in the new step 1 isn't stored. Its loggable values go in the `pending_found` event, never the password.
- The recovery link uses the URL name `password_reset`, the way `_portal_links.html` handles `login`: the message always shows, and the link appears once epic-login registers the name.
- English and Spanish strings for every new text.
- Decision (user, 2026-10-13): a resumed signup lands on the old row's next unsaved step (today the email code step, which sends a new email code); step 1 stays reachable with Back.
- Decision (user, 2026-10-13): the registered-mobile race is guarded by a check at account creation inside the transaction; no database constraint on `User.phone_index`.
- Decision (user, 2026-10-13): the plan is kept whole, about 1,800 tokens.

**Never:**
- Resend limits, attempt limits, or a real WhatsApp/SMS send (entry 7). The 24 h purge job (entry 10). Idle timeout (entry 8).
- Telling the user anything beyond "in progress" about the matched registration before the code.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| Registered email | Step 1 with a user's email, in any case | Error on email with the recovery link; `rejected`, reason `email exists` | No error expected |
| Registered mobile | Step 1 with a user's mobile, typed in a different format | Error on mobile with the recovery link; `rejected`, reason `phone exists` | No error expected |
| Pending email | Valid step 1; another active pending row has this email | Redirect to the resume page with the warning and a "Send code" button; `pending_found`, field `email`, reason `email pending` | No error expected |
| Pending mobile | As above, the mobile matches | Same flow, code to the mobile; reason `phone pending` | No error expected |
| Resume code correct | Code for the matched contact | A new pending row holds the old data with a new id and this attempt. The old row is deleted and its attempt ends `abandoned`. Then `resumed` and redirect to the old row's current step | No error expected |
| Resume code wrong | Bad code | Error, nothing revealed; `rejected`, field `code`, reason `OTP verification failed` | No error expected |
| Page closed | A fresh client enters the same email within 24 h | Resume flow; afterwards step 1 shows the old names, country and mobile | No error expected |
| Stale pending | Matching row older than 24 h | No warning; step 1 saves as usual | No error expected |
| Own pending | Back to step 1 and re-saved with the same email | Saves as today, no warning | No error expected |
| Race at creation | Mobile registered while the attempt was in progress | `rejected`, reason `phone exists`, the recovery message | As with email |

</frozen-after-approval>

## Code Map

- `signup/forms.py` -- `AccountForm.clean_email` (add `phone_registered`, next to `email_registered`); `FAILURE_REASONS`; `email_is_registered` (add `phone_is_registered` through `User.phone_index`, `accounts.crypto.phone_index`). The resume code form follows the `EmailCodeForm` pattern, with a channel and a contact.
- `signup/models.py` -- `PendingRegistration`: add a manager or queryset `active()` (24 h from `created_at`) and `matching_email`/`matching_phone` via `email_index`/`phone_index`. Also a method that copies the user-facing fields, password hash, `phone_region` and `step` into a new row.
- `signup/views.py` -- `signup()`: after `form.is_valid()` on step 1 and before `step.save`, look for a pending match, log `pending_found` and redirect to the resume view. `_create_account`: add the phone race check next to the email one. Add the new `resume` view, which stays outside the generic step loop. Session keys go next to `SESSION_KEY`; the helpers can be reused by entry 8.
- `signup/urls.py` -- `signup/resume/` (name `signup_resume`) **before** `signup/<slug:slug>/`.
- `signup/verification.py` -- add `PURPOSE_RESUME`. `MOBILE` already works in the fake provider.
- `signup/steps.py` -- `EmailCodeStep._send` shows the `code_sent` pattern. Resume sends log `code_sent` with `channel` and `purpose: resume`.
- Templates: `signup/templates/signup/resume.html` (extends `portal_base.html`, styled as `wizard.html`). `step_account.html`, or `wizard.html` for the race non-field error, shows the recovery note with `{% url 'password_reset' as reset_url %}` when an error has code `email_registered`/`phone_registered`.
- `eventlog/service.py` -- `record`, `end_attempt`, `get_attempt`. No changes.
- `locale/es/LC_MESSAGES/django.po` -- the new strings.
- Tests: new `signup/tests/test_duplicates.py`. The helpers in `test_step1.py` post a full step 1. The conftest `external_checks` fixture is used. Read codes with `FakeVerificationProvider._code` as the existing tests do.

## Tasks & Acceptance

**Execution:**
- [x] `signup/models.py`, `signup/verification.py` -- active queryset, matching lookups, copy-to-new-row, resume purpose.
- [x] `signup/forms.py` -- registered-mobile error, reasons, resume code form.
- [x] `signup/views.py`, `signup/urls.py` -- pending detection, resume view (offer, send, resend, check, replace), phone race check.
- [x] `signup/templates/signup/resume.html`, `step_account.html`/`wizard.html` -- warning, code form, "use different details" link back to step 1 (clears the resume state), recovery note.
- [x] `locale/es/LC_MESSAGES/django.po` -- Spanish.
- [x] `signup/tests/test_duplicates.py` -- every matrix row, plus the event sequence on both attempts.

**Acceptance Criteria:**
- Given staging, when a person starts a signup, closes the browser, and starts again in a private window with the same email, then a code from the journal resumes it with the old data, and the admin shows the old attempt `abandoned` and the new one with `pending_found` and `resumed`.
- Given CI on PostgreSQL, when the suite runs, then every test passes.

## Implementation Notes

- The resume session state also holds a `sent` flag (no contact) so the page shows the code form only after "Send code".
- After the code, the page shows the contact read from the row; it is the value typed at step 1 (same blind index), so nothing new is revealed.
- The recovery note is one include (`signup/_recovery_note.html`), shown by `wizard.html` whenever a form error has code `email_registered`/`phone_registered` (step 1 fields and the race's non-field error).
- "Use different details" is `signup/resume/?cancel` (clears the resume state, back to step 1). A step 1 that saves also clears any stale resume state.
- If the matched row was resumed elsewhere or went stale before the code, the code POST sends the person back to step 1.
- Not done here: the staging acceptance check and CI on PostgreSQL. Run locally on SQLite only.

## Plan Change Log

## Review Triage Log

**Pass 1 (quick lens, 2026-10-13):** high 0, medium 1, low 3, false 1, maybe-false 0. One patch; four findings rejected.

| # | Finding | Verdict | Route | Evidence |
|---|---------|---------|-------|----------|
| 1 | Both acceptance criteria (staging resume, PostgreSQL CI) unmet | false | rejected | Not a code defect: both criteria are checked after the push (CI) and after the merge (staging), as in every story of this epic. |
| 2 | A resume gives the new row a fresh `created_at`, so each resume restarts the 24 h window and personal data can outlive the purge | medium | patch | Real: `copy_to_new_row` saves a new row and `auto_now_add` sets `created_at` to now. `active()` and the entry 10 purge count from it. Fix: the new row keeps the old row's `created_at`, with a test. |
| 3 | A code POST is checked before "Send code", and a wrong one is then rendered without the code form | low | rejected | Needs a hand-made POST: the page shows no code field before the send. With Twilio (entry 11) an unsent code never checks. The fix adds a branch. |
| 4 | Email and mobile both registered: both errors show and two `rejected` events are logged | low | rejected | Both errors are true statements about what the person typed, and the logs are correct. "Email before mobile" was written for the pending match. Changing it would add a cross-field branch. |
| 5 | `_take_over` with no open attempt in the session makes a row without an attempt, and later events are dropped | low | rejected | Reachable only if another browser resumes this browser's own row while this browser sits on the resume page, or the attempt read fails. Step 1's POST always opens an attempt before the redirect. The fix adds a fallback branch. |

## Design Notes

- **Replace, not reuse:** the resumed data moves to a new row id, so a browser still pointing at the old row loses it on its next request (`_pending_registration` drops a missing id). This browser's own earlier pending row, if it had one, is deleted too.
- **Events:** new attempt: `pending_found` → `code_sent` (resume) → `resumed` (data: the replaced attempt's id). Old attempt: `end_attempt(old, 'abandoned')`.

## Verification

**Commands:**
- `DATABASE_URL="sqlite:///$TMPDIR/x.sqlite3" ../.venv/bin/python -m pytest -q` (with test keys) -- expected: only the 3 known PostgreSQL-only tests fail.
- `manage.py makemigrations --check`, `manage.py compilemessages` -- expected: no changes; compiles.

Done locally (2026-10-13) after the review patch, on SQLite: `pytest` 532 passed, 6 skipped (PyYAML), 3 failed only because those tests use PostgreSQL-only raw SQL. `makemigrations --check` reports no changes; `compilemessages` is up to date. Pending: CI on PostgreSQL and the staging resume check.

