---
title: 'Public legal notice (aviso legal) and contracting conditions pages'
type: 'feature'
ticket: ''
created: '2026-11-21'
status: 'in-progress'
baseline_revision: '3eae4b5750b4e7e12fdb8079035ba96db37cffba'
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

**Problem:** Santander will not finish setting up Redsys card payments until the public website shows two things. The first is a legal notice that names the merchant, MyTPDSO Spain S.L., with its CIF. The second is the contracting terms. The EU site has neither. It names only MyTPDSO Limited (Ireland), and the contracting conditions are visible only inside signup (LegalDocument kind CONDITIONS).

**Approach:** Add two public pages to the EU site, with language prefixes: an "Aviso legal" page (the data LSSI art. 10 requires) and a "Condiciones de contratación" page. Link both from the public footer and list both in the sitemap. Claude drafts the texts. The user's lawyer reviews and approves them before LEGAL_TEXTS_FINAL is set.

Decisions (user, 2026-11-21):
- MyTPDSO Limited stays the owner of the website. It also sells the paid services in every country the .com serves, except Spain and Portugal. MyTPDSO Spain S.L. sells them in Spain and Portugal. The legal notice says both.
- The S.L.'s data lives once, in the EU region profile (`regions/profiles.py`).
- The conditions page renders the current CONDITIONS LegalDocument, the same text accepted at signup. Claude also writes a full es+en Markdown draft. The user passes it to the lawyer, then publishes it as a new version in the admin site.
- Both pages are EU only. The UAE site gets no links and a 404. "A product with a price" is a separate story (deferred-work).

S.L. data (user, 2026-11-21):
- Name and CIF: MyTPDSO Spain S.L., B93858371.
- Address: Avda. Labradores 1, Edificio COS, Planta 4, Oficina 6, 28760 Tres Cantos (Madrid).
- Registro Mercantil de Madrid: Hoja M-894040, Inscripción 1.ª, folio electrónico; IRUS 1000477547891.
- Email: sayhi@mytreatplan.com.

## Boundaries & Constraints

**Always:**
- Both pages build on `LegalPageView`/`base_legal.html`, so they keep the "provisional text" notice while LEGAL_TEXTS_FINAL is False.
- The legal notice exists in Spanish and English. Spanish is the text for the lawyer.
- Company data is rendered from the profile, never retyped in templates.
- New strings use gettext, with es .po entries and a recompiled .mo.
- Whether a region has the pages depends on its profile, not on `REGION == 'eu'`.

**Never:**
- No change to the EU `company` (MyTPDSO Limited), the /terms/ or /privacy/ texts, invoices, BillingCompany or signup.
- No price list or product page.
- No data migration that changes CONDITIONS. Publishing the new text is the user's step in the admin site.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| Legal notice es | GET /es/legal-notice/ on EU | 200, Spanish body. The Irish Ltd is the owner (name, registration, office, email). An "España y Portugal" section names the S.L. with CIF, address, Registro Mercantil data and email | — |
| Legal notice en | GET /en/legal-notice/ on EU | 200, English body, same data | — |
| Conditions | GET /es/ or /en/conditions/ on EU | 200, title and Markdown body of `LegalDocument.objects.current('conditions')` in that language | none published → 404 |
| UAE | either page on the AE portal | 404; the footer has no such links; not in the sitemap | — |
| Footer EU | any public page | "Legal notice" and "Contracting conditions" links after Privacy and Terms, translated on /es/ | — |
| Sitemap EU | /sitemap.xml | both pages in en and es with alternates | — |

</frozen-after-approval>

## Code Map

- `regions/profiles.py` -- `RegionCompany` (name, registration as gettext_noop, registration_number, office), `RegionOffice`, `RegionProfile`, `PROFILES`. Keep `company` as it is.
- `regions/context_processors.py` -- `company_registration`, `company_address` (country named in the active language), and `region_company`. Reuse them for the S.L.
- `mytp_publicsite/views.py:114` -- `LegalPageView`: picks the `_<body>_body_<lang>.html` body (falls back to English) and sets `legal_company_*` inside `translation.override(body_lang)`. `PrivacyView`/`TermsView` are the pattern.
- `mytp_publicsite/urls.py` -- routes. Paths are the same in every language (`terms/`), under `i18n_patterns`.
- `mytp_publicsite/templates/publicsite/terms.html`, `legal/base_legal.html`, `legal/_terms_body.html` -- the shell and a numbered-`<h2>` body to copy.
- `templates/base.html:113-118` -- the footer's legal `<nav>`.
- `mytp_publicsite/sitemaps.py` -- the `PublicPagesSitemap.pages` dict.
- `legal/models.py:55` -- `LegalDocument.objects.current(kind)`; `.title`/`.body` follow the active language. `legal/rendering.py:render_markdown` (raw HTML blocked).
- `conftest.py:225` -- `ae_portal`, `publish_site_documents`. `mytp_publicsite/tests/test_legal_seo.py` (LEGAL_PAGES, footer and sitemap tests) and `test_region_company.py` (eu/ae param).
- `locale/es/LC_MESSAGES/django.po` -- the only catalogue.

