---
title: 'Region company in the footer and legal pages'
type: 'feature'
ticket: '6'
created: '2026-11-14'
status: 'built'
baseline_revision: '822382e4062881d04b1b2de76ce8b4b6f236d7c8'
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

**Problem:** Every site shows the Irish company in its footer and structured data, the public Privacy and Terms pages name the UAE company on both sites, and the documents a doctor accepts at signup have no region, so dev-ae cannot show its own company or its own texts (R5).

**Approach:** The footer, the structured data and the legal pages take the company from the region profile; legal documents get a region and each site reads only its own; texts that are not the lawyer's final ones are visibly marked provisional.

## Boundaries & Constraints

**Always:**
- Existing acceptances keep pointing at the document they accepted.
- A site never shows or offers another region's document.
- Region codes appear only where `regions/tests/test_guard.py` allows them.
- EN and ES for every new visible string.

**Never:**
- No mytp-core, contract or client-sync change (consent does not cross the wire).
- No change to billing (`catalogue/billing.py`), the country map, or the "MyTPDSO group" consent wording.
- No final legal text is written here; the lawyer's texts arrive later through the admin and template updates.

## Decisions

- Decision (user, 2026-11-14): each site gives its existing documents its own region (dev: eu, dev-ae: ae); dev-ae's existing rows become its placeholders. This replaces the ticket's "existing documents get region eu".
- Decision (user, 2026-11-14): acceptances get no region field; the accepted document carries it.
- Decision (user, 2026-11-14): the public Privacy and website Terms pages stay templates.
- Decision (user, 2026-11-14): one text for both regions, with the company name, registration and office taken from the region profile, marked provisional on both.
- Decision (user, 2026-11-14): signup documents carry a per-document `provisional` flag (true for every existing one), shown as a notice on the signup legal step; the public pages show a notice while the site's `LEGAL_TEXTS_FINAL` setting is off.
- Decision (user, 2026-11-14): the region profile gains the registered office (Dublin for eu; the Meydan address of today's privacy text for ae), used in the footer and the home page's structured data.
- Decision (user, 2026-11-14): the UAE company is spelled "MyTPDSO L.L.C-FZ".
- Decision (user, 2026-11-14): the footer shows the profile's registration line as a whole.
- Decision (user, 2026-11-14): merge and staging for this entry are delegated.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| Footer, eu site | any page | "MyTPDSO Limited", "Company number 815526 (Ireland)", the Dublin office; nothing of the UAE company | none |
| Footer, ae site | any page | "MyTPDSO L.L.C-FZ", "License 2542361.01 (Dubai)", the Meydan office; nothing of the Irish company | none |
| Structured data | home page, each region | legal name, identifier and address of the region's company | none |
| Privacy / Terms page | each region, EN and ES | the text names the region's company and office only | none |
| Public provisional notice | `LEGAL_TEXTS_FINAL` off / on | notice shown / not shown on Privacy and Terms | none |
| Migration on a site with documents | rows from before | every row gets the site's region and `provisional` true; acceptances unchanged | reversible |
| Current document | documents of both regions exist | only the site's region is read, at signup, in erasure and in the admin status | none |
| Signup with no document of the site's region | only another region's rows | refused as today when documents are missing | `documents_missing` |
| New version | saved for a kind | version = highest of that region and kind + 1; the same kind and version may exist in another region | a duplicate in the same region is refused |
| Signup legal step | a provisional document / a final one | notice shown / not shown, EN and ES | none |
| Row inserted without a region | raw SQL, as the cross-sites test does | accepted through the column default | none |

</frozen-after-approval>

## Code Map

- `regions/profiles.py:27-31, 72-73, 85-86` -- `RegionCompany(name, registration)`; eu "MyTPDSO Limited" / "Company number 815526 (Ireland)", ae "MyTPDSO L.L.C-FZ" / "License 2542361.01 (Dubai)". No address. Pinned by `regions/tests/test_profiles.py:14-15,32-33`. `settings.REGION_PROFILE` (`mytreatplan/settings.py:46-50`); no context processor exposes it (`:470-474`).
- `templates/base.html:108-110` -- hard-coded footer (name, "Company number" 815526, Dublin office); `locale/es/LC_MESSAGES/django.po:536-541`.
- `mytp_publicsite/views.py:25-68` -- JSON-LD legalName, identifier, address; `:110-139` `LegalPageView` picks `_<body>_body[_<lang>].html`.
- `mytp_publicsite/templates/publicsite/legal/_privacy_body.html:17,173`, `_terms_body.html:17,126` -- UAE company and Meydan address in the text; `base_legal.html:33-37` -- the notice block (place for the provisional notice).
- `mytreatplan/settings.py:118-124`, `mytp_publicsite/checks.py:22-37` -- `LEGAL_TEXTS_FINAL` and deploy check `mytp_publicsite.E001`.
- `legal/models.py:22-37` `current(kind)`, `current_by_kind()` (no region); `:40-106` `LegalDocument` (kinds terms, conditions, ormco_tps, commercial_consent; version = max of the kind + 1 in `save()`; constraint `legal_document_kind_version` `:76`); `:109-139` `LegalChoice` (FK PROTECT).
- `legal/migrations/0002_draft_documents.py` (v1 drafts on every site), last `0005_uid_unique.py`. Precedent for the column + fill: `accounts/migrations/0012`, `0013` (`db_default`, RunPython reading `settings.REGION_PROFILE`).
- Readers: `signup/steps.py:171,178,191,198-205`, `signup/forms.py:383-432`, `signup/views.py:425-444`, `accounts/erasure.py:65-73`, `legal/admin.py:49`. `signup/templates/signup/step_legal.html` -- the signup legal step.
- Tests that pin today's behaviour: `mytp_publicsite/tests/test_legal_seo.py:53,69,86-100,191-197`, `test_home.py:29`, `test_cross_sites.py:98,157-200` (raw SQL insert without a region: the column needs a `db_default`), `regions/tests/test_suggestion.py:268`, `legal/tests/test_legal.py:29,60`, `signup/tests/test_legal_step.py`, `signup/tests/browser/conftest.py:49-61`, `accounts/tests/test_erasure.py:43,58`, `signup/tests/test_retention.py:38`.
- Region switch in tests: `ae_portal` (`catalogue/tests/conftest.py:15-20`).

## Tasks & Acceptance

**Execution:**
- [ ] `regions/profiles.py`, `regions/tests/test_profiles.py` -- `RegionCompany` gains the registered office (lines and country code) for both regions; the ae name stays "MyTPDSO L.L.C-FZ".
- [ ] `mytreatplan/settings.py` (or a small context processor module in `regions/`), `templates/base.html`, `locale/es` -- the footer shows the profile company's name, registration line and office; the hard-coded strings and their unused translations go.
- [ ] `mytp_publicsite/views.py` -- the JSON-LD takes legal name, identifier and address from the profile.
- [ ] `mytp_publicsite/templates/publicsite/legal/_privacy_body*.html`, `_terms_body*.html`, `base_legal.html`, `mytp_publicsite/views.py` -- company name, registration and office come from the profile in every language file; a provisional notice (EN, ES) shows while `LEGAL_TEXTS_FINAL` is off; the deploy check `mytp_publicsite.E001` stays.
- [ ] `legal/models.py`, `legal/migrations/0006…`, `0007…` -- `region` (column default, then a RunPython that fills the site's region, following `accounts/migrations/0012`/`0013`) and `provisional` (existing rows true; new rows default true); the uniqueness becomes region + kind + version; `current`, `current_by_kind` and the version numbering read the site's region only; `save()` sets the site's region on a new row.
- [ ] `legal/admin.py` -- lists and edits only the site's region; `provisional` editable; status uses the region-aware `current`.
- [ ] `signup/forms.py`, `signup/steps.py`, `signup/templates/signup/step_legal.html`, `locale/es` -- the legal step shows a notice beside a provisional document.
- [ ] `legal/migrations/0006…` or `0007…` -- on a site whose company differs from the one named in the seeded draft bodies, nothing is rewritten: the rows stay as they are, flagged provisional.
- [ ] Tests (`legal/tests`, `signup/tests`, `mytp_publicsite/tests/test_legal_seo.py`, `test_home.py`, `test_cross_sites.py`, `regions/tests/test_suggestion.py`, `accounts/tests/test_erasure.py`, `signup/tests/browser/conftest.py`) -- one test per matrix row, both regions through the `ae_portal` pattern (moved to the root `conftest.py` if other apps need it); the migration test covers forward and back with existing acceptances.
- [ ] `deploy/README.md`, `README.md` -- how a final text lands: a new document version with `provisional` off in the admin; the templates updated and `LEGAL_TEXTS_FINAL` on.

**Acceptance Criteria:**
- Given the suite, when it runs, then 0 failed and `makemigrations --check --dry-run` reports no changes.
- Given `regions/tests/test_guard.py`, when it runs, then it passes with no new exemption.
- Given the templates and `.po` files, when grepped, then "815526", "Baggot", "Meydan" and both company names appear only in `regions/profiles.py`, migrations and tests (amended at review: and the two translated registration lines in `locale/es`).
- Given a database migrated from the baseline with acceptances, when the migrations run forward and back, then every acceptance points at the same document.

## Implementation Notes

## Plan Change Log

## Review Triage Log

Quick lens (Opus), 2026-11-14: 3 findings (2 medium, 1 low), all patched by the implementer; none deferred.

| # | Severity | Finding | Verdict |
|---|----------|---------|---------|
| 1 | medium | The Spanish footer and legal pages printed the registration line in English beside a translated office line | patched: the line is marked for translation in the profile and translated where rendered; ES entries added. The acceptance criterion on where "815526" may appear is amended: the `.po` file now holds the two translated registration lines |
| 2 | low | JSON-LD `identifier.value` held the whole display sentence | patched: `RegionCompany.registration_number`; the identifier carries the number |
| 3 | medium | A document saved by the previous release after 0007 kept an empty region: hidden from signup and the admin, its version number reused, a later fill or a reverse of 0006 failing on the unique constraint | patched: an empty region is read as the site's own in `of_site()`, the version numbering and the admin; a save fills it; tests added, including a row inserted between 0006 and 0007 |

Not run locally: the two migrations on PostgreSQL (the local role cannot create a test database); CI is their first PostgreSQL run.

## Design Notes

- Working notes for the implementer: the branch is `story/2.6-region-company-legal`, already created in the portal checkout; do not create another branch and do not commit. Do not touch `../mytp-core` or `../mytp-admin`.
- Downstream: epic-doctor-profile entry 1 waits on this entry; epic-regions entry 8 writes its production checks.
- Baseline suite: 3027 passed, 12 skipped.

## Verification

**Commands:**
- the portal's full suite command (SQLite, `../.venv`, sandbox off) -- expected: 0 failed
- `manage.py makemigrations --check --dry-run` -- expected: no changes
