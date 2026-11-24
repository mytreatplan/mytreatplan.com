---
title: 'Responsive layout'
type: 'feature'
ticket: '14'
created: '2026-11-24'
status: 'done'
baseline_revision: '5ff5ad0cbd3f8fac8a8cd94286f4359924fd5490'
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

**Problem:** Doctor pages sit in a narrow fixed column (480px by default in `doctor_base.html`, with ad-hoc page overrides of 760–1080px), so tables and the wizard look cramped on a laptop; the "Add a patient" dialog is too tall for a laptop screen. Tailwind's `@source` list does not scan `patients`, `cart`, `payments`, `invoices` or `storage` templates, so classes used only there (e.g. the dialog's `tablet:max-h-[90dvh]`) may never be generated.

**Approach:** One shared content width for the doctor area (header and panel aligned), set once in `doctor_base.html`; drop the per-page width overrides; tables get a horizontal-scroll wrapper; forms keep a readable measure inside the wide page; scan every app's templates; the dialog's height is capped to the viewport with its body scrolling and its buttons always visible. The public site already uses the 1440px canvas: only its contact form and any narrow column found get checked.

## Boundaries & Constraints

**Always:**
- Works at 390, 1024 and 1440 px wide with no horizontal page scroll (a wide table scrolls inside its own wrapper).
- Mobile (< 701px, the `tablet` breakpoint) keeps today's phone layout.
- Dialog: fits a 768px-tall viewport; header and buttons visible, form body scrolls.
- EN and ES pages render the same layout; no copy changes.

**Decisions:** see the end of this block.

**Never:**
- No change to mytp-admin, signup/login pages' narrow form column (portal_base.html keeps 480px), legal pages' 760px reading measure, the invoice PDF.
- No new CSS framework, no committed `static/css/site.css`, no JS layout code.
- No behaviour change to views, forms or the wizard flow (UI-3 redesigns it).

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| Laptop | 1440×800, patient list with 30 rows | Panel uses the shared width; table fills it | No error expected |
| Tablet | 1024 wide, cart and checkout | Content fills width minus gutter | No error expected |
| Phone | 390×844, invoices table | No page scroll sideways; table scrolls in its wrapper | No error expected |
| Short laptop | 1366×768, "Add a patient" dialog open | Dialog ≤ viewport height; Save visible; fields scroll | No error expected |

- Decision (user, 2026-11-24): the doctor area's shared content width is 1280px, a new `--container-portal` token (`max-w-portal`), with header and panel aligned.
- Decision (user, 2026-11-24): single-column forms keep a readable 640px cap inside the wide page; short paired fields (name/surname, city/postcode) sit side by side from the `tablet` breakpoint up.

</frozen-after-approval>

## Code Map

- `assets/css/site.css:8-17` -- `@source` list; add `patients`, `cart`, `payments`, `invoices`, `storage` templates (and `static/js/dialog.js`, `static/js/case-wizard.js` if they set classes). `@theme` (:19-112): add a `--container-portal` token (see Open Questions).
- `templates/doctor_base.html:21` -- header inner `max-w-[1100px]`; `:66` panel `{% block portal_width %}max-w-[480px]`. Both become the shared token.
- `templates/portal_base.html:29` -- signup/login 480px; leave as is.
- Overrides to remove (pages then inherit the shared width): `accounts/templates/accounts/settings_base.html:11` (880), `accounts/templates/accounts/home.html:13` (1080), `subscriptions/templates/subscriptions/upgrade.html:11` (1040), `payments/templates/payments/checkout.html:12` (760).
- Inner caps to review: forms `max-w-[640px]` (`patients/_patient_form.html:4`, settings maker/fiscal/shipping forms) and `max-w-[520px]` (settings contact change/code); `cart/choose.html:26,78`, `cart/planning_services.html:13,18`, `cart/virtuaortho.html:13,18` (720); `cart/cart.html:38` voucher input 320 (fine); `payments/test_order.html:24` (420).
- Tables: `patients/list.html:33` already has `overflow-x-auto`; add it to `invoices/list.html:14` and `payments/checkout.html:22`.
- Dialog: `patients/templates/patients/list.html:19` and `cart/templates/cart/start.html:21` (identical markup) -- extract to one include `patients/_patient_dialog.html`; `static/js/dialog.js` unchanged.
- Public site: `mytp_publicsite/templates/publicsite/contact/_form.html:10`, `_thanks.html:6` (880) -- keep unless they look narrow at 1440.
- Tests constraining: `subscriptions/tests/test_upgrade.py:258,412` assert `max-w-[1040px]` (update to the new class); `mytp_publicsite/tests/test_legal_seo.py:47` (`max-w-legal`, keep). Browser tests: Playwright at 390×844 in `accounts/tests/browser/test_doctor_area.py:53`, `test_personal_data.py:67`, `signup/tests/browser/test_success.py:145`; conftests in `accounts|signup|mytp_publicsite/tests/browser/`.

## Tasks & Acceptance

