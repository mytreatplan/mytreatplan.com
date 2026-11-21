---
title: 'Invoices'
type: 'feature'
ticket: '6'
created: '2026-11-20'
status: 'done'
baseline_revision: 'fecbc12e2c0cb2ae9a4686e2b9135a2750450e37'
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

**Problem:** A paid order produces no invoice. AD-7 requires each billing company to have a gapless, immutable invoice series per instance and year, and later work plugs into invoices: 7.7 credit notes, 7.8 accounting feed, 7.10 PDF, UAE e-invoicing and VeriFactu (Y6, P8).

**Approach:** A new portal app `invoices` holds structured, immutable invoices: seller and buyer snapshots, lines with a tax category, VAT groups, totals, and an issue date and time in the company's legal time zone. A locked per-(company, instance, year) counter assigns `<company>-<instance>-<year>-<6 digits>`. Invoices are issued from the order's `paid` hook, and a separate service invoices a manual sale for a buyer who has no account. Each invoice carries an e-invoice transmission status, which is `none` until a channel exists.

## Boundaries & Constraints

**Always:**
- Copy the invoice from the order: company code and legal name, `bill_*` snapshot, line values, `vat_groups` and totals. Never re-price. Seller identity (address, country, company number, VAT number, tax number) comes from the snapshot `BillingCompany` at issue time.
- The number and the invoice row commit or roll back together, under a `select_for_update` lock on the counter row. Year and issue date use `ZoneInfo(company.time_zone)`.
- Idempotent per order: one invoice per order (a unique constraint), whether the hook fires again or a call is repeated.
- An invoice and its lines are append-only, following the `OrderChange` pattern: `save` on an existing row, `delete`, `QuerySet.update` and `QuerySet.delete` all raise. The e-invoice fields are the only exception (see Design Notes).
- A 0% line carries its exemption or reverse-charge text. An included-case line keeps its list amount and the `included_case` flag. A €0 order still gets a €0 invoice.
- Buyer fields are encrypted like the order's. No foreign key to the fiscal entity or the user may cascade, because erasure must not touch invoices.

**Never:** credit notes (7.7); the accounting copy or feed (7.8); restore-safe renumbering (7.9); the PDF, the doctor's download or patient names (7.10); admin buttons (epic-sales-finance); any transmission to an ASP or VeriFactu; a core release.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| First paid order | ES order paid on the EU instance in 2026 | `ES-EU-2026-000001`, issue date in Europe/Madrid | — |
| Concurrent payments | 3 orders confirmed at once (PostgreSQL) | 000001–000003, no gap, no duplicate | lock serialises |
| Year boundary | paid 2026-12-31 23:30 UTC, Madrid already 2027 | `…-2027-000001` | — |
| Reverse charge | IE company, DE buyer with VIES valid | line 0%, group text = reverse-charge text | — |
| €0 / included case | voucher covers all; included-case line | €0 invoice; line at list amount, flag kept | — |
| Hook repeat | `paid` fires twice for one order | the same single invoice | unique constraint |
| Company missing | `company_code` not in the snapshot | no invoice; the payment rolls back | `InvoiceError` → `HookFailed` |
| Edit attempt | `save()`/`update()` on an issued invoice | refused | `AppendOnly` |

## Decisions (user, 2026-11-20)

- Invoices are issued in the `paid` hook, inside the payment's transaction. If issuing fails, the payment rolls back and the webhook or the staff action retries. No order is ever paid without an invoice.
- The manual-sale service creates an invoice directly from a company code, a buyer snapshot and lines (net, VAT percent, text). It creates no order. epic-sales-finance builds the screen.
- A blank `patient_code` is added to `OrderLine` and to the invoice line now, and copied across. 9.1 fills it on orders.
- No "payment received" email in this entry. 7.10 sends it with the PDF and the download link.
- Merge and staging are delegated. Claude merges after CI is green and runs the staging checks on dev and dev-ae over SSH. The user gave an explicit SSH OK for this story's checks.
- The plan is kept whole although it is over 1600 tokens.
- (Claude, told at the checkpoint) No backfill. Orders paid on staging before the deploy stay without an invoice.

</frozen-after-approval>

## Code Map

