---
title: 'Email-first resume'
type: 'feature'
ticket: '5'
created: '2026-10-13'
status: 'done'
baseline_revision: '4d3e58b601035a5f077425cf8fce5b63810f916d'
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

**Problem:** The resume flow from 2.5 (staging, 2026-10-13) makes a returning Doctor do too much:
- they fill in all of step 1 again before the resume is offered;
- they then need two codes, one to resume and one for the email. With the mobile code (entry 7) that would be three.

**Approach:**
- Email moves to the top of step 1 and is checked as soon as the person leaves the field.
- A match with a signup in progress offers a resume. One code to that email proves control, so it also marks the email verified.
- The saved data is reloaded into step 1 for review. On Continue, contacts that are already verified aren't asked again.

## Boundaries & Constraints

**Always:**
- Decision (user, 2026-10-13): email-first flow, replacing 2.5's step-1-submit resume.
- Decision (user, 2026-10-13): the check runs when the email field is left, and the page updates in place (htmx). Without JavaScript, Continue runs the same check.
- Decision (user, 2026-10-13): resume is by email only. A mobile that's in another signup in progress is allowed. When an account is created, every other pending registration with its email or mobile is deleted, and their open attempts end with outcome `abandoned`.
- `PendingRegistration` records when the email was verified. The resume code sets it, and so does a correct email-step code. Saving step 1 with a different email clears it. A step whose contact is already verified is skipped; when no step is left, the account is created.
- During review, the password fields may stay empty, which keeps the saved hash. When they're filled in, every password rule applies.
- The live email check:
  - **Registered email:** shows the recovery note.
  - **Pending match:** shows the resume offer and logs `pending_found` (field `email`, reason `email pending`, the email in the encrypted data). An attempt is started if there is none.
  - **No match:** shows nothing and logs nothing.
- Nothing from the matched registration is shown until the code is correct. The session holds only the row id. A resume keeps the old row's `created_at`, as 2.5 does.
- `registration-failure-policies.md`: the pending row now reads "Email in a pending registration: warn and offer to resume after a code to that email; the code also verifies it. A mobile in a pending registration is allowed; the other registration is removed when an account is created", with reason `email pending`. The page-closed row says the email starts the resume.
- English and Spanish for every new or changed text.

**Never:**
- Resume through a mobile code; `phone pending` is no longer used.
- The mobile code, resend limits or attempt limits (entry 7). Rate limits (entry 9).

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| Live match | Email of an active pending row typed, field left | Resume offer appears under the email, nothing else revealed; `pending_found` | No error expected |
| Live registered | A user's email typed, field left | Recovery note appears | No error expected |
| No JavaScript | Full step 1 with a pending email, Continue | Same offer page as the live match | No error expected |
| Resume | Offer accepted, correct code | New row (old data, old `created_at`, email verified), old row deleted, its attempt `abandoned`. Step 1 shows the saved data, password fields empty | No error expected |
| Review and continue | Resumed, nothing changed, Continue | No email code is sent; the account is created and the person is logged in | No error expected |
| Email changed in review | Resumed, a new email typed, Continue | Email verified mark cleared; the email code step runs | No error expected |
| New password in review | Password fields filled | Validated as usual; the new hash is stored | No error expected |
| Wrong resume code | Bad code | Error; nothing revealed; `rejected`, reason `OTP verification failed` | No error expected |
| Mobile in another pending | New email, mobile of an active pending row | Step 1 saves; at account creation that row is deleted and its attempt ends `abandoned` | No error expected |
| Email step code | Normal signup, correct email code | Email marked verified; account created | No error expected |

</frozen-after-approval>

## Code Map

- `signup/views.py` (from 2.5) -- `signup()`:
  - the step-1 pending check (`find_pending_match`) becomes email-only;
  - after a step is saved, skip steps whose `is_done(pending)` is true, and create the account when none is left;
  - the `resume` view, `_take_over` and `start_resume`/`clear_resume` are reused. After the code, redirect to step 1 (`step_url(0)`), not the next step, and set the email verified mark on the new row;
  - `_create_account`: after `pending.delete()`, delete the other rows matching the email or phone, and end their attempts after the commit.
- New view `signup/check-email/` (POST, htmx). It runs the email checks of `AccountForm.clean_email` (registered, disposable not needed) and `find_pending_match`, and returns a partial. Add its URL before `signup/<slug>/`.
- `signup/steps.py` -- add `Step.is_done(pending)` (default False). `EmailCodeStep.is_done` is true when the email is verified, and its `save` sets the mark. In `AccountStep.save`, clear the mark when the email's index changes. Password fields are optional when `pending` has a hash (pass that as a form kwarg).
- `signup/forms.py` -- in `AccountForm`, field order puts `email` first. Make the passwords optional when there is a saved hash (`clean()` validates only when filled). Remove the mobile channel from resume.
- `signup/models.py` -- `email_verified_at` (null). `copy_to_new_row` sets it. Migration `signup/0007`.
- Templates:
  - `step_account.html`: email first. The email input gets `hx-post` to the check URL with `hx-trigger="change"`, a target div below it, and the CSRF token through `hx-headers` or the form.
  - New partial `signup/_email_check.html`: the offer with a "Resume" button (POST `action=send` to `signup_resume`), or `_recovery_note.html`.
  - `resume.html`: email only.
