---
title: 'Patient list and portrait'
type: 'feature'
ticket: '2'
created: '2026-11-22'
status: 'done'
baseline_revision: 'e60c3c70e7878e7eabcc471c284a807c6a554b2d'
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

**Problem:** 9.1 gives a doctor a bare patient list (code, name or reference, created) and no way to put a face to a patient. The doctor needs the list with each patient's status, and an optional portrait that never reaches the screen before it has been virus-scanned.

**Approach:** Extend 9.1's list with status and a portrait thumbnail, and add a patient page where the doctor uploads, replaces or removes one portrait through the existing storage service and scan (1.11, 1.12), modelled on the doctor's own profile photo (6.4). Regional only.

## Boundaries & Constraints

**Always:**
- A doctor sees only their own patients; another doctor's patient code gives 404, as for invoices.
- A portrait is shown only once its scan is clean; until then the patient shows "being checked".
- Object keys are UUIDs; no patient name in any URL, log, flash message or cache key (AD-5, AD-23). URLs carry the patient code only.
- Portrait rules as the profile photo: one per patient, 5 MiB, JPEG/PNG/WebP, Pillow check up to 6000 px, pending then promoted on a clean scan, replace and delete; deletion goes through `storage.service.delete` (its `DeletionTrace` carries the patient code).
- Where the region has no bucket (`photo.storage_available()` false), the portrait controls are hidden and the list works without them.
- EN and ES strings for everything new.

**Never:**
- No name search or name sort (names have no blind index); no CSV export.
- No stage, history or comments (epic-case-lifecycle), no portrait for staff or the admin site, no deletion of patients.
- No change to the storage service's scan flow or to the profile photo.

## Decisions

