---
title: 'Patients, work orders and a first ICE order'
type: 'feature'
ticket: '1'
created: '2026-11-22'
status: 'built'
baseline_revision: '84da0546e94b99c67f279dc1e08805e612a056ca'
route: 'full'
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

**Problem:** The portal has no patients and no work orders. Ordering (9.2–9.9), the case lifecycle and clinical ops all build on them. 7.1 left `OrderLine.patient_code` empty, and 7.10 left the invoice's patient-name lookup unregistered.

**Approach:** This is a tracer bullet. It adds:
- the regional Patient model, the patient-code generator and the create-patient service (AD-5, AD-15);
- the WorkOrder model, with the case stage machine in a mytp-core minor release (AD-21);
- the invoice name lookup;
- the thinnest path: a doctor creates a patient, then orders and pays an ICE for them with the fake gateway, and that creates a work order in "Pending assignment".

Decisions (user, 2026-11-22):
- Stages (from the initiative, P9), in order: `pending_payment` (not live; for 9.9), `pending_assignment`, `assigned`, the optional `pending_registers_upload`, `processing_prescription` and `prescription_done`, then `pending_appointment`, `in_development`, `pending_review`, `pending_approval`, and the final `approved` and `cancelled`.
- Allowed moves, drafted by Claude for clinical ops to check later:
  - Forward, one step at a time; any optional stage may be skipped.
  - From `pending_review` or `pending_approval` back to `in_development` (changes requested).
  - `cancelled` from any stage that is not final.
- "Live" means any stage except `pending_payment`, `approved` and `cancelled`. A patient has at most one live work order.
- A patient has an optional first name and surname and an optional reference of the doctor's own; at least one of them is required. All three and the comments are field-encrypted, with no blind index.
- UI: the placeholder Patients page becomes a create form plus a list of the doctor's patients (code, name or reference, created). The dev test-order page gets a patient choice for an ICE. The real wizard is 9.3.
- An ICE paid for a patient creates one work order in `pending_assignment`, idempotent per order line. 9.8 extends the same receiver to every service. No outbox events in 9.1; they come with 9.8.

## Boundaries & Constraints

**Always:**
- Patient code: region prefix (`REGION_PROFILE.patient_code_prefix`), `-`, then 8 random Crockford base32 characters from `secrets`. Unique; retried on collision; never derived from patient data. Patients are never hard-deleted, so a code is never reused.
- Patient and WorkOrder have UUID primary keys and are marked `CROSSES_SITES`. Each carries a `version`, and every change goes through its service function (AD-15).
- Stage rules live only in mytp-core: stages, the allowed-move table, `LIVE_STAGES` and a check function. The portal service enforces them. The one-live rule is also a partial unique constraint in the database.
- A doctor sees and orders only their own patients. An ICE line without the doctor's own patient is refused at order creation.
- AD-2 table: add the patients and work-orders rows as the architecture already lists them (patients code only centrally; names and comments encrypted).

**Never:**
- No patient portrait, no stage history UI, no cart, no wizard, no admin-site screens.
- No name search, and no names in URLs or logs.
- Do not change the invoice PDF beyond registering the lookup.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| Create patient | Name and surname given | Patient with a code like `EU-7K3QX9MD`, shown in the list | — |
| Reference only | No name, reference "P-12" | Created; the list shows the reference | — |
| Nothing given | All three empty | Form error, no patient | Field error |
| Code collision | Generator returns an existing code once | Retries with a new code | — |
| ICE paid | Test order: ICE for own patient, fake gateway pays | One work order in `pending_assignment` for that patient and line | — |
| Hook replay | Paid hook fired twice | Still one work order | — |
| Other doctor's patient | ICE line with someone else's code | Order refused | Refusal message |
| Second live work order | Patient already has a live one; ICE ordered | Order refused at creation; a second one at payment rolls back | HookFailed |
| Illegal move | `pending_assignment` → `in_development` | Refused by the core check | `InvalidStageTransition` |
| Invoice PDF | Paid ICE invoice | Name (or reference) next to the code | Erased patient: code only |

</frozen-after-approval>

## Code Map

- `regions/profiles.py:90` -- `patient_code_prefix` ('EU'/'AE').
- `../mytp-core/mytp_core/contracts/checks.py:45` `PATIENT_CODE_RE`. Core is at 0.32.0 (7.9 took it); add a new module (e.g. `mytp_core/stages.py`) and release 0.33.0. Tests go in `../mytp-core/tests/`.
- `scripts/vendor-mytp-core.sh vX.Y.Z`, `requirements.txt:60`, `integration/tests/test_vendored_core.py`. The admin repo pins the same minor (AD-10). It only needs the vendor bump, no code.
- `accounts/crypto.py` -- `EncryptedCharField` etc.; examples at `accounts/models.py:100-113`.
- `mytreatplan/uids.py` -- `CROSSES_SITES`. Convention test: `mytp_publicsite/tests/test_cross_sites.py`. Example: `payments/models.py:39`.
- `payments/models.py:129` `OrderLine` (`product_id`, `code`, `ice`, `net`, `patient_code` l.162). `payments/service.py:353` `create_order`, `_line_rows` l.178; `pricing.OrderRefused` for refusals.
- `payments/hooks.py` -- `hooks.register('paid', name, receiver)` in `AppConfig.ready`. The receiver gets the locked Order inside the transaction. Precedent: `invoices/apps.py:13-17`.
- `payments/views.py:~358` `test_order` + its template. Gated by `PAYMENTS_TEST_ORDERS`/`PAYMENTS_FAKE_GATEWAY`. Tests: `payments/tests/test_checkout.py`, `payments/tests/conftest.py` (snapshot products).
- `catalogue/models.py:119` -- the ICE category is `'initial_case_evaluation'`.
- `invoices/patients.py` -- `register(lookup)` with signature `lookup(user, codes) -> {code: name}`, omitting unknown or erased codes. Called by `invoices/pdf.py:85`.
- `accounts/urls.py:18` -- the placeholder `patients/` → `views.coming_soon` (name `patients`). `templates/doctor_base.html:38` links it. Urls are included flat in `mytreatplan/urls.py:56-63`.
- `architecture-portal-admin.md` AD-2 table (l.79-98).

