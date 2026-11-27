---
title: 'Patient list and file with stages, history and comments'
type: 'feature'
ticket: '4'
created: '2026-11-27'
status: 'done'
baseline_revision: '3ebd81e0546e5d3a1655f2e593736482c6706123'
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

**Problem:** The patient list has no stage. The patient file shows only the current case, with no comments, no history and no way to set a patient inactive (L1, L2, L11). Later stories need places on the file for case actions (10.5, 10.8, 10.9) and appointments (epic-appointments).

**Approach:** Add a stage column and a row highlight for inactive patients to the list. On the file add: the editable comments, an active/inactive control, the history of every service with its dated stage changes, and two named slots ("case actions", "appointments") that other apps fill through a small registry. Both writes go through new patient service functions.

## Boundaries & Constraints

**Always:**
- Only the patient's own doctor sees and edits; another doctor's or an erased patient is a 404.
- Writes go through `patients/service.py` (AD-15): lock the row, check `version` (stale → a message to reload), raise it by one, store the actor (`doctor_actor`). Forms are POST plus CSRF, then redirect with a message, as the portrait.
- Inactive keeps today's meaning: still listed, highlighted, and no new orders (the existing `status=ACTIVE` filters). Reactivating is allowed any time.
- List stage = the file's current case (`current_work_order`: live, else latest), fetched for all rows in a fixed number of queries, not one per row. A patient with no case shows "—".
- History: services newest first, each with its product name (or code), current stage and its `StageChange` rows (date, translated label). Labels in EN and ES.
- Comments: max 2000 characters, encrypted as today, never logged.
- Slots render nothing when no app has registered content.

**Never:**
- A stage column in the cart wizard's chooser (mode "choose").
- Backfilling `StageChange` rows for older work orders.
- Fixing the portrait's missing version/actor (deferred).
- Building any case action or appointment content.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| List | patients with a live case, a finished case and none | stage labels shown, "—" for none | — |
| Inactive in list | patient `inactive` | row highlighted, status pill kept | — |
| Two services | cancelled ICE + live planning | both listed, newest first, each with its dated stage changes | — |
| Older service | work order created before 10.1, no rows | creation date and current stage, with "Earlier stage changes were not recorded" | — |
| Edit comments | doctor saves new text | stored, version +1, actor set, message shown | — |
| Stale edit | version moved meanwhile | nothing saved, "changed meanwhile, reload" | message |
| Too long | 2001 characters | refused with the form error | form error |
| Set inactive / active | doctor toggles | status changes; inactive patient refused by the cart wizard as today | — |
| Other doctor | POST to another doctor's patient | 404, nothing changed | — |
| Erased patient | GET or POST | 404 | — |

## Decisions

- Decision (user, 2026-11-27): setting a patient inactive is allowed while a case is live. The case carries on, and only new orders are refused.
- Decision (user, 2026-11-27): a service from before 10.1 shows its creation date and current stage, with the note "Earlier stage changes were not recorded".
- Decision (user, 2026-11-27): comments are edited on the patient file, in a textarea with Save.

</frozen-after-approval>

## Code Map

- `patients/views.py:54-71` `patient_list`, `:89` `_own_patient`, `:99-106` `_case_of`, `:113-144` `patient_page`: its POST is the portrait's, so add separate views for comments and status.
- `patients/templates/patients/_patient_chooser.html` -- list table shared with the cart wizard (mode "list"/"choose"); status pill at :46; no-match colspan at :56. `static/js/patient-list.js` does search and paging in the browser.
- `patients/templates/patients/patient.html` -- sections: header (:18), Case (:20-25, `data-case*` attributes), Portrait (:27-45). Styles: h2 `mt-10 text-[22px] font-black`, callout `rounded-12 border border-notice-border bg-notice-bg`.
- `patients/models.py:41-66` -- `status` ACTIVE/INACTIVE/ERASED, `comments` EncryptedCharField(2000), `version`, actor fields; `StageChange` (:129-163, related_name `stage_changes`).
- `patients/service.py` -- `current_work_order` (:176), `patients_of` (:159), `StaleVersion` (:82), `_check_actor` (:106); `payments.service.doctor_actor(user)`.
- `patients/portrait.py:120-178` -- view pattern (login, csrf_protect, never_cache, require_http_methods, `_locked_patient`).
- `patients/stages.py` `stage_label`; product name via `catalogue.resolver.product(id).public_name`.
- Tests: `patients/tests/test_list.py`, `patients/tests/test_views.py:161-187`. `test_the_file_shows_the_live_case_before_a_finished_one` asserts `'Cancelled' not in page`, which must be rewritten now that every service is listed.

## Tasks & Acceptance

**Execution:**
- [ ] `patients/service.py` -- `update_comments(patient, text, expected_version, actor)` and `set_active(patient, active, expected_version, actor)`; `current_work_orders(patients)` (bulk, for the list).
- [ ] `patients/slots.py` -- `register(slot, name, template, context=None)`, `unregister`, `render_context(slot, request, patient)`; slots `case_actions` and `appointments`.
- [ ] `patients/forms.py`, `patients/views.py`, `patients/urls.py` -- comments and status POST views (`patients/<code>/comments/`, `patients/<code>/status/`); `patient_page` adds history and slot context; `patient_list` adds the stage per row.
- [ ] `_patient_chooser.html`, `patient.html` -- stage column (list mode only) and inactive row highlight; comments form, status control, "Services" history, the two slots (`data-slot="case_actions"`, `data-slot="appointments"`).
- [ ] `locale/es/LC_MESSAGES/django.po` -- new strings.
- [ ] `patients/tests/` -- one test per matrix row, the slot registry, a query-count bound on the list, and comments never in logs.
- [ ] `deferred-work.md` -- the portrait writes without version/actor.

