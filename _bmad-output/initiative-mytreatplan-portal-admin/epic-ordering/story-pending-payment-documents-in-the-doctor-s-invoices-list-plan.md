---
title: 'Pending-payment documents in the doctor''s invoices list'
type: 'feature'
ticket: '19'
created: '2026-11-25'
status: 'ready-for-dev'
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

**Problem:** A doctor who approves a bank-transfer order (9.17) has no document to find again or file: the invoices list (7.10) shows only issued invoices, and there is no orders page (user decision 2026-11-25).

**Approach:** List each of the doctor's orders waiting for a bank transfer in the invoices list as a pending-payment document, marked unpaid and stated not to be an invoice. It downloads as a PDF in the invoice's layout, with the order reference to quote and the bank details. When the order is paid, the real invoice takes its place. A cancelled order's document leaves the list.

**Decisions (user, 2026-11-26):**
- Seller details (address, tax ids) come from the live billing company when the PDF is made. No snapshot on the order and no migration.
- Title: "Pending payment document", with "This is not an invoice" under it.
- Placement: in the same invoices list, by date, with a "Pending payment" badge in place of the invoice number.

## Boundaries & Constraints

**Always:**
- The document is shown only while the order is `pending_payment` with an open `bank_transfer` attempt (`integration.transfers.pending()`), and only to the order's own doctor (404 otherwise, like `invoice_pdf`).
- Every page and PDF says plainly that it is not an invoice. It has no invoice number; its reference is the order reference.
- Same patient-name rules as the invoice PDF (names only on the PDF, through `invoices.patients`; codes only on the list page).
- Rendered in the doctor's language; new strings are in `locale/es/LC_MESSAGES/django.po`.

**Never:**
- No change to the invoice models or numbering, and no migration (`test_the_models_are_untouched` stays green).
- Not visible to staff through this page.
- No emails (the order emails are a separate deferred story).

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| Transfer approved | Doctor approved a transfer order | List shows it marked "Pending payment", with no number; the PDF has the pending title, the reference and the bank details | — |
| Paid | Staff confirm the money | The pending row is gone and the issued invoice is listed | — |
| Cancelled | Doctor or staff cancel | No row; the PDF URL answers 404 | 404 |
| Switched to card | Order still `pending_payment`, transfer attempt closed | No pending row | — |
| Another doctor | URL with someone else's order | 404 | 404 |
| No bank details | Billing company has no bank details | PDF shows the same "not available, contact us" text as the transfer page | — |

</frozen-after-approval>

## Code Map

- `invoices/views.py:24` `invoices_of`, `:46` `invoice_list`, `:54` `invoice_pdf` -- the list and the access pattern. Add `pending_documents_of(user)` (orders from `integration/transfers.py:47` `pending()` filtered by `user_id`), pass them to the list, and add the view `invoice_pending_pdf(request, order_pk)` with a `never_cache` attachment named `<reference>-pending.pdf`.
- `invoices/urls.py` -- new route, name `invoice_pending_pdf`.
- `invoices/templates/invoices/list.html` -- the rows (`data-invoice`, kind label :27, download :30). Add pending rows (`data-pending-document`) marked unpaid, with the reference instead of a number.
- `invoices/pdf.py` -- `context(invoice, user)` :82 reads the instance's attributes directly (bill_*, lines, totals, vat_groups, issue_date, kind, number). Add `pending_context(order, user)`, which reuses the line, total and buyer formatting. Seller from the live `BillingCompany` (as `invoices/service.py:164` `_seller` reads it). `issue_date` comes from `order.pending_at`. Add `bank_transfer.details(order, payment)` (`payments/bank_transfer.py:52`) and render it with the same template plus a `pending` branch (title, "not an invoice" notice, reference and bank details block). `pdf.html` reads `invoice.number` at :9 and :37: give the pending branch the reference instead.
- `payments/models.py:38` `Order` (reference :58, totals :69-74, vat_groups :76, bill_* :83-98, pending_at :105) and `:129` `OrderLine` -- same field names as the invoice for buyer, lines and totals.
- `invoices/service.py:164` `_seller` / `:197` `issue_for_order` -- how the seller is filled from the live `BillingCompany` at payment.
- `payments/templates/payments/transfer.html:14-28` -- the reference, bank details and fallback wording to match.
- Tests: copy `invoices/tests/test_pdf.py` (list :251/:261; helpers `browser`, `invoice_with_patient`, `no_lookup`, `knows`) and `payments/tests/test_bank_transfer.py:34-46` (`bank` fixture, `start_transfer`). Use `integration.transfers.update` to cancel the way staff do, and `mark_transfer_paid` to pay.

## Tasks & Acceptance

**Execution:**
- [ ] `invoices/views.py`, `invoices/urls.py` -- pending documents query, list context, PDF view with owner and state check
- [ ] `invoices/pdf.py`, `invoices/templates/invoices/pdf.html` -- pending context and template branch
- [ ] `invoices/templates/invoices/list.html` -- pending rows by date, with a "Pending payment" badge instead of the number
- [ ] `locale/es/LC_MESSAGES/django.po` -- new strings, then `compilemessages`
- [ ] `invoices/tests/test_pending_documents.py` -- every row of the matrix, the language, the patient-name rule on the list

**Acceptance Criteria:**
- Given an approved transfer order, when the doctor downloads its document, then the PDF text contains the pending title, the "not an invoice" notice, the order reference and the IBAN, and contains no invoice number.
- Given the same order paid by staff, when the doctor opens the list, then it shows the issued invoice and no pending row for that order.

## Implementation Notes

## Plan Change Log

## Review Triage Log

## Design Notes

The PDF uses the invoice template with a branch rather than a copy, so the two layouts can't drift. The adapter is a plain context function, not a fake Invoice, so nothing can mistake an order for an issued invoice.

## Verification

**Commands:**
- portal full suite (HANDOFF command) -- expected: passes, ~17 local skips
