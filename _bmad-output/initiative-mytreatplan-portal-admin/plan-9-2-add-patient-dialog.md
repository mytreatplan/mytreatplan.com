---
title: 'Add a patient in a dialog'
type: 'bugfix'
ticket: ''
created: '2026-11-23'
status: 'built'
baseline_revision: 'c5d621b60a47607b9e2ae4e2d42e2fb1dc3624ee'
route: 'full'
route_source: 'auto'
risk: 'low'
review: 'quick'
review_source: 'pinned'
lenses_ran: ['quick']
review_loop_iteration: 0
context: []
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** On the patient list (9.2), the "Add a patient" form is always open above the table. It crowds the page, pushes the table down on phones and is not the most usable layout (user's by-eye check, 2026-11-23).

**Approach:** An "Add a patient" button next to the page title opens the same form in a native `<dialog>`: a centred card on desktop and a full-screen sheet on phones. Esc and Cancel close it, and it reopens with the errors when a save fails. Without JS the button is a link to a small page `patients/new/` with the same form. The dialog opener is generic, so the 9.3 case wizard can reuse it.

## Boundaries & Constraints

**Always:**
- Same form, validation, service call (`service.create_patient`), success message and redirect to the list as 9.1. Only where the form is shown changes.
- No patient name in any URL, log or message.
- `patients/new/` is a doctor-only page (404 for staff), `@login_required`, `@csrf_protect`, `@never_cache`, like the list.
- English and Spanish strings for everything new.
- The dialog is accessible: a labelled title, focus on the first field when it opens, focus back on the button when it closes. The native `<dialog>` with `showModal()` gives Esc and a focus trap.
- No inline JS handlers (repo convention, 9.2 review); behaviour lives in static JS files.

**Never:**
- No JS framework or modal library; no change to the list's columns, portraits or pagination.
- No change to `PatientForm`, `patients.service` or the 9.1 POST contract of `patients/`, which existing tests post to.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| Open | JS on, doctor clicks "Add a patient" | the dialog opens over the list; focus on the first field; the table does not move | — |
| Save | valid form in the dialog | 302 to the list, success message with the code, new row first | — |
| Invalid | nothing given, from the dialog (POST `patients/`) | list re-rendered with the dialog open and the field error shown | — |
| Close | Esc or Cancel | dialog closed, nothing saved, focus back on the button | — |
| No JS | the button is a plain link | `patients/new/` shows the form alone; a valid save → list; an invalid one → same page with the error | — |
| Empty list | doctor with no patients | the empty state's "Add a patient" opens the same dialog (link to `patients/new/` without JS) | — |
| Not a doctor | staff GET `patients/new/` | 404 | — |

</frozen-after-approval>

## Code Map

- `patients/views.py` -- `patient_list` already handles the create POST (form, `service.create_patient`, message, `redirect('patients')`). Add `new_patient` (GET/HEAD/POST, same decorators and `_doctor_only`) that reuses the same create logic; extract a small shared helper rather than duplicating it. On an invalid POST, `patient_list` passes `open_dialog=True` to the template.
- `patients/urls.py` -- add `patients/new/` (name `patient_new`) BEFORE `patients/<str:code>/`, since `new` would otherwise match as a code. Patient codes are `EU-`/`AE-` + 8 chars, so `new` can never be a real code.
- `patients/templates/patients/list.html` lines 16-33 -- the inline form moves into a new partial `patients/templates/patients/_patient_form.html` (fields, non-field errors, `data-patient-form`, `id="patient-form"`). The list's heading row gets the button: `<a href="{% url 'patient_new' %}" data-opens-dialog="add-patient">`. The `<dialog id="add-patient" data-dialog {% if open_dialog %}data-open{% endif %}>` holds a title, the partial with `action="{% url 'patients' %}"`, Save, and Cancel (`data-dialog-close`, type button). The empty-state link at line 83 points the same way.
- New `patients/templates/patients/new.html` -- `doctor_base.html`, title "Add a patient", the partial posting to `patient_new`, a "Back to patients" link.
- `templates/doctor_base.html:38` -- add `patient_new` to the names that keep the Patients nav item current.
- New `static/js/dialog.js` (generic, reusable by 9.3) -- `[data-opens-dialog=id]` click → `preventDefault`, `showModal()`; `[data-dialog-close]` → `close()`; `dialog[data-open]` → `showModal()` on load; on close, focus returns to the opener. Load it from `list.html`'s `head` block next to `patient-list.js`. `patient-list.js` keeps only the page-size code; its focus-the-form code goes.
- Styling, Tailwind 4 (`assets/css/site.css` → `static/css/site.css`, `npm run build:css`): on phones `m-0 h-dvh max-h-none w-full max-w-none`, a full-screen sheet; from the `tablet:` breakpoint a centred card of about 640 px with `rounded-20` and padding; a `backdrop:` dimmed overlay. Reuse the button classes of the current Save (green pill) and a secondary outline pill for Cancel. `static/css/site.css` is not tracked (CI and deploy build it): run `npm run build:css` locally only to check the classes.
- `locale/es/LC_MESSAGES/django.po` -- "Cancel" may exist; add "Add a patient" if it lacks a no-context entry, plus any new string; recompile.
- Tests: `patients/tests/test_views.py` (9.1 create tests post to `/en/patients/` and must keep passing; `test_the_page_lists_no_patients_at_first` checks `data-patient-form`, which stays in the dialog) and `patients/tests/test_list.py`.

## Tasks & Acceptance

**Execution:**
- [ ] `patients/views.py`, `patients/urls.py` -- `new_patient` view and route; shared create helper; `open_dialog` on an invalid list POST.
- [ ] `patients/templates/patients/_patient_form.html`, `list.html`, `new.html`, `templates/doctor_base.html` -- partial, button and dialog, fallback page, nav.
- [ ] `static/js/dialog.js`, `static/js/patient-list.js` -- generic dialog behaviour; drop the old focus code.
- [ ] `locale/es/LC_MESSAGES/django.po` -- Spanish strings.
- [ ] `patients/tests/test_views.py` -- one test per server-side matrix row (Save, Invalid with `data-open`, No-JS page GET/valid/invalid, empty-state link target, staff 404, `patients/new/` not taken as a code, Spanish); the JS rows are checked by hand on dev.

**Acceptance Criteria:**
- Given a doctor on a phone, when they open the patient list, then the table starts right under the title row and the form shows only after "Add a patient" is tapped, as a full-screen sheet.
- Given the portal suite, when it runs, then there are no failures except the 8 local WeasyPrint tests.

## Verification

**Commands:**
- Portal full suite (local-dev-notes) -- expected: no failures except the 8 local WeasyPrint tests.
- `npm run build:css` -- expected: the dialog classes present in `static/css/site.css`.

**Manual checks:**
- On dev, EN and ES, desktop and phone (by the user): the dialog opens, Esc and Cancel close it, an empty save reopens it with the error, a valid save lists the patient; `patients/new/` works on its own.

## Review Triage Log

Quick lens (Sonnet; low-risk UI change), 2026-11-23. Full suite after the change: 4165 passed; failures only the 8 local WeasyPrint tests and the known-flaky Chromium lockout test (passes alone).

| # | Finding | Verdict | Evidence | Route |
|---|---------|---------|----------|-------|
| 1 | Reopened dialog focuses the first field, not the one with the error | low | `open()` focused the first input; Django marks invalid fields `aria-invalid` | patch: focus the first `[aria-invalid="true"]`, else the first field |
| 2 | Errors and typed values stay after Cancel until reload | low | only after an invalid POST; reopening shows the last attempt, which is arguably useful | reject (cosmetic) |
| 3 | `tablet:rounded-16` instead of the plan's `rounded-20` | false | the theme has no `--radius-20` (only 16/24); changed deliberately by the planner, CSS checked | reject |
| 4 | Thin test for the empty-list row | low | covered by the link assertions; JS rows checked by hand | reject |
| 5 | `test_list.py` empty-state check satisfied by the header button | low | substring `data-add-patient` now always present | patch: assert the empty-state link's href and `data-opens-dialog` |

CI then failed on `test_lockout_after_five_wrong_passwords[chromium]` (both runs, and every local full run, passing alone): `str(BLOCKED_MESSAGE)` was read in the thread's active language, which the new Spanish page test leaves at `es`, so it waited for the Spanish text on the English login page. Fixed in the test (read it under `translation.override('en')`); full suite 4166 passed. Likely the cause of this test's earlier "flaky" failures too.
