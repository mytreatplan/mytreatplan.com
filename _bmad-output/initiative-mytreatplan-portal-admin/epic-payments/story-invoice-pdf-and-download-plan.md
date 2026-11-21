---
title: 'Invoice PDF and download'
type: 'feature'
ticket: '10'
created: '2026-11-21'
status: 'done'
baseline_revision: 'e03ed84c47a4bd612c40c4c3a4db4702b836ebb7'
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

**Problem:** Invoices and credit notes are issued (7.6, 7.7), but a doctor cannot see or download them. Lines hold only patient codes, and nothing yet adds the patient's name at render time (Y7, AD-7).

**Approach:**
- Render an invoice or credit note as a PDF in the doctor's language, from the invoice's own snapshot.
- Add each patient's current name next to the code through a new lookup hook. Epic-ordering registers it later; this story tests it with a fixture.
- The doctor downloads the PDF from a new invoices page, under their own ownership check.

## Boundaries & Constraints

**Always:**
- The PDF is built only from the `Invoice`/`InvoiceLine` snapshot: the `seller_*`, `bill_*`, totals, `vat_groups` and the per-line `vat_text`. Never from the live `BillingCompany` or fiscal entity.
- The language is the doctor's profile language at download time, through the `payments/transfers.py::build_message` fallback. That fallback already forces English on .ae.
- Ownership: an order invoice through `invoice.order.user`; a manual invoice when `bill_entity_id` is a `FiscalEntity` of the doctor; a credit note through its original's owner. Anything else (no owner, an orphan, a deleted entity) returns 404, the same as a missing invoice.
- Rendering is synchronous, inside the view. The response is no-store and is sent as an attachment named `<number>.pdf`.
- A code with no name from the hook shows only the code. That covers an erased patient, no hook registered, and a hook error.

**Never:**
- Store, log, cache or put in a URL a patient name (AD-23). Never pass one to a task or to Sentry.
- Change the invoice models or their append-only guards. No migration.
- Show patient names on the invoices list page.
- Give staff access through this page.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| ES invoice | EU doctor, language es, a line with a patient code that the hook knows | PDF in Spanish: seller legal details, buyer, lines with "name (code)", VAT groups with exemption text, totals | — |
| Erased patient | The hook returns no entry for the code | The line shows only the code | — |
| No hook / hook raises | No hook registered, or the hook raises | Every line shows only the code; the error is logged without names | Logged, not raised |
| Credit note | A doctor's credit note | A PDF titled as a credit note, with negative amounts and the original invoice number | — |
| .ae | AE portal, doctor's language es | English PDF | — |
| Other doctor | The invoice belongs to another doctor | 404 | — |
| Manual, linked | A manual invoice issued with the doctor's fiscal entity | Listed and downloadable for that doctor | — |
| Manual unlinked / orphan | A manual invoice with no entity, or whose order, user or entity is gone | 404 for every doctor | — |
| Staff / anonymous | A staff user, or a user who is not logged in | 404 for staff (`is_client`); anonymous goes to login | — |

## Decisions (user, 2026-11-21)

- PDF library: WeasyPrint. Pango goes into `deploy/provision.sh` and the CI apt step; dev is re-provisioned once.
- The doctor finds PDFs on a new "Invoices" page with a home tile.
- Manual invoices are matched to the doctor through the buyer snapshot's `bill_entity_id`. `issue_manual` takes an optional fiscal entity and snapshots its id and version (the fields exist; no migration).
- No bank details on the PDF: legal details (`seller_*`) only.

</frozen-after-approval>

## Code Map

**Invoices:**
- `invoices/models.py`:
  - `Invoice`: UUID `id`; `kind` is order, manual or credit_note.
  - `original` and `reason`; for credit notes, `original` is the invoice being corrected.
  - Seller and buyer snapshots: `seller_*` (l.99-104) and `bill_*`, with the `bill_display_name` property.
  - Totals, `vat_groups` (a list of {percent, text, base, vat}), `issue_date` and `time_zone`.
  - `InvoiceLine`: `patient_code` (l.205) and `vat_text`.
  - Read only: no new fields, no migration.
- `invoices/service.py`: `issue_manual` (l.334) builds lines with `patient_code` and gets the optional entity; `issue_credit_note` copies `bill_entity_id` from the original (l.230).
- `accounts/models.py` l.322 `FiscalEntity`: UUID `id`, `user` FK, `version`. `payments/service.py` l.157 shows the id and version snapshot.
- `invoices/apps.py::ready`: the pattern for hook registration.
- `payments/hooks.py`: the registry pattern to copy (`register`/`unregister`, NAME_RE). Do not change its EVENTS.

