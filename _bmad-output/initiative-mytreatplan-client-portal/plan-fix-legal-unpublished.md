---
title: 'Fix: no signup without published Terms and Conditions'
type: 'bugfix'
ticket: ''
created: '2026-10-23'
status: 'built'
baseline_revision: 'd92aec06d7060bf68ac91d468f1865cad1f6cb04'
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

**Problem:** The signup retrospective (finding S1, action item 2; QA A1) found that `LegalForm` (`signup/forms.py`) adds a required checkbox only for a Terms or Conditions version that is published. With no published Terms or Conditions version, the legal step is valid with nothing ticked, and the account is created without the acceptance that signup CAP-4 requires. Migration `legal 0002` seeds version 1 of each document, and published versions can't be deleted in the admin, so this needs a database without them (for example a production database seeded another way).

**Approach:**
- **Legal step:** while a required kind (Terms, Conditions) has no published version, `LegalForm` refuses every submission with a non-field error (code `documents_missing`): "Signup is not available right now. Please try again later." Nothing is saved, and the refusal is logged as `rejected` like any form error. The server log gets an error naming the missing kinds, so operators see it.
- **Account creation:** under the creation lock, `_create_account` checks again that the pending registration has an accepted Terms choice and an accepted Conditions choice (any version). If not, no account is made: the row goes back to the legal step and the browser is redirected there.
- **Tests:** a missing Terms or Conditions version refuses the legal step with the message, in English and Spanish; nothing is saved; and a row that reaches creation without the required acceptances goes back to the legal step with no user created.

</frozen-after-approval>

## Implementation Notes

- Oneshot route: a form check, a creation guard, one Spanish string and tests, well under 100 lines.
- The creation guard accepts any version, not only the current one, so a version published after the legal step doesn't change today's behaviour (that is the decided "asked again when Back reaches step 1" rule).

- Files: `signup/forms.py` (`LegalForm.clean`, `documents_missing`), `signup/views.py` (`_LegalNotAccepted` in `_create_account`, logged `rejected`/`legal_not_accepted`), `locale/es/LC_MESSAGES/django.po` (one string), `signup/tests/test_legal_step.py` (three tests, two parametrized).
- Results (SQLite, test keys, browser outside the sandbox): the full suite gives 1,287 passed and 3 failed (the known PostgreSQL-only tests). `makemigrations --check`: no changes.

## Review Triage Log

**Pass 1 (quick lens, 2026-10-23):** high 0, medium 1, low 4, false 0, maybe-false 0. Three patched, two rejected.

| # | Finding | Verdict | Route | Evidence |
|---|---------|---------|-------|----------|
| 1 | The creation guard records nothing | medium | patch | It now logs `rejected` (field `legal`, reason `legal_not_accepted`) on the attempt; the test checks it. |
| 2 | The guard redirects with no message | low | reject | A state the legal form can no longer produce; a message adds a new string and template work for it. |
| 3 | `documents_missing` is shown and logged with the `required` errors | low | patch | The field errors are cleared, as `documents_changed` does; the test ticks nothing and checks one `rejected` event. |
| 4 | The guard is tested for Terms only | low | patch | Parametrized over Terms, Conditions and no choices at all. |
| 5 | Spanish is tested for Terms only | low | reject | One message for both kinds; English covers both. |

## Verification

**Commands:**
- The local suite (test keys, SQLite) -- expected: only the 3 known PostgreSQL-only tests fail.
- `makemigrations --check` -- expected: no changes.
