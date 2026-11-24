---
title: 'Case wizard redesign with django-formtools'
type: 'feature'
ticket: '16'
created: '2026-11-24'
status: 'ready-for-dev'
baseline_revision: ''
route: 'full'
route_source: 'auto'
risk: 'high'
review: ''
review_source: ''
lenses_ran: []
review_loop_iteration: 0
context: []
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** The case wizard (9.3, 9.4) works but crams TPS and VirtuaOrtho into one long page of radios and checkboxes (`cart/choose.html`), which the user found hard to use.

**Approach:** Rebuild step 2 onwards as a step-by-step wizard on django-formtools (`NamedUrlSessionWizardView`, one URL per step, Back keeps the choices): product (two cards) → TPS maker or VirtuaOrtho type → a last page with lengths and prices, add-ons, and for VirtuaOrtho the shipping address, ending in "Add to cart". Step 1 stays the patient step (9.15's chooser + the add-patient dialog). Every business rule stays in `cart/service.py`, unchanged.

## Boundaries & Constraints

**Always:**
- Steps (patient code in the path, under `start-a-case/`): `product` (cards "Treatment Planning" / "VirtuaOrtho", each with one sentence) → `maker` (TPS: "In-house printing" or "Brand-made aligners"; brand reveals a `<select>` of the makers, "Not decided yet" and "Other" + name) or `vo_type` (cards: One "Without patient monitoring", DigitalTwin "With patient monitoring") → `options` (TPS: ICE, short, medium, long with prices; one arch; Junior I/II on medium — VO: lengths, the two ODBs, retainers, Spark on Demand on One only with quantity, Byte Sync, address `<select>` with the default preselected) → `done()` calls `service.add_tps` / `service.add_virtuaortho` and redirects to the cart.
- Every rule of 9.3, 9.4, 9.3-ICE and 9.6 holds: maker-login warning with the `settings_maker_save?next=` link and refusal; "not decided yet"/"other" never block; live work order refusal with the ICE→planning exception; no ICE when any live work order; VO only where `offers_for` offers it; retainers only after a finished treatment; ODB-DT automatic under DigitalTwin; Spark 4–9 from the catalogue; add-ons exactly `resolver.add_ons(product)`; fiscal-entity block; shipping block; included-cases count shown; one item per patient.
- A `CartRefused` from the service re-renders the `options` step with its message; the wizard data is kept.
- Session storage holds only the patient code and choices (no name, no maker credential); no name in any URL, log or message.
- Each `?next=` return link brings the doctor back to the step they left.
- A choice of three or more options is a `<select>` or a priced option list (see Decisions); two options are cards with a short explanation.
- Works without JS (each step is a plain POST); EN and ES strings; layout follows 9.14 (shared width, 640px form measure).
- Links keep working: `start_a_case`, and `start_a_case_tps` (old path `start-a-case/<code>/`) redirects to the wizard's first step.

**Never:**
- No change to `cart/service.py`'s rules or signatures, the cart page, checkout, or the catalogue.
- No new pricing logic: prices come from `offers_for`, `virtuaortho_lines` and `resolver.price` as today.
- No hard-coded add-on, size or maker rules in forms or JS.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| TPS happy path | TPS → brand, Invisalign (login saved) → medium + Junior II | Item in cart, redirect to cart | No error expected |
| Missing maker login | brand, Smartee, no saved login | `maker` step shows warning + save link; cannot continue | Refused, link returns to `maker` |
| In-house ICE | TPS → in-house → ICE | ICE item, no maker | No error expected |
| VO happy path | VO → DigitalTwin → medium + Byte Sync, default address | Item with address; ODB-DT not offered as a choice | No error expected |
| Spark quantity | VO One → Spark on Demand, 3 aligners | `options` re-renders with the quantity message | CartRefused QUANTITY |
| No VO offer | rest-of-EU doctor | VirtuaOrtho card greyed out with "Not included in your plan" + subscription link; a forged VO post is refused | No error expected |
| Live work order | patient with a live TPS | `options` re-renders with the refusal | CartRefused |
| Back | on `options`, press Back | `maker` shows the earlier choice | No error expected |

- Decision (user, 2026-11-24): the wizard engine is django-formtools 2.7 (`NamedUrlSessionWizardView`); if the suite shows an incompatibility with Django 6.1, the implementer stops and reports, and the user decides on an own step view.
- Decision (user, 2026-11-24): a doctor without a VirtuaOrtho offer still sees the product step; the VirtuaOrtho card is greyed out (not selectable, not submittable) with "Not included in your plan" and a link to the subscription levels page (`upgrade`).
- Decision (user, 2026-11-24): lengths on the last page are a priced option list (one row per length: radio + price; the medium row shows its Junior add-ons), not a select; re-evaluated after the user's by-eye test.

</frozen-after-approval>

## Code Map

- `cart/urls.py:10-17` -- `start_a_case`, `start_a_case_patient`, `start_a_case_tps` (`start-a-case/<str:code>/`), cart URLs. Add the wizard URL `start-a-case/<str:code>/<str:step>/` (name `start_a_case_step`); `start_a_case_tps` becomes a redirect to step `product`; `start_a_case_patient` redirects to it too.
- `cart/views.py` -- `_fiscal_block` :87, `_shipping_block` :95, `_options` :149, `_virtuaortho_options` :178, `_virtuaortho_options` retainers hide :202, `_step_two_context` :232 (maker-login warning, included cases), `start_a_case_tps` :247 (hand-parsed POST; dispatch at :252, :271), `_line` :299. Move the option builders and blocks to `cart/wizard.py` (new) with the `CaseWizard(NamedUrlSessionWizardView)`, `condition_dict` for the TPS/VO branches, and `done()`.
- `cart/forms.py` (new) -- `ProductForm`, `MakerForm` (choices from `cart/models.py:21 MAKER_CHOICES`, `OTHER_NAME_MAX_LENGTH` 60), `VoTypeForm`, `TpsOptionsForm`, `VoOptionsForm`; choices are built from the service helpers per doctor and patient, so `clean()` only checks membership; the service stays the authority.
- `cart/service.py` -- `add_tps` :178, `add_virtuaortho` :248, `_maker` :160, `offered_tps`, `offered_ice`, `offered_virtuaortho`, `variants_offered`, `virtuaortho_lines` :124, `CartRefused` codes :59; read-only here.
- `cart/templates/cart/choose.html` (141 lines) -- replaced by `cart/wizard/product.html`, `maker.html`, `vo_type.html`, `options_tps.html`, `options_vo.html` with a shared step header (patient code, step n of 3, Back).
- `static/js/case-wizard.js` (75 lines) -- keep only: reveal the maker select and "other" name, hide add-ons not offered for the chosen size (`data-offered`); server re-validates.
- `requirements.txt` (exact pins), `mytreatplan/settings.py` INSTALLED_APPS -- add `django-formtools==2.7` and `formtools`. 2.7 declares Django 5.2/6.0 and requires Django>=5.2; the portal runs 6.1.2.
- `accounts/page_records.py:44` PORTAL_PREFIXES (paths stay under `start-a-case/`); `mytp_publicsite/tests/test_settings_staging.py:225-293` PORTAL_URL_NAMES -- add `start_a_case_step`.
- Tests: `cart/tests/test_views.py` (22, all HTTP on old markup) and the last 14 of `cart/tests/test_virtuaortho.py` (from :250, `step2()` helper) are rewritten for the steps; `test_service.py` (27), the first 24 of `test_virtuaortho.py`, `test_checkout.py`, `test_pages.py` must pass unchanged.
- Depends on 9.15 (patient chooser on step 1) being merged first.

## Tasks & Acceptance

**Execution:**
- [ ] `requirements.txt`, settings -- add django-formtools.
- [ ] `cart/forms.py` -- the five forms.
- [ ] `cart/wizard.py`, `cart/urls.py`, `cart/views.py` -- wizard view, conditions, `done()`, CartRefused handling, redirects from the old URLs; remove the old step-2 view code.
- [ ] `cart/templates/cart/wizard/*.html` -- the step templates; remove `choose.html`.
- [ ] `static/js/case-wizard.js` -- trimmed as in the Code Map.
- [ ] `locale/es/LC_MESSAGES/django.po` -- Spanish strings.
- [ ] `cart/tests/test_wizard.py` (new, replaces the HTTP tests) -- every matrix row, the `?next=` return to the step, no name in session data or URLs, old URL redirect.
- [ ] `cart/tests/browser/test_wizard_flow.py` (new) -- Playwright: TPS branded path and VO DigitalTwin path to the cart.

**Acceptance Criteria:**
- Given an Iberia Advanced doctor with a saved Invisalign login, when they go patient → TPS → brand/Invisalign → medium + Junior II → Add to cart, then the cart shows that line at the same price the old wizard gave.
- Given the same doctor, when they go VO → DigitalTwin → medium + Byte Sync, then the cart line carries the default address.
- Given the service test files, when the suite runs, then they pass unchanged.

## Verification

**Commands:**
- the portal suite command (local-dev-notes) -- expected: 0 failures besides the known WeasyPrint `test_pdf.py` on this Mac

**Manual checks (if no CLI):**
- On dev, EN/ES, desktop and phone: both branches end to end; Back on each step; maker-login link returns to the maker step.
