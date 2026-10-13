---
title: 'Legal acceptance and commercial consent'
type: 'feature'
ticket: '6'
created: '2026-10-13'
status: 'done'
baseline_revision: '0f819dce1e0b12190236c6fe2214fdf25a2108a3'
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

**Problem:** The wizard goes from step 1 straight to the email code. No Doctor accepts the Terms or the Conditions, sees the Ormco TPS contract, or gives commercial consent, and there's no record to use as evidence (CAP-4, CAP-13).

**Approach:**
- Add a legal step between step 1 and the email code. It shows the current version of four documents, each editable in the admin as new versions, in English and Spanish:
  - Terms, which must be accepted;
  - Conditions, which must be accepted;
  - the Ormco TPS contract, unticked and optional;
  - commercial consent, unticked and optional.
- Store each choice, ticked or not, with its timestamp and the document version shown. Move the choices to the user when the account is created.

## Boundaries & Constraints

**Always:**
- A new `legal` app with two models:
  - `LegalDocument`: kind (terms, conditions, ormco_tps, commercial_consent), version, English and Spanish title and body, `published_at`;
  - `LegalChoice`: document version, accepted yes/no, `decided_at`, and either a pending registration (CASCADE) or a user (PROTECT).
- The current version of a kind is the latest with `published_at` in the past. Publishing a new version in the admin applies at once, with no deploy.
- A version that's published can't be edited or deleted in the admin; a change means a new version. The version number is set automatically (previous + 1 per kind).
- A data migration publishes version 1 of each kind with draft texts, clearly marked as drafts. The commercial-consent text names MyTPDSO Limited (Ireland), MyTPDSO Spain SL (Spain) and MyTPDSO LLC-FZ (UAE), the channels email, WhatsApp, SMS and phone, and that the data is never shared outside the group.
- Ormco and consent are never pre-ticked, including when the step is refilled with Back. Signup completes whether they are ticked or not.
- Saving the step replaces the pending registration's choices. Back refills them.
- The step is skipped (`is_done`) when the pending registration already holds choices for the current Terms and Conditions versions. A resume copies the choices to the new row.
- At account creation, inside the transaction, the choices move from the pending row to the user.
- The event log's `step_saved` records each kind, version and accepted flag (no personal data). A missing Terms or Conditions acceptance is logged as `rejected` with code `required`.
- English and Spanish for every label and message. The document shown is in the page language.
- Decision (user, 2026-10-13): document bodies are Markdown. They are rendered with Python-Markdown and then sanitized with `nh3` (headings, paragraphs, lists, emphasis, links, tables; no scripts, styles or raw HTML that survives). Both libraries are pinned in `requirements.txt`.
- Decision (user, 2026-10-13): version 1 of each document is a short placeholder written by us, in English and Spanish, clearly marked "Draft" until the lawyer's texts replace it in the admin.

**Never:**
- Withdrawing consent after signup (profile settings). Final legal texts (from the lawyer). The email or mobile code changes (entry 7).
- Editing a published version in place.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| All required | Terms and Conditions ticked, Ormco and consent not | Saved: 4 choices (2 true, 2 false), each with version and `decided_at`; next step is the email code | No error expected |
| Terms missing | Conditions ticked only | Error on Terms; nothing stored; `rejected`, field `terms`, reason `required` | No error expected |
| Conditions missing | Terms ticked only | Error on Conditions | No error expected |
| All ticked | All four ticked | 4 choices true | No error expected |
| Unticked by default | First visit | Ormco and consent unticked | No error expected |
| New version published | Admin adds Terms v2 (published now) | Step shows v2 at once; a new choice stores v2 | No error expected |
| Draft version | Terms v3 with `published_at` in the future | Not shown; v2 is current | No error expected |
| Published locked | Admin opens a published version | Read-only, no delete | No error expected |
| Spanish | `/es/` | Spanish titles, bodies, labels and errors | No error expected |
| Account created | Steps done | The user's choices exist with the same versions and times; the pending row is gone | No error expected |
| Resume | Resumed signup that had done this step | Choices copied; Continue from step 1 skips the legal step and the verified email step | No error expected |