## Tasks & Acceptance

**Execution:**
- [ ] `regions/profiles.py` -- Add a frozen `CountrySeller(company: RegionCompany, tax_id: str, countries: tuple[str, ...])`. Add `RegionProfile.country_sellers: tuple[CountrySeller, ...] = ()` and `contact_email: str = ''`. On EU, add one seller for ('ES', 'PT') with the S.L. data. Its registration is gettext_noop('Madrid Commercial Register, sheet M-894040, entry 1, electronic folio; IRUS 1000477547891'). Set EU contact_email to 'sayhi@mytreatplan.com'. AE has neither -- one source of truth.
- [ ] `regions/context_processors.py` -- Expose `region_has_sales_pages` (True when there are country_sellers) for the footer.
- [ ] `mytp_publicsite/views.py`, `urls.py` -- Add `LegalNoticeView` (body `legal_notice`, named `legal_notice`, path `legal-notice/`) and `ConditionsView` (`conditions`, `conditions/`). Both return 404 when the profile has no country_sellers. The notice's context carries each seller with its registration, address and country names, rendered in the body's language. The conditions view renders the current CONDITIONS document (`render_markdown`), or returns 404.
- [ ] `mytp_publicsite/templates/publicsite/` -- Add `legal_notice.html`, `legal/_legal_notice_body.html` (en) and `_legal_notice_body_es.html` (es). The numbered sections: website owner; sale of services in Spain and Portugal; terms of use (link to /terms/); contracting conditions (link); privacy (link); intellectual property; applicable law. Also add `conditions.html`, which shows the document's title and body in the legal shell.
- [ ] `templates/base.html` -- Add the two links inside `{% if region_has_sales_pages %}`.
- [ ] `mytp_publicsite/sitemaps.py` -- Add both url names only when the profile has country_sellers.
- [ ] `locale/es/LC_MESSAGES/django.po` (+ `.mo`) -- Add "Legal notice" → "Aviso legal", "Contracting conditions" → "Condiciones de contratación", the registration line and the page titles.
- [ ] `mytp_publicsite/tests/test_legal_pages.py` -- Cover every matrix row on eu and ae, and check that no S.L. data appears on AE pages.
- [ ] `_bmad-output/initiative-mytreatplan-portal-admin/legal-drafts/condiciones-contratacion.md` -- Write the full es text first, then en, in Markdown that the admin can paste. Sections: provider by country (both companies' data); recipients (dental professionals only, B2B); services and prices (shown in the portal before ordering, EUR, VAT apart); ordering steps, contract language and archiving (LSSI art. 27); payment (card through Redsys or Revolut, bank transfer; MyTreatPlan stores no card data); invoicing by the selling company; delivery; cancellation (no consumer withdrawal right); liability (clinical decisions stay with the doctor); complaints; data protection; law and courts (Spanish law and the courts of Madrid for the S.L.; Irish law for the Ltd). End with a short "Notas para el abogado" list of the open points.

**Acceptance Criteria:**
- Given LEGAL_TEXTS_FINAL is False, when either page is opened, then it shows the provisional-text notice.
- Given the conditions page on /es/, when the current CONDITIONS version has Spanish text, then the Spanish title and body are shown.
- Given the full suite, when it runs, then it has 0 failures.

## Design Notes

The legal notice names two companies because each country has its own seller: the Ltd owns the site, and the S.L. is the seller for ES/PT, which is what Santander checks. The Irish Ltd's VAT/NIF is not in the repo, so the notice shows its company number only. This goes on the lawyer's notes list.

## Verification

**Commands:**
- The full suite (local-dev-notes) -- expected: 0 failures.
- `../.venv/bin/python manage.py compilemessages -l es` -- expected: no errors.

**Manual checks:**
- On dev: /es/legal-notice/, /en/legal-notice/ and /es/conditions/ render, and the footer shows both links.