**Execution:**
- [ ] `assets/css/site.css` -- add the missing `@source` dirs and the `--container-portal` token -- classes in those templates are generated; one width token.
- [ ] `templates/doctor_base.html` -- header and panel use `max-w-portal`; default `portal_width` block becomes that class -- one width everywhere.
- [ ] `accounts/.../settings_base.html`, `home.html`, `subscriptions/.../upgrade.html`, `payments/.../checkout.html` -- remove the `portal_width` overrides -- pages inherit.
- [ ] Forms and wizard templates listed above -- apply the forms decision -- readable fields in a wide page.
- [ ] `invoices/list.html`, `payments/checkout.html` -- wrap tables in `overflow-x-auto` -- no page scroll on phones.
- [ ] `patients/templates/patients/_patient_dialog.html` (new), `patients/list.html`, `cart/start.html` -- one dialog include; `max-h-[calc(100dvh-2rem)]` flex column with a scrolling body and a fixed footer for the buttons -- fits a 768px screen.
- [ ] `subscriptions/tests/test_upgrade.py` -- assert the shared class instead of 1040.
- [ ] `accounts/tests/browser/test_layout.py` (new) -- Playwright: at 1440×800 the panel on the patient list, cart and settings pages is wider than 1000px; at 390×844 `document.scrollingElement.scrollWidth <= innerWidth` on the same pages and invoices; at 1366×768 the dialog's bounding box fits the viewport and its submit button is in view.

**Acceptance Criteria:**
- Given a doctor at 1440px, when they open the patient list, home, cart, checkout or settings, then the content column has the same width as the header.
- Given a phone at 390px, when any doctor page with a table loads, then the page does not scroll sideways.
- Given `npm run build:css`, when the built CSS is searched, then the dialog's height-cap class is present.

## Implementation Notes

- Paired short fields (name/surname, city/postcode) already sat side by side from `tablet` up in the patient, fiscal and shipping forms; no form change was needed beyond the checkout cap.
- The browser layout test builds a paid and an open order through the catalogue fixtures (`interface_settings`, `admin`) so the patients, invoices and checkout tables render.

## Review Triage Log

Pass 1 (quick, Opus; 2026-11-24): 7 findings — high 1, medium 3, low 3, false 0, maybe-false 0. All patched by the implementer.

| # | Finding | Verdict | Route | Evidence / action |
|---|---|---|---|---|
| 1 | `invoices/list.html` scroll wrapper never closed | high | patch | `</table>` ran straight into `{% else %}`, breaking the panel nesting for any doctor with invoices; `</div>` added. |
| 2 | Header and panel not aligned (panel inner div held both `max-w-portal` and `px-gutter`) | medium | patch | Panel content was 1280 − 2 gutters; `px-gutter` moved to `[data-doctor-panel]`, as on the header. |
| 3 | Laptop test did not compare with the header, skipped checkout | medium | patch | Test now compares content boxes within 1px and visits a checkout page. |
| 4 | Phone test never rendered a table | medium | patch | Test now creates a patient, a paid order (invoice) and an open order, and asserts the tables are visible. |
| 5 | Checkout form stretched to the full width | low | patch | Against the 640px forms decision; form capped at `max-w-[640px]`, order table stays full width. |
| 6 | `test_upgrade.py` assertions always true (header carries `max-w-portal`) | low | patch | Assertions now match the panel's own markup. |
| 7 | `doctor_base.html` comment wrong about portal_base | low | patch | Comment corrected. |

## Design Notes

Forms in a wide page: a 2-column grid from `tablet` up for paired fields (name/surname, city/postcode) is fine, but single fields should not stretch past a readable measure.

## Verification

**Commands:**
- `npm run build:css && grep -c "max-h-\\\\\[calc" static/css/site.css` -- expected: ≥ 1
- the portal suite command (local-dev-notes) -- expected: 0 failures except the known WeasyPrint ones on this Mac
- `pytest accounts/tests/browser/test_layout.py` -- expected: pass

**Manual checks (if no CLI):**
- By eye EN/ES at 1440, 1024, 390 on dev: patient list, wizard, cart, checkout, settings, home; dialog on a 768px-tall window.

## Staging

- Merged: PR #136 → 4a14f047cc607f16b65bbf79e95abd13708686f8 (2026-11-24). CI on the PR green (test job); master deploy run 45475162510 green on dev and dev-ae. Local suite: 4344 passed, 8 failed (known WeasyPrint `test_pdf.py` on this Mac), 17 skipped.
- Check 1 passed (dev): the built `static/css/site.css` contains `max-w-portal` and the dialog's height cap.
- Check 2 passed (dev, server-side render for the doctor of EU-0BNY91JQ): home, patients, invoices, cart and settings return 200 with the panel's `mx-auto max-w-portal` div, balanced `<div>` markup, and each table inside an `overflow-x-auto` wrapper. Checkout of the doctor's last order redirected (302: order already paid), not rendered.
- Check 3 (by eye, EN/ES at 1440/1024/390 px, dialog on a 768 px tall window): owed by the user, not reported yet.