</frozen-after-approval>

## Code Map

- `signup/steps.py`:
  - `STEPS` becomes `[AccountStep(), LegalStep(), EmailCodeStep()]`.
  - Follow `Step`: `form_class`, `template_name`, `get_initial`, `save`, `log_data`, `is_done`. `log_fields` holds only non-personal values, so a `log_data` override returns kind, version and accepted.
  - `AccountStep.save` stays as it is. `EmailCodeStep.enter` sends the code when the wizard moves in.
- `signup/views.py`:
  - the generic loop already skips done steps and creates the account when none is left;
  - `_create_account`: move the choices inside the transaction, before `pending.delete()`;
  - `_take_over`: copy the choices to the new row.
- `signup/forms.py` -- the new `LegalForm` (`StyledForm`, but checkboxes need their own classes, not `FIELD_CLASSES`). Each field's label and document come from the current versions.
- `signup/templates/signup/` -- new `step_legal.html`, extending `wizard.html`. Each document's body sits in a `<details>` (or a scroll box) beside its checkbox, rendered from Markdown and sanitized (one helper in `legal/`, also used by the admin preview if one is added).
- New `legal/` app (models, admin, migrations `0001` and the data migration `0002`, apps). Register it in `mytreatplan/settings.py` `INSTALLED_APPS` and in `pytest.ini` `testpaths`.
- The admin follows `eventlog/admin.py` for read-only behaviour on published versions (`has_change_permission`/`has_delete_permission` per object).
- Public-site `terms`/`privacy` pages (`mytp_publicsite`) are the website's own texts. Don't touch them.
- `locale/es/LC_MESSAGES/django.po` -- the new UI strings. The document texts live in the database, not the `.po` file.
- Tests:
  - `signup/tests/test_wizard.py`, `test_event_log.py`, `test_step1.py` and `test_duplicates.py` walk the flow step 1 → email code. Add the legal step in their helpers (a `_legal(client)` helper next to `_step1`).
  - New `legal/tests/` and `signup/tests/test_legal_step.py`.

## Tasks & Acceptance

**Execution:**
- [x] `legal/` -- models, admin (published read-only, auto version), migrations including the draft texts.
- [x] `signup/forms.py`, `signup/steps.py`, `signup/views.py` -- the legal step, choice storage, skip, resume copy, move to the user.
- [x] `signup/templates/signup/step_legal.html` -- checkboxes and document texts.
- [x] `mytreatplan/settings.py`, `pytest.ini`, `locale/es/LC_MESSAGES/django.po`.
- [x] Tests -- every matrix row; update the existing flow helpers.

**Acceptance Criteria:**
- Given staging after the merge, when the admin publishes a new Terms version, then the next signup shows it with no deploy, and the user's stored choice carries that version.
- Given CI on PostgreSQL, when the suite runs, then every test passes.

## Implementation Notes