- `payments/models.py` -- `Order` :38 (`company_code`, `company_legal_name`, totals :68-73, `vat_groups` :75 `[{percent,text,base,vat}]`, `bill_*` encrypted snapshot :85-97, `paid_at`); `OrderLine` :127 (`list_amount` is the amount for the quantity, `net`, `vat_percent`, `vat_text`, `included_case` :156); `OrderChange` :168-208 is the append-only pattern to copy (`AppendOnly`, guarded QuerySet). Next migration `payments` 0005.
- `payments/hooks.py` -- `register(event, name, receiver)` :22; receivers run inside the transition's transaction and get the locked order; a raise means `HookFailed` and the rollback. Register `paid` in an `AppConfig.ready` (model: `payments/apps.py:16-36`). The three paid paths (`_pay_free` :353, `confirm_payment` :637, `mark_transfer_paid` :679) all go through `service._fire` :267.
- `payments/service.py` -- `_BILL` :134, `_snapshot_entity` :146 (buyer field map); `_lock` :276.
- `accounts/fiscal.py:75` -- `FIELDS`; `accounts.crypto` encrypted fields; `accounts/erasure.py:110` (snapshots are the legal record).
- `catalogue/models.py:390-438` -- snapshot `BillingCompany` (`code` not unique, `time_zone`, `company_number`, `vat_number`, `tax_number`, `address`, `country`). Look it up as in `payments/bank_transfer.py:28,62`.
- `regions/profiles.py:79` -- `settings.REGION_PROFILE.invoice_prefix` (`EU`, `AE`). Never use `BILLING_COMPANY_CODES` (:147): the company comes from the order.
- `mytp_core.money.Money` -- half-up arithmetic for the manual-invoice groups; mirror `catalogue/pricing.py:353-359`.
- `integration/outbox.py:99-116` -- `_next_sequence`: lock, increment, create on missing with an IntegrityError retry.
- Tests: `payments/tests/conftest.py` (`catalogue_loaded` ES/IE/AE companies, `doctor`, `entity`, `fake_on`), `payments/tests/test_service.py` (`line`, `new`, `pay` :26-47; DE reverse charge :159; thread pattern :474-503, PG-only skip). Wiring: `INSTALLED_APPS`, `pytest.ini` testpaths, README :329 ("the invoice (6)").
- **Build setup:** branch `story/7.6-invoices` is checked out (baseline fecbc12, mytp-core 0.29.0 already vendored). Do not commit or push anything. Use the venv `/private/tmp/claude-501/portal-6.9-venv` (it has the 0.29.0 wheel). Run the suite command in Verification with the sandbox off. After adding the app, also run `makemigrations --check`. Untranslated strings are not expected (no UI).
- Planning: the epic Note "billing companies gain a short company code (entry 6)" is stale. `BillingCompany.code` has existed since core 0.23.0.

## Tasks & Acceptance

**Execution:**
- [ ] `payments/models.py`, `payments/migrations/0005_orderline_patient_code.py` -- add `OrderLine.patient_code`, a blank CharField with `default=''` and `db_default=''` -- Decision 3; the previous release can still insert rows.
- [ ] `invoices/` (new app: `__init__.py`, `apps.py`, `models.py`, `migrations/0001_initial.py`) -- `InvoiceSeries`, `Invoice` and `InvoiceLine` as in Design Notes; the app's own `AppendOnly` with guarded QuerySet, save and delete; buyer fields use the `accounts.crypto` encrypted fields; `order` is a OneToOne with SET_NULL; `number` is unique, and `order` is unique when not null -- the immutable, structured record (AD-7).
- [ ] `invoices/service.py` -- `issue_for_order(order)`, idempotent: returns the existing invoice and copies `patient_code` from each line; `issue_manual(company_code, buyer, lines, currency, actor)`: checks buyer keys against `fiscal.FIELDS`, groups VAT per (percent, text) with `Money`, half up; `_next_number(company_code, instance, year)`: locks or creates the series row, retries on `IntegrityError`; `set_einvoice_status(invoice, status, reference='')`. Errors are `InvoiceError` subclasses: company missing, bad buyer, no lines, a 0% line without text -- one place for numbering and snapshots.
- [ ] `invoices/apps.py` -- `ready()` calls `hooks.register('paid', 'invoice_issue', issue_for_order)` -- Decision 1.
- [ ] `mytreatplan/settings.py`, `pytest.ini`, `README.md` (:329) -- wire the app in; the README says what 7.6 delivers and what 7.7–7.10 add.
- [ ] `invoices/tests/` -- cover the I/O matrix: numbering; the year boundary in the company time zone; the ES exemption text; IE→DE reverse charge; €0 and included case; a repeated hook; a missing company rolling the payment back (the order stays unpaid); immutability (save, update, delete and bulk paths); e-invoice status changes allowed only on its three fields; a manual invoice with no account; buyer data kept after the user's erasure; the PG-only concurrency test (3 threads, no gap); an AE invoice on the AE instance -- proves each scenario.

**Acceptance Criteria:**
- Given three ES orders paid on the EU instance in 2026, when their invoices are listed, then they are ES-EU-2026-000001 to 000003, each with the order's totals, VAT groups and buyer snapshot.
- Given a manual sale to a buyer with no account, when `issue_manual` runs, then the invoice takes the next number in the company's series, with `kind=manual` and no order.
- Given an issued invoice, when any field other than the e-invoice fields is saved or updated, then `AppendOnly` is raised and the row is unchanged.
- Given a paid order whose user is then erased, when the erasure finishes, then the invoice and its buyer snapshot remain.

## Implementation Notes

## Plan Change Log

## Review Triage Log

