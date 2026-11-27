---
title: 'Patient list and file with stages, history and comments'
type: 'feature'
ticket: '4'
created: '2026-11-27'
status: 'in-progress'
baseline_revision: '3ebd81e0546e5d3a1655f2e593736482c6706123'
route: 'full'
route_source: 'auto'
risk: 'medium'
review: ''
review_source: ''
lenses_ran: []
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
- Portal suite (per the local dev notes) -- expected: 0 failures besides the known WeasyPrint ones.
- `../.venv/bin/python manage.py makemigrations --check --dry-run` -- expected: no changes.

## Implementation Notes

## Plan Change Log

## Review Triage Log