- `legal/models.py`: `LegalDocument.objects.current(kind)` / `current_by_kind()` read the latest version with `published_at <= now` on every request. `version` is set in `save()` (previous + 1 per kind, unique per kind). `LegalChoice` has a check constraint: exactly one of `pending_registration` (CASCADE) and `user` (PROTECT); the document FK is PROTECT too.
- `legal/rendering.py` `render_markdown`: Python-Markdown (`tables`) with raw HTML switched off (typed HTML shows escaped), then `nh3` with the listed tags, `href`/`title` on links only, http/https/mailto, `rel="noopener noreferrer nofollow"`. Used by the step and by the admin preview.
- Admin: a published version (`published_at` passed) is view-only and cannot be deleted; drafts (no date or a future one) can be edited or deleted; `kind` is fixed once saved; bulk actions are off so the bulk delete cannot bypass the rule. Choices are read-only in the admin.
- Back refill: read as "Terms and Conditions accepted in their current version come back ticked; Ormco and consent always start unticked" (the Always rule names Back explicitly). Choices for an older version are not refilled.
- `is_done`: the Terms and Conditions current versions are both accepted. So Back to step 1 and Continue skips the legal step, unless a new Terms or Conditions version was published meanwhile (then it is asked again).
- Event log: `step_saved` (and `rejected`) data for the legal step is `{kind: {version, accepted}}`; a missing required box logs `rejected`, field = kind, reason `required`.
- Data migration `0002_draft_documents`: v1 of each kind published at migration time, titles end in "(draft)/(borrador)", bodies start with "**Draft.**/**Borrador.**". Reverse deletes the v1 rows (blocked by PROTECT once choices point at them).
- `Markdown==3.11` and `nh3==0.3.7` pinned in `requirements.txt`.

Verification run (2026-10-13): SQLite suite 586 passed, 3 failed (the known PostgreSQL-only `accounts/tests/test_crypto.py` tests); a throwaway local PostgreSQL 17 cluster: 589 passed, `migrate legal zero` and back OK; `makemigrations --check` no changes; `compilemessages` OK; `check` no issues; Tailwind build includes the new classes.

## Plan Change Log

## Review Triage Log

**Pass 1 (quick lens, 2026-10-13):** high 1, medium 1, low 2, false 0, maybe-false 0. Three patches; one finding rejected.

| # | Finding | Verdict | Route | Evidence |
|---|---------|---------|-------|----------|
| 1 | A pending registration saved before the deploy (step `verify-email`, no choices) can create an account with no Terms or Conditions acceptance | high | patch | Real: the wizard checks only `index > current`, and those rows are already at the code step. Fix: a data migration moves such rows back to the legal step, with a test. An account created after a new Terms version keeps the version that was accepted, which is the correct evidence. |
| 2 | The stored version can be one the Doctor never saw (a version published between showing the page and submitting it) | medium | patch | Real: `LegalForm` is rebuilt from the current versions on POST. Fix: the page carries the shown document ids. A form whose ids are no longer current is refused and shown again with the new versions, with a test. |
| 3 | Version numbering can collide when two admins save the same kind at once (500 on the unique constraint) | low | rejected | One superuser edits these texts. The constraint keeps the data correct. A fix would need row locking. |
| 4 | `requirements.txt` swaps the two argon2 lines | low | patch | Direct correction: restore the original order. |

## Verification

**Commands:**
- `DATABASE_URL="sqlite:///$TMPDIR/x.sqlite3" ../.venv/bin/python -m pytest -q` (with test keys) -- expected: only the 3 known PostgreSQL-only tests fail.
- `manage.py makemigrations --check`, `manage.py compilemessages` -- expected: no changes; compiles.

Done locally (2026-10-13) after the review patches, on SQLite: `pytest` 589 passed, 6 skipped (PyYAML), 3 failed only because those tests use PostgreSQL-only raw SQL. Before the patches, the implementer's run on a throwaway PostgreSQL 17 passed all 589 tests. `makemigrations --check` reports no changes; `compilemessages` is up to date. CI on PostgreSQL 16 (PR #18): 598 passed.

Staging (2026-10-13): the merge deployed and ran migrations `legal.0001`, `legal.0002` and `signup.0008`. The checks:
- The legal step showed the four draft documents, with Ormco and consent unticked.
- Continue with nothing ticked showed errors on Terms and Conditions.
- Terms v1 was read-only in the admin, with no delete. A new Terms published now became v2, and the next signup showed it with no deploy.
- The stored choices carried Terms v1 for the first user and v2 for the second, with Ormco and consent recorded as not accepted.
- Kept as built: after Back, only Terms and Conditions come back ticked; the checkbox labels remain placeholder wording until the lawyer's texts.