**Pass 1 (quick lens, Opus, 2026-11-20):** 7 findings: 0 high, 3 medium, 2 low, 2 rejected (low, loud failure). 5 patched by the implementer.

| # | Finding | Verdict | Route | Evidence / action |
|---|---------|---------|-------|-------------------|
| 1 | 0% line marked `AE` on valid VIES alone; pricing reverse-charges only when the rule has `reverse_charge_text` (ES exempt + PT valid VIES → `AE` instead of `E`) | medium | patch | Reviewer reproduced it; `pricing.py:340-345`. Fix: `AE` only when the line text is one of the company's rules' reverse-charge texts, plus a test. |
| 2 | `seller_legal_name` comes from the current company row, not `order.company_legal_name` | low | patch | Plan Always says copy the legal name from the order; direct correction. |
| 3 | `vat_percent='NaN'` raises `InvalidOperation`; a non-dict line raises `AttributeError` | low | reject | Fails loudly on input only the future admin screen builds. The fix adds guards for a case nobody meets in everyday use. |
| 4 | `code` and `patient_code` cut silently, and `net` rounded silently, on an immutable record | medium | patch | `_check_line` `[:64]`, `[:32]`, `Money` quantize. Fix: refuse with `BadLine`. |
| 5 | `set_einvoice_status` reference not length-checked (`DataError` on PG) | low | reject | Fails loudly; no caller exists until the e-invoicing entry, which owns that input. |
| 6 | The €0 test bypasses `_pay_free`; `confirm_payment` and `mark_transfer_paid` are not exercised | medium | patch | The test hand-edits the order. Fix: tests through a full-cover voucher, the fake gateway and a bank transfer. |
| 7 | Year assertion `== 2026 or > 2026` is vacuous | low | patch | Direct correction: assert the Madrid year. |

## Design Notes

- Models: `InvoiceSeries(company_code, instance, year, last_number)` unique on the three keys; `Invoice` (number unique, `order` OneToOne SET_NULL null, `kind` order|manual, seller snapshot, `bill_*` encrypted, currency, totals, `vat_groups` JSON, `issued_at` (UTC), `issue_date` (company-local), `time_zone`, `einvoice_status` none|pending|sent|accepted|rejected, `einvoice_reference`, `einvoice_updated_at`, `extensions` JSON `{}` reserved for VeriFactu and UAE fields); `InvoiceLine` (position, code, name, quantity, `list_amount`, discount, ice, voucher share, net, `vat_percent`, `vat_text`, `tax_category` S|Z|E|AE (standard, zero, exempt, reverse charge), `included_case`, `patient_code`).
- The e-invoice fields change after issue only through `invoices.service.set_einvoice_status`, which saves with `update_fields` limited to those three. The guard refuses any other `update_fields` and any full save.

## Verification

**Commands:**
- Suite (repo root, sandbox off): `FIELD_ENCRYPTION_KEYS="test1:dGVzdC1vbmx5LWZpZWxkLWtleS1ub3Qtc2VjcmV0ISE=" BLIND_INDEX_KEY="dGVzdC1vbmx5LWJsaW5kLWluZGV4LWtleS0zMmJ5dGU=" DATABASE_URL="sqlite:///$TMPDIR/p76.sqlite3" <venv>/bin/python -m pytest -q -p no:cacheprovider --tb=line` -- expected: 0 failures (baseline 3840 passed / 13 skipped)
- `manage.py makemigrations --check` -- expected: no changes
- Concurrency test once against PostgreSQL -- expected: passes, no gap

## Staging

- PR #119 merged as 9dbc79e (feature commit 5a59cb1). The PR's pull-request test run failed once on the flaky `accounts/tests/test_makers.py::test_an_other_login_is_saved_listed_replaced_and_deleted` (unrelated, recorded in deferred-work) and passed on re-run. The push run passed: 3883 passed, 1 skipped on PostgreSQL, so the concurrency test ran there. Master run 44891624053: test and both deploys green; /healthz ok on dev and dev-ae.
- My local suite: 3871 passed, 14 skipped, 0 failed (SQLite).
- Checks 1–5 passed 2026-11-21 (Claude, over SSH, script check76.py, all in a rolled-back transaction, so no invoices or series remain):
  1. Migrations `invoices` 0001 and `payments` 0005 applied on dev and dev-ae.
  2. dev: three paid ES orders got ES-EU-2026-000001…000003, dated 2026-11-21 Europe/Madrid. Totals, VAT groups and buyer equal the order. Line 0.00 % tax category E with the exemption text. A repeated hook returns the same invoice.
  3. save, update, delete and line update are refused (`AppendOnly`). `set_einvoice_status` set `pending`, and the totals are unchanged.
  4. A manual sale with no account got ES-EU-2026-000004, kind manual, no order, VAT 21.00, total 121.00.
  5. dev-ae: AE-AE-2026-000001, Asia/Dubai, AED, line 5.00 % category S.