## Tasks & Acceptance

**Execution:**
- [ ] `../mytp-core/mytp_core/stages.py` + tests -- Stage constants, `ALLOWED` (from → set of to), `LIVE_STAGES`, `FINAL_STAGES`, and `check_transition(from, to)` raising `InvalidStageTransition`. Bump the version, commit and tag locally; no push.
- [ ] `patients/` (new app: models, migration, service, apps, forms, views, urls, templates) --
  - `Patient`: doctor FK, unique code, encrypted first_name, surname, reference and comments, status, version, timestamps.
  - `WorkOrder`: patient FK, unique `order_line_id`, product_id, code, stage, version, timestamps, and a partial unique constraint on patient where the stage is live.
  - `create_patient(doctor, …, actor)`, `move_work_order(work_order, to_stage, expected_version, actor)` and `live_work_order(patient)`.
  - The paid receiver `work_orders`, and the invoice name lookup, both registered in `ready()`.
  - The patients list and create page at `patients/` (replacing the placeholder route).
- [ ] `payments/service.py` -- `create_order` lines accept `patient_code`, stored on `OrderLine.patient_code`. An ICE line must have one of the user's patients with no live work order; otherwise `OrderRefused` with a new reason and message.
- [ ] `payments/views.py` + test-order template -- A patient select (the user's patients) when the chosen product is an ICE.
- [ ] `accounts/urls.py`, `mytreatplan/urls.py`, `mytreatplan/settings.py` -- Route `patients/` to the new app and add it to INSTALLED_APPS.
- [ ] `requirements.txt` + `vendor/`, and `../mytp-admin` requirements -- Vendor the new core release.
- [ ] `locale/es/LC_MESSAGES/django.po` -- New strings (es).
- [ ] `architecture-portal-admin.md` AD-2 -- Add or confirm the rows for patients and work orders.
- [ ] Tests -- One for each matrix row: core move table, patients service and views, order refusals, the paid hook, the invoice lookup, and the cross-sites convention.

**Acceptance Criteria:**
- Given a doctor on dev with test orders on, when they create a patient and pay an ICE for them with the fake gateway, then the patient has an `EU-` code, one work order is in `pending_assignment`, and the invoice PDF shows the name next to the code.
- Given another doctor, when they open the patients page, then they do not see the first doctor's patients.
- Given the portal, core and admin suites, when they run, then they have 0 failures (except the local WeasyPrint tests).

## Verification

**Commands:**
- Portal full suite (local-dev-notes) -- expected: 0 failures except the 8 local WeasyPrint tests.
- `../mytp-core`: `uv run --no-cache pytest -q` -- expected: 0 failures.

**Manual checks:**
- On dev after the merge: create a patient, place a test order for an ICE, pay it with the fake gateway, and check the work order and the invoice PDF.

## Review Triage Log

Pass 1 (quick lens, Opus, 2026-11-22): high 0, medium 0, low 7, false 0, maybe-false 0. No unmet acceptance criterion.

| # | Finding | Verdict | Route | Evidence / action |
|---|---------|---------|-------|-------------------|
| 1 | `PATIENT_REFUSED` missing from `pricing.__all__` and `resolver.__all__`; `resolver.PATIENT_REFUSED` raises AttributeError | low | patch | Every other refusal is in both; direct correction. |
| 2 | Paid receiver identifies a case by the product's current category; a category change between order and payment skips the work order silently | low | reject | Needs the snapshot to re-categorise an ICE while an order is pending; the fix changes the receiver and the invoice tests' fixtures. |
| 3 | Test-order page: the patient select ignores the `ice` flag, lists patients with a live case, and is absent when the doctor has none | low | reject | Staging-only page (`PAYMENTS_TEST_ORDERS`); the server refuses every wrong choice with a message. Fix needs template logic. |
| 4 | Start-up test does not prove `ready()` registers the invoice name lookup (fixture re-registers it) | low | patch | Direct correction: assert the lookup left by `ready()`. |
| 5 | Core release commit adds a stray `uv.lock` | low | patch | Done by me: new commit 09243b7 drops it and ignores it; local tag v0.33.0 moved (never pushed); the wheel's code is unchanged. |
| 6 | The 0.33.0 wheels are untracked in both repos | low | patch | Process: added with the commits. |
| 7 | Any `IntegrityError` in the receiver is reported as a second live case, also a duplicate `order_line_id` from a replay race | low | reject | Message meaning only; the payment rolls back either way. |
