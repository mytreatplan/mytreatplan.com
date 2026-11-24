---
title: 'Product pages and start-a-case links'
type: 'feature'
ticket: '5'
created: '2026-11-24'
status: 'done'
baseline_revision: '91282f57d2c27d7a50d92439c286dc0568120e45'
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

**Problem:** The home's VirtuaOrtho and Planning Services tiles open "coming soon" placeholders, so a doctor cannot see which products their subscription offers, at what price, before starting a case.

**Approach:** Replace the two placeholders with product pages listing only what `offers_for` returns: catalogue names and descriptions in the page's language, net prices, offered add-ons, a short translated intro and a "Start a case" button to the wizard (the home's green bar already opens it).

## Decisions

- Decision (user, 2026-11-24): no education page in 9.5 (parked; no products, no billing rule, no order path without a patient); recorded in deferred-work.
- Decision (user, 2026-11-24): Planning Services = ICE + branded TPS (by maker) + in-house printing TPS.
- Decision (user, 2026-11-24): each row shows name and description (catalogue, page language), the net price (Spark "per aligner"), and the add-ons offered on it with their prices; each page has a short translated intro and a "Start a case" button to `start_a_case`.
- Decision (user, 2026-11-24): when a page has nothing offered, it says the products are not included in the doctor's subscription; the home tile stays.
- Decision (user, 2026-11-24): retainers are listed with the note "after a finished treatment".
- Decision (user, 2026-11-24): the VirtuaOrtho page groups by One and DigitalTwin, as in the wizard: sizes, ODB (under DigitalTwin with ODB DigitalTwin included in its price), retainers, and Spark under One only.
- Decision (user, 2026-11-24): merge, staging checks over SSH, branch pushes and the completion commit to master are DELEGATED for this story.

## Boundaries & Constraints

**Always:** Same URL names and paths (`virtuaortho`, `planning_services`); doctor-only like the wizard (login required; staff get 404). Products only from `offers_for` via the `cart.service` wrappers, never by code; add-ons only from `resolver.add_ons`; prices with the `money` filter. EN and ES. Rows that cannot be sold are not listed (unsized One/DigitalTwin; DigitalTwin ODB without ODB-DT), as in the wizard.

**Never:** Ordering from the pages, deep links to step 2, education, catalogue or admin-site changes, new models.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| VO, Iberia Advanced | VO catalogue offered | One and DigitalTwin groups with sizes, ODB, retainers note, Spark per aligner under One; add-ons with prices | — |
| VO, rest-of-EU Advanced | no VO offered | "not included in your subscription" | — |
| Planning, Basic | ICE + TPS offered | ICE, branded sizes, in-house sizes with add-ons and prices | — |
| No subscription offers | `offers_for` empty | both pages show the not-included message | — |
| Spanish | `/es/` | intro, labels and catalogue texts in Spanish | English fallback for a missing ES text |
| Staff | staff user | — | 404 |

</frozen-after-approval>

## Code Map

- `accounts/urls.py:~69-72`, `accounts/views.py:1256-1273` (`COMING_SOON_AREAS`, `coming_soon`) -- the two placeholders; remove their entries, keep names and paths.
- `accounts/templates/accounts/home.html:16,28,36` -- tiles link `virtuaortho`, `planning_services`; green bar already `{% url 'start_a_case' %}` (no change needed).
- `accounts/tests/test_doctor_area.py:25-26,72`, `mytp_publicsite/tests/test_settings_staging.py:222-253` -- coming-soon expectations to update; `accounts/page_records.py:41-47` `PORTAL_PREFIXES` already lists both prefixes.
- `cart/service.py:79-105` `offered_tps`, `offered_ice`, `offered_virtuaortho`, `variants_offered`, `ODB_DT` -- reuse; `catalogue/resolver.py:487` `offers_under` already drops VO for rest-of-EU.
- `cart/views.py` `_options`, `_virtuaortho_options`, `size_label` -- the wizard's grouping and price rules (ODB-DT in the DigitalTwin ODB price; unsized rows skipped); share a helper rather than copy it.
- `catalogue/models.py` `Product.public_name`, `description_in(lang)` (plain text with `\n`, render with `linebreaksbr`), `per_unit`; `AddOn.public_name`, `description_in`; `resolver.add_ons(product)`, `resolver.price`.
- `mytp_publicsite/templatetags/money.py` `money`; `cart/templates/cart/choose.html:56-58,97` -- price and "per aligner" precedent; `accounts/templates/accounts/coming_soon.html` -- page frame to follow.
- `patients/tests/conftest.py` `virtuaortho_catalogue`, `advanced`; `cart/tests/test_virtuaortho.py` `basic` -- fixtures to reuse.

## Tasks & Acceptance