**Doctor pages:**
- `payments/views.py`:
  - `_doctor_order` (l.88-96): the doctor guard pattern, `is_client` then 404.
  - Decorators: `login_required`, `require_http_methods`, `never_cache`.
- `payments/models.py` l.59: `Order.user` is a nullable FK, so the invoice owner can be missing.
- `accounts/views.py::home` (l.406) and `accounts/templates/accounts/home.html`: home tiles.
- `accounts/page_records.py` `PORTAL_PREFIXES` (l.40): add the new `invoices/` prefix, and check the tests that list prefixes.
- `mytreatplan/urls.py` l.60: `portal_patterns` inside `i18n_patterns`.

**Language and caching:**
- `payments/transfers.py::build_message` (l.30-43): profile language, fallback, `translation.override`.
- `accounts/profile.py::profile_of`.
- `integration/remote.py::no_store`: reference only. Use `add_never_cache_headers` plus `Cache-Control: private`.

**Tests and assets:**
- Test setup:
  - `invoices/tests/test_credit_notes.py`: `invoice_for(doctor)` and the `fake_gateway` fixture.
  - `payments/tests/conftest.py`: the `doctor` and `entity` fixtures.
  - Root `conftest.py` l.225: the `ae_portal` fixture.
- `static/fonts/`: Inter in woff2, latin and latin-ext.
- Deploy: `deploy/provision.sh` `PACKAGES` (l.179) and `.github/workflows/ci.yml` (l.62, apt) are only needed if the library needs system packages.

## Tasks & Acceptance

**Execution:**
- [ ] `invoices/patients.py`:
  - A single-slot lookup registry: `register(fn)`, `unregister()`, and `names_for(user, codes) -> dict`.
  - `names_for` never raises. When nothing is registered it returns `{}`. When the hook raises, it logs the error class and returns `{}`.
  - The hook shape is `fn(user, codes) -> {code: name}`.
- [ ] `invoices/pdf.py`: `render(invoice, user) -> bytes`.
  - Resolve the language, then render `invoices/templates/invoices/pdf.html` with the snapshot and the names, then convert it to a PDF.
  - Labels are translatable; use `pgettext` where a label would clash with an existing msgid.
  - Money is formatted to 2 decimals with the currency code.
- [ ] `invoices/views.py`, `invoices/urls.py`, `mytreatplan/urls.py`, `accounts/page_records.py`:
  - `invoices/` lists the doctor's invoices and credit notes, newest first: number, kind, date, total and a download link. No names.
  - `invoices/<uuid:pk>/pdf/` serves the PDF.
  - Both views use the doctor guard and return 404 for anything the doctor does not own.
- [ ] `invoices/service.py::issue_manual` -- optional `entity` keyword (a `FiscalEntity`); when given, snapshot `bill_entity_id` and `bill_entity_version` from it; existing callers unchanged -- lets a manual invoice reach its doctor.
- [ ] `accounts/views.py` and `home.html`: an "Invoices" tile.
- [ ] `requirements.txt`, `deploy/provision.sh` (`PACKAGES`: `libpango-1.0-0 libpangoft2-1.0-0`), `.github/workflows/ci.yml` (apt step), `deploy/README.md`: pin WeasyPrint and its system libraries.
- [ ] `locale/es/LC_MESSAGES/django.po`: the ES strings; compile them.
- [ ] `invoices/tests/test_pdf.py`:
  - One test per matrix row.
  - Read the PDF text with the library's own text extraction, or assert on the rendered HTML before conversion where that is more reliable.

**Acceptance Criteria:**
- Given a doctor with a paid ES order and a fixture hook that knows the line's patient code, when they open Invoices and download, then they get a Spanish PDF named `<number>.pdf` that shows the seller's legal details, the patient's name next to the code, and totals that match the invoice.
- Given the same invoice and the fixture hook with that patient removed, when the doctor downloads again, then only the code shows.
- Given any download, then the response has `Cache-Control` no-store and private, and no patient name is in the URL or in a log record.
- Given the full suite, then it has 0 failures.

## Implementation Notes

## Plan Change Log

## Review Triage Log

**Pass 1 (quick, Opus), 2026-11-21:** 0 high, 1 medium, 4 low, 0 false, 0 maybe-false. All five were patched.