- htmx is loaded by `templates/base.html` and is not yet used anywhere; vendored at `static/js/htmx.min.js`.
- `spec-client-portal-signup/registration-failure-policies.md` -- update the two rows as stated above.
- `locale/es/LC_MESSAGES/django.po` -- strings.
- Tests: `signup/tests/test_duplicates.py` -- rewrite the mobile-resume and land-on-next-step tests to the new matrix. Keep the CSRF-token helper.

## Tasks & Acceptance

**Execution:**
- [x] `signup/models.py`, migration -- email verified mark.
- [x] `signup/steps.py`, `signup/forms.py`, `signup/views.py`, `signup/urls.py` -- skip verified steps, optional password in review, email check endpoint, email-only resume to step 1, clean-up at creation.
- [x] Templates -- email first, live check partial, resume page.
- [x] `registration-failure-policies.md`, `locale/es/LC_MESSAGES/django.po` -- policy rows, Spanish.
- [x] `signup/tests/` -- every matrix row; update the 2.5 tests the new flow changes.

**Acceptance Criteria:**
- Given staging with a signup closed at the email code step, when a person types the same email in a private window and leaves the field, then the resume offer appears. One code from the journal reloads the data into step 1, and Continue creates the account with no second code.
- Given CI on PostgreSQL, when the suite runs, then every test passes.

## Implementation Notes

- No JavaScript: Continue with a pending email re-renders step 1 with the same offer partial under the email (status 200), not a redirect to the resume page. The Resume button posts the step 1 form (with its CSRF token) to `signup_resume` via `formaction`, so it works with and without htmx.
- The live check sends only `email` and `csrfmiddlewaretoken` (`hx-params`), never the passwords. It also clears an earlier resume offer when the email no longer matches.
- After a resume, step 1 shows a "registration restored" message (new string, EN/ES). The password help text explains that empty fields keep the saved password.
- The optional passwords also apply on Back from step 2, since that row has a saved hash too. Once one password field is filled in, both are required.
- At account creation, every other pending row with the email or mobile is deleted, whether or not it is still active.

## Plan Change Log

## Review Triage Log

**Pass 1 (quick lens, 2026-10-13):** high 0, medium 2, low 3, false 0, maybe-false 0. Three patches; two findings rejected.

| # | Finding | Verdict | Route | Evidence |
|---|---------|---------|-------|----------|
| 1 | The Resume button in the offer becomes the step 1 form's default button, so Enter in any field sends a resume code | medium | patch | Real: it is a submit button inside the step 1 form, placed before Continue. Fix: a separate resume form outside step 1, which the button names with `form=`, plus a test. |
| 2 | After a resume the new row keeps step `verify-email`, so `/signup/verify-email/` renders "we sent a code" with no code sent and the email already verified | low | patch | Real, reachable by URL. Direct correction: `_take_over` sets the new row to step 1, plus a test. |
| 3 | Creating an account deletes other pending registrations by an unverified mobile | low | rejected | This is the user's decision 2a. The mobile is verified before account creation from entry 7, which closes the gap. |
| 4 | The spec's CAP-3 and `signup-fields.md` still describe resume by mobile, and a mobile unique across pending registrations | medium | patch | Real: they contradict the updated policy row, and later entries read them. Fix: reword both to the user's decision. |
| 5 | The live check and a fast Continue can log `pending_found` twice | low | rejected | A duplicate event is harmless for reconstructing the attempt. Fixing it needs request syncing or a guard. |

## Verification

**Commands:**
- `DATABASE_URL="sqlite:///$TMPDIR/x.sqlite3" ../.venv/bin/python -m pytest -q` (with test keys) -- expected: only the 3 known PostgreSQL-only tests fail.
- `manage.py makemigrations --check`, `manage.py compilemessages` -- expected: no changes; compiles.

Done locally (2026-10-13) after the review patches, on SQLite: `pytest` 544 passed, 6 skipped (PyYAML), 3 failed only because those tests use PostgreSQL-only raw SQL. `makemigrations --check` reports no changes; `compilemessages` is up to date. CI on PostgreSQL 16 (PR #17): 553 passed.

Staging (2026-10-13): the merge deployed and ran migration `signup.0007`. The checks:
- A signup closed at the code screen was found from a private window as soon as the email field was left, so htmx sent the CSRF token.
- One resume code brought the saved data back into step 1, with the password fields empty.
- Continue created the account and logged the person in with no second code.
- In the admin, the old attempt was `abandoned`, and the new one showed `pending_found`, `code_sent`, `resumed`, `step_saved` and `completed`.