- Decision (user, 2026-11-22): only the patient's own doctor sees a portrait, through the inline URL, with no access record per portrait: the patient list and patient file pages are already recorded (page records) and always show the portrait, so a view of those pages covers it. AD-9's exception is widened from the doctor's own profile photo to "the portraits of a doctor's own patients, shown on the patient list and patient file pages".
- Decision (user, 2026-11-22, after 9.1 was built): 9.1 built the status values `active` (default) and `erased`; 9.2 adds `inactive` (migration), shown in the list so inactive patients stand out; no control to change it yet (epic-case-lifecycle adds that). The patient model is in `patients/models.py` (Patient.doctor is PROTECT: logical deletion only, see the 9.1 plan's change log).
- Decision (user, 2026-11-22): pagination as DataTables' standard: 10 rows by default, a selector for 10, 25, 50 and 100, "Showing 1 to 10 of N" and page links; newest first. Built server-side with Django's `Paginator` (page and size in the query string, an invalid value falls back to the default); no DataTables library.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| List | doctor with 3 patients | rows newest first: thumbnail or placeholder, code, name or reference, status, created | — |
| Empty | doctor with none | empty state with "Add a patient" | — |
| Pages | 30 patients, default size | 10 rows, "Showing 1 to 10 of 30", 3 page links; size 25 → 25 rows, 2 pages | size or page out of range or not a number → default size, nearest valid page |
| Other doctor | GET another doctor's patient page | 404 | — |
| Upload | JPEG 2 MB on the patient page | "being checked"; after the clean scan the portrait shows on the page and in the list | — |
| Rejected | infected or not an image | pending cleared; page says the file was refused; old portrait (if any) stays | — |
| Replace | new clean upload over an old portrait | new one shown; old object deleted with a trace | — |
| Delete | doctor removes the portrait | placeholder shown; object deleted with a trace | — |
| No bucket | dev-ae | no portrait controls; list without thumbnails | — |

</frozen-after-approval>

## Code Map

- 9.1 plan `story-patients-work-orders-and-a-first-ice-order-plan.md` -- `patients/` app, `Patient` (UUID pk, doctor FK, code `EU-` + 8 Crockford chars, encrypted first_name/surname/reference/comments, `status`, version, timestamps), list + create at the `patients` route. 9.2 builds on the code as 9.1 leaves it: read `patients/` first.
- `accounts/photo.py` -- the model to copy: purpose `profile_photo` registered with `storage.purposes.register(max_size, content_types, validate=check_image, on_clean, unclaimed_after, in_use)`; `attach` l.177, `remove` l.231, `settle` l.247, `avatar_of` l.289, `storage_available()`. Reuse `check_image` and the limits; do not change the profile photo.
- `accounts/models.py:240-242` -- `Profile.photo` / `photo_pending` FKs to `StoredObject` (SET_NULL): same pair on `Patient` (migration in `patients/`).
- `accounts/views.py:532, 564`, `accounts/templates/accounts/settings_photo*.html`, `static/js/profile-photo.js` -- page, attach POST with `object_id`, delete confirmation; the `storage:uploaded` event.
- `storage/templates/storage/_upload_widget.html`, `storage/static/storage/upload.js` -- the upload widget (`purpose`, `max_size`, `allowed_types`, `single_file`).
- `storage/purposes.py` `may_download` (clean only), `storage/service.py` `owner_inline_url` l.103, `download_url` l.85, `delete` l.125.
- `invoices/views.py:24, 46`, `invoices/templates/invoices/list.html` -- list conventions: `@login_required`, `@never_cache`, GET/HEAD, client-only (404 for staff), empty state, `data-*` test hooks.
- `templates/doctor_base.html:38` nav `patients`; `accounts/page_records.py` `PORTAL_PREFIXES` already has `patients/`.
- `architecture-portal-admin.md` AD-9 (l.144-150), the profile-photo exception.
- Tests to model: `accounts/tests/test_profile_photo.py`, `storage/tests/`, `invoices/tests/`.

## Tasks & Acceptance

**Execution:**
- [ ] `patients/models.py` + migration -- `portrait` and `portrait_pending` FKs to `StoredObject`; add `inactive` to the status values per the decision.
- [ ] `patients/portrait.py` -- purpose `patient_portrait` (rules above), attach, remove, settle, `portrait_of` (inline URL, owner doctor only), `can_download` owner-only.
- [ ] `patients/views.py`, `urls.py`, templates, JS -- list columns and the DataTables-style pagination per the decision, patient page `patients/<code>/` with the portrait and its controls, delete confirmation.
- [ ] `locale/es/LC_MESSAGES/django.po` -- Spanish strings.
- [ ] `architecture-portal-admin.md` AD-9 -- widen the exception per the decision.
- [ ] `patients/tests/` -- one test per matrix row, the scan path with the storage test fakes.

**Acceptance Criteria:**
- Given a doctor on dev with patients, when they open the patient list, then each row shows code and status, and a portrait appears only after its scan.
- Given the portal suite, when it runs, then 0 failures (except the local WeasyPrint tests).

## Verification

**Commands:**
- Portal full suite (local-dev-notes) -- expected: 0 failures except the 8 local WeasyPrint tests.

**Manual checks:**
- On dev: add a portrait to a test patient, see "being checked", then the portrait in the page and list; replace it; delete it. EN and ES, desktop and phone.

## Review Triage Log

Quick lens (Opus), 2026-11-23. Security focus held (doctor isolation, clean-only owner-only portraits, CSRF, attach limited to the doctor's own unowned `patient_portrait` upload, storage delete unchanged without a patient code).

| # | Finding | Verdict | Evidence | Route |
|---|---------|---------|----------|-------|
| 1 | `page=0`/`page=-3` went to the last page, not the nearest | low | `Paginator.get_page` maps every EmptyPage to the last page; the test locked it in | patch: clamp to 1..num_pages |
| 2 | A deletion finished by the hourly sweep writes its trace without the patient code | low | `storage/sweep.py` requeue passes no code; only after the task's own retries fail | reject (rare; fix changes the storage sweep); recorded in deferred-work.md |
| 3 | A refused portrait's trace never got the patient code | medium | `_reject` writes the system trace first; the later delete's `get_or_create` keeps it | patch: back-fill the blank code after the delete, scan flow untouched |
| 4 | Erased patients' codes linked to a page that 404s | low | the list shows erased rows; `_own_patient` excludes them | patch: plain text for erased rows |
| 5 | Patient page title not translated | low | django.po had "Patient" only with msgctxt "invoice PDF" | patch: no-context entry "Paciente" |
| 6 | Inline `onchange`/`onclick` handlers | low | no other template uses inline handlers; "Add a patient" was a dead `#` link without JS | patch: static/js/patient-list.js, anchor `#patient-form`, Apply button without JS |
| 7 | `inactive` treated as unusable by 9.1's ACTIVE filters (ordering, work orders, invoice names) | low | latent: nothing sets inactive until epic-case-lifecycle | reject; recorded in deferred-work.md as a decision for case lifecycle |

## Staging

- Merged 2026-11-23: PR #128 → 68d1d41 (build commit 80b561e); deploy run 45147759539 green on dev and dev-ae (CI suite pass). Local suite 4156 passed, 17 skipped, 8 local WeasyPrint failures only.
- Staging checks run over SSH with the user's delegation (2026-11-23), on test patient EU-0BNY91JQ on dev (server-side upload through the same sign/complete steps, real bucket and clamd scan, pages rendered as its doctor):
  1. List: status, size selector and "Showing 1 to 1 of 1"; `size=25`, `size=abc&page=-3` and `page=99` answer 200 with the default/nearest page (multi-page cases covered by tests: one patient on dev).
  2. Upload: "being checked" on the page, then after the clean scan the portrait's object key is on the patient page and the list, EN and ES.
  3. Replace: the new portrait shown; the old object deleted with a DeletionTrace carrying EU-0BNY91JQ (user actor).
  4. Remove (confirmation page, POST): placeholder; object deleted with a trace carrying the patient code; only the doctor's own avatar link left on both pages.
  5. By-eye EN/ES desktop and phone: owed by the user.
  6. dev-ae (no bucket): the list answers 200 with no portrait controls or links (its doctor has no patients: empty state).
- Left on dev: three deleted portrait objects' DeletionTraces (f56041d6, c0ea78a1, c7887911) for EU-0BNY91JQ; no portrait linked.
