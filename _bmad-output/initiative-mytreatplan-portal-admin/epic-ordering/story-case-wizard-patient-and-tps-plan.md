---
title: 'Case wizard: patient and TPS'
type: 'feature'
ticket: '3'
created: '2026-11-23'
status: 'draft'
baseline_revision: ''
route: 'full'
route_source: 'auto'
risk: 'medium'
review: ''
review_source: ''
lenses_ran: []
review_loop_iteration: 0
context: []
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** `start-a-case/` is a placeholder. A doctor cannot choose a patient and a treatment-plan service (TPS) and put it in the cart, and nothing stops a TPS for a maker whose login the doctor never saved.

**Approach:** Replace the placeholder with a server-side wizard: step 1 picks an active patient or creates one (the 9.2 dialog), step 2 picks maker or in-house, size (ICE, short, medium, long) and the add-ons the catalogue offers on that product; the result is a cart item stored regionally. Regional only; no checkout (9.6).

## Boundaries & Constraints

**Always:** Doctor-only views (404 for staff) with the usual decorators; no patient name or maker credential in a URL, log or message. Makers are `accounts.makers` (`NAMED`, `has_login`) plus two wizard-only choices, "not decided yet" and "other" with a name (at most 60 characters); a named maker without a saved login shows a warning with a link to `settings_maker_save` with `?next=` back to the wizard and the item is refused; the two wizard-only choices never block. Products are chosen by category (`tps_branded` for a maker or "not decided yet" or "other", `tps_in_house` for printing) and size, never by code; add-ons are only those `resolver.add_ons(product)` offers, priced by `resolver.price`. A patient with a live work order is refused unless that work order is an ICE and the new service is a TPS. EN and ES texts.

**Never:** Checkout, vouchers, the ICE deduction, the included-case tick, VirtuaOrtho (9.4–9.7). No catalogue or admin-site code change. No new `OrderLine` or `WorkOrder` field.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| Happy | active patient, Invisalign with saved login, medium, Junior II | cart item with patient, maker, product, add-on; cart page shows it | — |
| Missing login | Smartee, no saved login | warning + link to save it, next= wizard | item refused |
| Not decided / other | "not decided yet", or "other" + name | item added | empty other name refused |
| Junior on short | short + Junior I | — | refused (not offered) |
| Live non-ICE work order | patient in `pending_assignment` of a TPS | — | refused with message |
| Live ICE | patient's live work order is an ICE, new TPS | item added | — |
| Not offered | no TPS product offered to this doctor in that size | size not listed | tampered POST refused |
| Other doctor's / inactive patient | code of another doctor or inactive | — | 404 |

</frozen-after-approval>

## Code Map

- `accounts/urls.py:71`, `accounts/views.py:1266` -- `start_a_case` placeholder; keep the name and path, move the view to the new app.
- `accounts/urls.py:66` -- `cart` placeholder (depends on Q1).
- `accounts/page_records.py:43` (`PORTAL_PREFIXES`), `mytp_publicsite/tests/test_settings_staging.py` (`PORTAL_URL_NAMES`) -- register every new address.
- `accounts/makers.py` -- `NAMED`, `MAKER_NAMES`, `has_login`; reuse, do not change.
- `catalogue/resolver.py:399` `add_ons`, `:410` `price`; `subscriptions/service.py:225` `offers_for` -- what the doctor may buy; `resolver.included_cases` -- remaining count (Q6).
- `patients/service.py` -- `patients_of`, `patient_of`, `live_work_order`, `create_patient`; add an "is this work order an ICE" helper reading `resolver.product(wo.product_id).category`.
- `patients/views.py` `_create`, `patients/templates/patients/_patient_form.html`, `static/js/dialog.js` -- reuse the add-patient dialog inside step 1.
- `payments/service.py:~259` `_check_patients` -- do not change (TPS lines carry no patient until 9.6/9.8).
- `accounts/fiscal.py:504` `has_usable_entity` (Q5).
- `signup/steps.py` -- precedent for a server-side wizard with plain-POST fallback.
- `patients/tests/conftest.py` -- ICE-only catalogue; extend with TPS branded/in-house short/medium/long and ONE-ARCH, JUNIOR-1/2.

## Tasks & Acceptance

**Execution:** (filled after the Open Questions)

**Acceptance Criteria:** (filled after the Open Questions)

## Open Questions

1. **What "add to the cart" means before 9.6 builds the cart.** (a) Recommended: 9.3 adds the cart's storage now, a regional `CartItem` table (doctor, patient, product id, add-on codes, maker, other name, created) with its AD-2 row, and a bare cart page listing items by patient with Remove; 9.6 adds checkout, vouchers and the rest. (b) Keep the wizard's result in the session and show a summary page; 9.6 builds the cart and rewires the wizard. (c) Create an order directly with `create_order` — not possible today, `_check_patients` refuses a TPS line that names a patient.
2. **Where the maker is kept.** (a) Recommended: on the cart item (maker code, or `undecided`, or `other` + name); 9.6/9.8 carry it onto the order line or work order. (b) Add it to `OrderLine` now — conflicts with Never (no order changes in 9.3).
3. **One arch.** The catalogue offers ONE-ARCH (−25 %) on in-house TPS only (5.6 decision); Junior I/II on medium branded only. (a) Recommended: the wizard shows what the catalogue offers on the chosen product, nothing hard-coded; if one arch should apply to branded TPS too, staff add the category to ONE-ARCH on the admin site (data, no code). (b) Hard-code one arch on every TPS in the wizard — the resolver would refuse it at pricing.
4. **Planning after an ICE.** (a) Recommended: allowed when the patient's live work order is an ICE and the new item is a TPS; the ICE work order stays open until 9.8 closes it on payment. (b) Refuse every patient with a live work order until 9.8.
5. **Fiscal block (epic note: ordering is blocked until `has_usable_entity`).** (a) Recommended: the wizard shows the block with a link to add a fiscal entity (`?next=` back) and refuses items without one. (b) Leave it to 9.6's checkout.
6. **Included branded-TPS cases (epic handoff: 9.3 shows the remaining count).** (a) Recommended: step 2 shows "N included cases left" when the doctor's subscription has any; no tick (9.6). (b) Leave it to 9.6.
7. **Creating a patient inside the wizard.** (a) Recommended: the 9.2 dialog on step 1 posts to a wizard view that reuses `_create` and goes straight to step 2 for the new patient. (b) Link to the patient list and back by hand.

## Verification

**Commands:**
- `pytest -q` (suite command in local-dev-notes) -- expected: 0 failures.
- `python manage.py makemigrations --check --dry-run` -- expected: no changes after the migration is committed.