**Acceptance Criteria:**
- Given a dev patient with two work orders, when the doctor opens the list and the file, then the list shows the current stage and the file shows both services with dated stage changes.
- Given the doctor edits the comments and sets the patient inactive, when the list reloads, then that row is highlighted and the cart wizard no longer offers the patient.

## Verification

**Commands:**
- This checkout is the worktree `../mytreatplan-10-4`, built in parallel with 10.2. Run everything from it with the sandbox off and these non-secret test values: `export SECRET_KEY=ci-only-not-a-secret DEBUG=False SECURE_SSL_REDIRECT=False ALLOWED_HOSTS=localhost,127.0.0.1,testserver FIELD_ENCRYPTION_KEYS="test1:dGVzdC1vbmx5LWZpZWxkLWtleS1ub3Qtc2VjcmV0ISE=" BLIND_INDEX_KEY="dGVzdC1vbmx5LWJsaW5kLWluZGV4LWtleS0zMmJ5dGU=" DATABASE_URL="sqlite:///$TMPDIR/wt104.sqlite3"`. Run `compilemessages` after editing the `.po`.
- Portal suite: `../.venv/bin/python -m pytest -q -p no:cacheprovider --tb=line` -- expected: 0 failures besides the known WeasyPrint ones.
- `../.venv/bin/python manage.py makemigrations --check --dry-run` -- expected: no changes.

## Implementation Notes

- New service functions `update_comments`, `set_active` and `current_work_orders` (one query for the list); `patients/slots.py`; POST views `patients/<code>/comments/` and `patients/<code>/status/`; the file template's Services history, Comments form, status control and slot wrappers. The existing test asserting `'Cancelled' not in page` was rewritten for the Case section only.
- A slot renders no wrapper at all when nothing is registered. Entries are included with their context as `slot`.
- "Not recorded" means a service with no stage-change rows, or one whose first row is not a creation row (`from_stage` empty). Such a service always shows "Created <date> — <current stage>" and the note.
- Verification after the patches: portal suite 4695 passed, 17 skipped, 11 failed (the known WeasyPrint OSError in invoices/tests/test_pdf.py and test_pending_documents.py); `makemigrations --check` clean.
- After review: the inactive row highlight is `bg-ink/5`, so the pill stays visible. Comments normalise CRLF before the 2000 limit (the textarea counts a line break as 1). A bad `version` on the status form says "The change could not be saved". Module docstrings were updated.

## Plan Change Log

## Review Triage Log

Pass 1 (quick lens, Opus): 7 findings — high 0, medium 1, low 6, false 0; patch 6, reject 1.

| # | Finding | Verdict | Route | Evidence / action |
|---|---------|---------|-------|-------------------|
| 1 | A pre-10.1 service that moved since loses its creation date | medium | patch | The template showed the creation line only with no rows; the user's decision asks for it with the note. Shown whenever `recorded` is false; test extended. |
| 2 | Inactive row highlight hides the inactive pill | low | patch | The row and the pill shared `bg-notice-bg`. Row now `bg-ink/5`. |
| 3 | CRLF line breaks push an accepted comment over 2000 | low | patch | The browser counts a line break as 1 for `maxlength`, but it is posted as CRLF. Normalised in `clean_comments`; test added. |
| 4 | Status form with a bad version says "changed meanwhile" | low | patch | Untrue message; replaced with "The change could not be saved…" (EN/ES); test added. |
| 5 | Comments form with a bad version re-renders silently | low | reject | `version` is a hidden field the page always fills; only a tampered form reaches this, and the fix adds a branch. |
| 6 | Module docstrings and template header out of date | low | patch | Updated in `views.py`, `service.py` and `patient.html`. |
| 7 | PEP 8 E305 after the `slot_template` fixture | low | patch | Blank lines added. |


## Staging

- **Merge:** PR #157 → 7d6d05c, after merging master (10.2) into the branch. Both sets of Spanish strings were kept, and "stage" is "Fase" in the email too. CI passed on both PR runs (45975598194, 45979874433). Master run 45982169255 passed: tests, then dev and dev-ae deployed. Local suite: 4695 passed, 17 skipped, 11 local-only WeasyPrint failures.
- **Checks on dev (2026-11-27),** run over SSH as doctor pk 18 through the test client, each in one transaction rolled back:
  1. A patient with two work orders shows the stage `pending_appointment` in the list. The file shows 2 services, 2 dated changes and 2 "not recorded" notes (both cases predate a creation row), in `/en/` and `/es/`. Passed.
  2. Saving comments returned 302, version 2, actor `doctor`. Set inactive returned 302, and the list row is highlighted (`bg-ink/5`). The cart wizard no longer offers the patient. Set active again returned 302. Passed.
  3. A patient of another doctor (made inside the rolled-back transaction) answered 404 to the file and to both write routes, and nothing changed. Passed.
- **Left on dev:** nothing.
