---
title: 'Case wizard: patient and TPS'
type: 'feature'
ticket: '3'
created: '2026-11-23'
status: 'done'
baseline_revision: '30b005596d209c5faa9a39bdbc0243836684a8ad'
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

**Problem:** `start-a-case/` is a placeholder. A doctor cannot choose a patient and a treatment-plan service (TPS) and put it in the cart, and nothing stops a TPS for a maker whose login the doctor never saved.

**Approach:** Replace the placeholder with a server-side wizard: step 1 picks an active patient or creates one (the 9.2 dialog), step 2 picks maker or in-house, size (ICE, short, medium, long) and the add-ons the catalogue offers on that product; the result is a cart item stored regionally. Regional only; no checkout (9.6).

## Boundaries & Constraints

**Always:** Doctor-only views (404 for staff) with the usual decorators; no patient name or maker credential in a URL, log or message. Makers are `accounts.makers` (`NAMED`, `has_login`) plus two wizard-only choices, "not decided yet" and "other" with a name (at most 60 characters); a named maker without a saved login shows a warning with a link to `settings_maker_save` with `?next=` back to the wizard and the item is refused; the two wizard-only choices never block. Products are chosen by category (`tps_branded` for a maker or "not decided yet" or "other", `tps_in_house` for printing) and size, never by code; add-ons are only those `resolver.add_ons(product)` offers, priced by `resolver.price`. A patient with a live work order is refused unless that work order is an ICE and the new service is a TPS. EN and ES texts.

**Never:** Checkout, vouchers, the ICE deduction, the included-case tick, VirtuaOrtho (9.4–9.7). No catalogue or admin-site code change. No new `OrderLine` or `WorkOrder` field.

## Decisions

- Decision (user, 2026-11-23): 9.3 adds the cart's storage: a regional `CartItem` per doctor and patient (product id, add-on codes, maker choice, other name) with its AD-2 row, and a bare cart page listing items by patient with Remove; 9.6 adds checkout, vouchers and the rest on top.
- Decision (user, 2026-11-23): the maker choice (a named maker, `undecided` or `other` + name) lives on the cart item; 9.6/9.8 carry it onto the order line and work order.
- Decision (user, 2026-11-23): the wizard offers exactly the add-ons `resolver.add_ons(product)` returns; nothing is hard-coded (one arch on branded TPS is a catalogue change staff make on the admin site).
- Decision (user, 2026-11-23): a patient whose live work order is an ICE may get a TPS (planning after an ICE); any other live work order refuses; 9.8 closes the ICE on payment.
- Decision (user, 2026-11-23): without a usable fiscal entity (`has_usable_entity`) the wizard shows a block linking to `settings_fiscal_add?next=<wizard>` and refuses items.
- Decision (user, 2026-11-23): step 2 shows the remaining included branded-TPS cases when there are any; the tick is 9.6's.
- Decision (user, 2026-11-23): step 1 reuses 9.2's add-patient dialog, posting to a wizard view that reuses `_create` and goes straight to step 2 for the new patient.
- Decision (user, 2026-11-23): plan kept whole (slightly over 1600 tokens). Approved and continued; merge, staging checks (SSH), pushing the story branch, the mytp-core release tag if needed, and the completion commit to master are DELEGATED for this story.

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
| Patient already in cart | second item for the same patient | — | refused (one service per patient) |
| No fiscal entity | doctor without a usable entity | block with link | item refused |

</frozen-after-approval>

## Code Map

- `accounts/urls.py:71`, `accounts/views.py:1266` -- `start_a_case` placeholder; keep the name and path, move the view to the new app.
- `accounts/urls.py:66` -- `cart` placeholder; replaced by the cart page.
- `accounts/page_records.py:43` (`PORTAL_PREFIXES`), `mytp_publicsite/tests/test_settings_staging.py` (`PORTAL_URL_NAMES`) -- register every new address.
- `accounts/makers.py` -- `NAMED`, `MAKER_NAMES`, `has_login`; reuse, do not change.
- `catalogue/resolver.py:399` `add_ons`, `:410` `price`; `subscriptions/service.py:225` `offers_for` -- what the doctor may buy; `resolver.included_cases` -- remaining count.
- `patients/service.py` -- `patients_of`, `patient_of`, `live_work_order`, `create_patient`; add an "is this work order an ICE" helper reading `resolver.product(wo.product_id).category`.
- `patients/views.py` `_create`, `patients/templates/patients/_patient_form.html`, `static/js/dialog.js` -- reuse the add-patient dialog inside step 1.
- `payments/service.py:~259` `_check_patients` -- do not change (TPS lines carry no patient until 9.6/9.8).
- `accounts/fiscal.py:504` `has_usable_entity` -- the fiscal block.
- `signup/steps.py` -- precedent for a server-side wizard with plain-POST fallback.
- `patients/tests/conftest.py` -- ICE-only catalogue; extend with TPS branded/in-house short/medium/long and ONE-ARCH, JUNIOR-1/2.

## Tasks & Acceptance