| # | Finding | Verdict | Route | Evidence / action |
|---|---------|---------|-------|-------------------|
| 1 | `issue_manual(entity=…)` never marks the entity used, so `fiscal.remove` hard-deletes it and the doctor loses the invoice | medium | patch | `fiscal.remove` deletes an entity whose `used_at` is None, and `invoices_of` matches manual invoices through the doctor's entities. `mark_used` is now called in the issue transaction, and a test removes the entity and still downloads. |
| 2 | The VAT/exemption test passes vacuously (empty `vat_text`) | low | patch | The default ES order has no exemption text. The test now uses an exempt 0% line plus a 21% line and asserts the texts unconditionally. |
| 3 | The download test never shows that the view renders in the profile language with the name | low | patch | The view's HTML is captured through a patched `render_html`. The test asserts `lang="es"` and `NAME (CODE)` under an `/en/` URL. |
| 4 | The URL fetcher allows any `file:`/`data:` URL, wider than its docstring | low | patch | There was no injection path (autoescape), but the defence was implicit. Only the font URLs are allowed now, and a test refuses `file:///etc/hosts`, `data:` and `http:`. |
| 5 | Staff see an Invoices tile that always returns 404 | low | patch | The tile is shown only when `is_client(user)`, and a test covers doctor and staff. |

Full suite after the fixes, merged with master (7.8): 3981 passed, 17 skipped, 1 failed. The failure is `test_remembered_device.py::test_ticked_right_code_logs_in_and_sets_the_mark`. It also fails on the 7.8 code with `SECRET_KEY=ci-only-not-a-secret` on SQLite, so it was not introduced here; it is already in deferred-work.

## Verification

Work in the git worktree `../mytreatplan-7.10` (branch `story/7.10-invoice-pdf`, based on the unmerged 7.8 branch). Never touch the main checkout `../mytreatplan.com`. Commit there with new commits; never push. The venv is `../.venv` (shared). Install the pinned WeasyPrint into it. WeasyPrint needs Pango; on this Mac it comes from Homebrew (`brew install pango`). If `import weasyprint` still fails, report it rather than skipping the tests. CI installs Pango through apt. The suite and git need the sandbox off.

Before the first suite run in the fresh worktree: `../.venv/bin/python manage.py compilemessages` and `npm ci && npm run build:css`.

**Commands** (from the worktree root):
- `export SECRET_KEY=ci-only-not-a-secret DEBUG=False SECURE_SSL_REDIRECT=False ALLOWED_HOSTS=localhost,127.0.0.1,testserver FIELD_ENCRYPTION_KEYS="test1:dGVzdC1vbmx5LWZpZWxkLWtleS1ub3Qtc2VjcmV0ISE=" BLIND_INDEX_KEY="dGVzdC1vbmx5LWJsaW5kLWluZGV4LWtleS0zMmJ5dGU=" DATABASE_URL="sqlite:///$TMPDIR/x710.sqlite3"`
- `../.venv/bin/python manage.py makemigrations --check --dry-run`: expected "No changes detected".
- `../.venv/bin/python -m pytest -q -p no:cacheprovider --tb=line`: expected 0 failures.

**Manual checks:**
- On dev, a doctor downloads an ES invoice. The fixture patient's name and code show; with the fixture patient erased only the code shows. Another doctor gets 404.

## Staging

Run on 2026-11-21, by Claude over SSH, with merge and staging delegated by the user. PR #123 merged as b82ff28. Deploy run 44950520851 passed on dev and dev-ae. Pango (`libpango-1.0-0 libpangoft2-1.0-0`) was installed on the staging host with apt before the deploy. On dev, WeasyPrint is 70.0.

All checks ran in one rolled-back transaction, using a made-up fixture patient.

1. **Manual invoice:** a manual invoice was issued with the dev doctor's fiscal entity and a line with `EU-7K3QX9MD`.
2. **Download with the hook registered:**
   - The doctor's language was Spanish, and the download URL was under `/es/`.
   - The download returned 200 `application/pdf`, as an attachment named `ES-EU-2026-000002.pdf`.
   - `Cache-Control` was no-store and private.
   - The file started with `%PDF`, and the name was not in the URL.
   - The PDF showed "Factura", the seller MyTPDSO Spain S.L. and its NIF-IVA, the buyer, "Paciente: <name> (EU-7K3QX9MD)", the VAT breakdown and the totals. Claude checked the rendered file by eye.
3. **Without the hook:** only the code shows.
4. **Other users:** another doctor got 404, a staff user got 404, and an anonymous user was redirected to login (302).
5. **List page:** returned 200 and listed ES-EU-2026-000001, ES-EU-2026-R000001 and the manual invoice. No name appeared.
