---
title: 'Fix the resume takeover'
type: 'bugfix'
ticket: ''
created: '2026-10-23'
status: 'done'
baseline_revision: '4e857e6f3deeb0d133a5831d75ae3af7349d8153'
route: 'oneshot'
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

**Problem:** The signup retrospective (`epic-client-portal-signup/epic-client-portal-signup-retrospective.md`, finding R1, action item 1) found this:
- Anyone can create a pending registration for someone else's unregistered email, choosing its password and its legal choices (Terms, Conditions, Ormco TPS, commercial consent).
- The email's owner can then only resume it, after a code to the email.
- The resume (`signup/views.py` `_take_over`) copies the old row's password hash (`PendingRegistration.RESUMED_FIELDS` includes `password`) and every legal choice with its original time (`legal_choices.copy_to`). `LegalStep.is_done` then skips the legal step.
- The account is created with Terms and consent the owner never gave and, if the password fields are left empty, a password the other person knows.

The resume code proves control of the email, not who made the row.

**Approach (user, 2026-10-23):**
- A resume no longer carries over the old row's password or its legal choices.
- The resumed registration has no password, so step 1 requires both password fields (`has_password` is false).
- It has no legal choices, so the legal step is shown again, with nothing ticked.
- The rest is restored as today, for review in step 1: the email (marked verified by the code), names, country, mobile and how-found.
- The old row and its choices are deleted, as today.
- Tests cover a resume of a row someone else created: the password must be set; the legal step is shown, unticked; the account's choices are only the resuming person's, with their own times. The tests that relied on the old behaviour are updated, including the browser tests for policy rows 5–7.

</frozen-after-approval>

## Implementation Notes

- Oneshot route: a two-line behaviour change plus test updates.
- `signup/models.py`: `RESUMED_FIELDS` no longer includes `password`, so a resumed row has no hash and step 1 requires both password fields (`AccountStep.get_form_kwargs` → `has_password=False`).
- `signup/views.py` `_take_over`: the legal choices are no longer copied, so the old row's choices are deleted with it (CASCADE) and `LegalStep.is_done` is false. The comments are updated.
- `legal/models.py`: `LegalChoiceQuerySet.copy_to` had no other caller, so it and the custom manager are removed. No migration change.
- `signup/forms.py`: the `has_password` docstring now says a resumed signup has no saved hash.
- Tests:
  - `signup/tests/test_duplicates.py`: the restore test asserts no password and no legal choices; `_review` sets a password; review flows go through the legal step. New `test_resume_of_a_row_someone_else_made_keeps_none_of_their_choices`: another person's row, with TPS and consent ticked, is resumed by the owner. The password is required, the legal step is unticked, and the account has only the owner's password and choices, with no time from the other person's.
  - `signup/tests/test_legal_step.py`: `test_resume_after_the_legal_step_asks_it_again` replaces the test that asserted the skip.
  - `signup/tests/test_cancel_idle.py`, `test_mobile_code.py`: resume flows updated.
  - `signup/tests/browser/test_failure_policies.py` row 6: password set again, legal step unticked, then the mobile code.
- Results (SQLite, test keys, browser outside the sandbox): the full suite gives 1,281 passed (one more test), 3 failed (the known PostgreSQL-only tests). `pytest -m browser`: 29 passed on 2 runs. `makemigrations --check`: no changes.

## Review Triage Log

**Pass 1 (quick lens, 2026-10-23):** high 0, medium 1, low 3, false 0, maybe-false 0. Three patches (four findings); one deferred. No behavioural bug in the fix.

| # | Finding | Verdict | Route | Evidence |
|---|---------|---------|-------|----------|
| 1 | `signup/steps.py:116` comment still says a resumed signup keeps a saved hash | low | patch | Comment corrected. |
| 2 | `LegalStep` docstring still says the choices "are copied by a resume (_take_over)" | low | patch | Docstring now says a resume never copies them. |
| 3 | Browser rows 5 and 7 stopped at step 1; row 5 is the takeover scenario itself | medium | patch | Row 5 now checks there's no "keep the password" text, that an empty password is refused, and that the legal step is unticked after a password is set. Row 7 checks the password must be set again. |
| 4 | Two blank lines left in `LegalChoice` after removing the manager | low | patch | One blank line. |
| 5 | The signup spec (CAP-3, failure-policy rows 5 and 6) still says a resume restores the saved data | low | defer | Spec edits belong to `bmad-spec`. Entry added to `deferred-work.md`, part of the retrospective's action item 8. |

After the patches (2026-10-23): the full suite gives 1,281 passed, 3 failed (the known PostgreSQL-only tests); browser rows 5–7 pass. `makemigrations --check`: no changes.

## Verification

**Commands:**
- The local suite command (test keys, SQLite), outside the sandbox for the browser tests. Expected: only the 3 known PostgreSQL-only tests fail.

## Staging

- PR #40 merged on 2026-10-23. The master CI run 39939848164 passed (1,284 tests on PostgreSQL 17) and deployed to staging.
- The user closed the plan on 2026-10-23 without reporting the manual takeover check from the PR description. That check (resume another browser's row: the password is required and the legal step is unticked) was not confirmed in this session.