**Execution:**
- [ ] `cart/` (new app: models, migration, service, views, urls, templates) -- `CartItem` (uuid, doctor CASCADE, patient CASCADE, product_id, add_on_codes JSON, maker, other_name ≤ 60, created_at; unique doctor+patient). `service.add_tps(doctor, patient, *, maker, other_name, in_house, size, add_on_codes)` raises `CartRefused(code)` for every refusal in the matrix and the Always rules; picks the offered product from `offers_for` by category and size (first by code when several); checks add-ons with `resolver.price`. `items_of(doctor)`, `remove(doctor, item_id)`.
- [ ] `cart/views.py` + templates -- `start-a-case/` (name `start_a_case`, step 1: active patients via `patients_of`, the dialog, fiscal block), `start-a-case/patient/` (POST, `start_a_case_patient`), `start-a-case/<code>/` (`start_a_case_tps`, step 2: maker or in-house, maker choice incl. "not decided yet" and "other" + name, size with price, offered add-ons, included-cases count, missing-login warning with `settings_maker_save?next=`), `cart/` (`cart`, items by patient: patient code and name, product, add-ons, maker) and `cart/<uuid>/remove/` (POST, `cart_remove`). Plain POST works without JS; a small static JS may hide add-ons not offered for the chosen size.
- [ ] `accounts/urls.py`, `accounts/views.py` -- drop the two placeholders (names kept by the new app); `mytp_publicsite/tests/test_settings_staging.py` `PORTAL_URL_NAMES` -- add the new names.
- [ ] `patients/service.py` -- `live_is_ice(patient)`; `patients/views.py` -- let the wizard reuse `_create`.
- [ ] `locale/es/LC_MESSAGES/django.po` -- Spanish for every new string.
- [ ] `architecture-portal-admin/architecture-portal-admin.md` -- AD-2 row for the cart.
- [ ] `patients/tests/conftest.py`, `cart/tests/` -- TPS branded/in-house × short/medium/long, ONE-ARCH (in-house), JUNIOR-1/2 (medium); one test per matrix row and the views (404 for staff and other doctors, fallback page without JS).
- [ ] `pytest.ini` -- add `cart` to `testpaths` (a new app is not collected otherwise).

**Acceptance Criteria:**
- Given a doctor with a saved Invisalign login, when the wizard adds a medium Invisalign TPS with Junior II, then the cart page shows it under the patient and the home's cart link reaches it.
- Given a missing Smartee login, when the doctor follows the warning's link and saves it, then they land back on step 2 for the same patient.
- Given a new patient created in step 1, then step 2 opens for that patient and the patient list shows it.

## Review Triage Log

Pass 1 (quick lens, opus): high 0, medium 2, low 3, false 0, maybe-false 0; one low rejected.

| # | Finding | Verdict | Route | Evidence / action |
|---|---------|---------|-------|-------------------|
| 1 | Size names shown as raw codes, English on /es/ | medium | patch | `choose.html`/`cart.html` print `size|capfirst`; no .po entries. Translated size labels added. |
| 2 | Included-cases number is the term total, not "remaining" | low | patch | `resolver.included_cases` = total (catalogue/models.py:221); used cases are counted in 8.13. Wording changed to what the subscription includes; the remaining count waits for 8.13 (deferred-work). |
| 3 | `live_is_ice` treats a missing product as ICE | medium | patch | `product is None or …` lets a second TPS in when a live TPS's product left the catalogue. Now refuses; test added. |
| 4 | Invalid dialog post does not reopen the dialog | low | patch | `open_dialog` always False; 9.2 re-renders with the dialog open. Fixed. |
| 5 | `?next=` not URL-encoded | low | patch | Precedent `payments/views.py:197` quotes it. Fixed. |
| 6 | Step 2 shows the form for a patient who will be refused | low | reject | Refusal happens on submit with a message (matrix met); a pre-check adds branches for a rare URL-typed case. |

## Verification

**Commands:**
- Work only in this worktree (`.worktrees/9.3`, branch `story/9-3-case-wizard-patient-and-tps`). From its root, sandbox disabled: `export FIELD_ENCRYPTION_KEYS="test1:dGVzdC1vbmx5LWZpZWxkLWtleS1ub3Qtc2VjcmV0ISE=" BLIND_INDEX_KEY="dGVzdC1vbmx5LWJsaW5kLWluZGV4LWtleS0zMmJ5dGU=" SECRET_KEY=test-only-worktree-secret DEBUG=False SECURE_SSL_REDIRECT=False ALLOWED_HOSTS=localhost,testserver,127.0.0.1; export DATABASE_URL="sqlite:///$TMPDIR/s93.sqlite3"; ../../../.venv/bin/python -m pytest -q -p no:cacheprovider --tb=line` -- expected: 0 failures (run `../../../.venv/bin/python manage.py compilemessages` after editing the .po).
- `python manage.py makemigrations --check --dry-run` -- expected: no changes after the migration is committed.

## Staging

- Merged: PR #130 → 05e0372 (2026-11-23); deploy run 45230682418 green (dev and dev-ae); PR CI run 45227480102 green. Local suite 4213 passed, 18 skipped, 1 known worktree-only failure (`test_remembered_device`).
- Checks on dev (server-side, test client, one transaction rolled back, nothing left behind), with patient EU-0BNY91JQ (live ICE work order) and its doctor (usable fiscal entity):
  1. Invisalign, medium, JUNIOR-2 → 302 to the cart; the cart shows the patient code and "Medium"/"Medio" in EN and ES. Passed (also proves the planning-after-an-ICE rule).
  2. Smartee without a saved login → refused, warning with the `settings/maker/smartee/?next=` link. Passed.
  3. "Not decided yet" and "other" + name → added. Passed.
  4. A patient with a live non-ICE work order → refused (`live_work_order`). Passed.
  5. No usable fiscal entity → block: covered by tests only.
- Found at staging: the wizard offers no ICE. The catalogue's ICE is its own category (`initial_case_evaluation`, no size), while the wizard lists only `tps_*` sizes, so the ticket's "ICE, short, medium or long" choice has no ICE (deferred-work).
- Owed by the user: by-eye check of the wizard and cart, EN/ES, desktop and phone.