**Execution:**
- [ ] `cart/views.py` (or a new `cart/pages.py`) -- views `virtuaortho_page`, `planning_services_page` building rows from the wrappers; share the grouping/price logic with the wizard.
- [ ] `cart/templates/cart/virtuaortho.html`, `cart/templates/cart/planning_services.html` -- intro, grouped rows (name, description, price, add-ons with prices, retainers note), not-included message, "Start a case" button.
- [ ] `accounts/urls.py`, `accounts/views.py` -- route the two names to the new views (or move them to `cart/urls.py`), drop their `COMING_SOON_AREAS` entries.
- [ ] `locale/es/LC_MESSAGES/django.po` -- Spanish for every new string; compile.
- [ ] `cart/tests/test_pages.py`, `accounts/tests/test_doctor_area.py`, `mytp_publicsite/tests/test_settings_staging.py` -- one test per matrix row; the home tiles reach the pages and the bar reaches the wizard.
- [ ] `_bmad-output/initiative-mytreatplan-portal-admin/deferred-work.md` -- entry: education page (parked).

**Acceptance Criteria:**
- Given an Iberia Advanced doctor on the home, when they open the VirtuaOrtho tile, then the page lists the offered VO products with prices and its "Start a case" button opens step 1 of the wizard.
- Given any doctor on the home, when they press the green bar, then step 1 of the wizard opens.

## Implementation Notes

- 722f0e6 + c3bffb4. `cart/service.virtuaortho_lines` holds the wizard's VO row/price rule, shared by the wizard and the VO page; views in `cart/pages.py`, routes moved to `cart/urls.py` (same names and paths). ODB is listed under One and DigitalTwin (DigitalTwin price includes ODB-DT), as in the wizard. Education entry added to deferred-work.
- Local suite: 4312 passed, 17 skipped; 8 local-only `invoices/tests/test_pdf.py` WeasyPrint failures.

## Plan Change Log

## Review Triage Log

Pass 1 (quick lens, opus): high 0, medium 1, low 5, false 0, maybe-false 0; all 6 patched (c3bffb4).

| # | Finding | Verdict | Route | Evidence / action |
|---|---------|---------|-------|-------------------|
| 1 | Rest-of-EU Advanced row tested with a Basic doctor | medium | patch | DE doctor moved to a rest-of-EU Advanced; EU VO rules removed as in the real catalogue. |
| 2 | Spanish row never checks catalogue texts or the English fallback | low | patch | ES name/description and EN fallback asserted. |
| 3 | Percentage add-ons not localized ("+10.00 %" on /es/) | low | patch | `floatformat:2` with sign; test on /en/ and /es/. |
| 4 | `cart/urls.py` comment lists only two prefixes | low | patch | Updated. |
| 5 | New .po entries carry wrong `#:` locations | low | patch | Corrected. |
| 6 | `accounts/views.py` comment points at accounts.urls | low | patch | Updated. |

## Verification

**Commands (sandbox disabled):**
- Portal, from the repo root: `export FIELD_ENCRYPTION_KEYS="test1:dGVzdC1vbmx5LWZpZWxkLWtleS1ub3Qtc2VjcmV0ISE=" BLIND_INDEX_KEY="dGVzdC1vbmx5LWJsaW5kLWluZGV4LWtleS0zMmJ5dGU="; export DATABASE_URL="sqlite:///$TMPDIR/s95.sqlite3"; ../.venv/bin/python -m pytest -q -p no:cacheprovider --tb=line` -- expected: 0 failures except the 8 local-only `invoices/tests/test_pdf.py` WeasyPrint failures (run `../.venv/bin/python manage.py compilemessages` after editing the .po); `makemigrations --check --dry-run` -- no changes.
- Branch `story/9-5-product-pages` already exists in this checkout. Commit on it (new commits); never push, tag or amend.

## Staging

- Merged: PR #134 → 1cd06bc (2026-11-24); deploy run 45370354547 green (dev and dev-ae); PR CI green on both runs. Local suite 4312 passed (8 local-only WeasyPrint PDF failures).
- Checks on dev (server-side, test client, one transaction rolled back, nothing left behind), doctor of patient EU-0BNY91JQ switched inside the transaction:
  1. SEED-ADV-IBERIA (ES): the VirtuaOrtho page lists One and DigitalTwin with sizes and prices (e.g. DigitalTwin Medium 1,549.00 EUR), Byte Sync and Junior on medium, ODB (DigitalTwin price includes ODB DigitalTwin), Spark per aligner under One only, retainers with their note; Spanish page with localized amounts; "Start a case" links to the wizard. Passed.
  2. SEED-ADV-ROEU (DE): "not included in your subscription", no Start button. Passed.
  3. Planning Services: ICE, branded and in-house TPS with prices and add-ons (One arch -25.00 %). Passed.
  4. The home (/en/home/) links both pages and the wizard; the wizard opens (200). Passed.
- Seen: free add-ons (Junior I/II) read "0.00 %"; a wording such as "included" may read better (by-eye check).
- Owed by the user: by-eye check of both pages, EN/ES, desktop and phone.

