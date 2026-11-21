---
title: 'Accounting feed'
type: 'feature'
ticket: '8'
created: '2026-11-21'
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

**Problem:** The portal issues invoices and credit notes and records payments and refunds, but the admin site gets none of it. Accounting has no central copy, and nothing keeps the issued buyer data once a doctor is erased (Y8, AD-18, AD-23).

**Approach:**
- mytp-core 0.31.0 adds closed contracts for invoices (credit notes included), confirmed payments and completed refunds.
- The portal records them on the outbox in the same transaction that creates them.
- A new admin `accounting` app stores them as the accounting copy:
  - Buyer identity is encrypted with the admin's field keys.
  - Doctors are referenced by `client_uid` only, so `client.erased` never touches the copy.

## Boundaries & Constraints

**Always:**
- **Patient data:**
  - Payloads carry patient codes only.
  - `patient_code` is optional in the contract, and the key is omitted when a line has none.
  - `issue_manual` refuses a malformed patient code (region prefix + 8 Crockford characters) with its existing line error before anything is written.
- **Amounts** use the closed `{amount, currency}` shape. VAT percents are strings. Never floats.
- **One event per record, version 1.** Sent only when the record is first created: the idempotent re-calls of `issue_for_order` and `issue_credit_note` send nothing.
- **Admin copy:**
  - The admin rows are written once and never updated or deleted. A replayed event is deduplicated by the existing envelope handling.
  - `client_uid` is a plain UUID with no FK to `ClientRecord`.
  - Region is an FK with `PROTECT`.

**Never:**
- Send the refund's staff `note`, gateway secrets, or any card data.
- Add admin screens. They belong to epic-sales-finance's accounting area.
- Send a patient name. A staff-typed manual line `name` cannot be checked by contract; that is noted in the README for staff.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| Order invoice | An order is paid (gateway or transfer) | `invoice.issued` with kind `order`, the seller, the buyer as issued, lines with patient codes, VAT groups and totals; `payment.confirmed` for the Payment | — |
| Repeat issue | `issue_for_order` is called again | No new event | — |
| €0 order | `_pay_free` | `invoice.issued` only | — |
| Credit note | A refund settles (gateway or manual) | `invoice.issued` with kind `credit_note`, `original_id`, `reason` and negative amounts; `refund.completed` without the note | — |
| Manual sale | `issue_manual`, a line without a patient code | `invoice.issued` with kind `manual`; that line has no `patient_code` key | — |
| Bad code | `issue_manual` with `patient_code="Ana"` | Refused; nothing written | The existing line ValueError |
| Patient name | A payload with `patient_name` | `build_payload` raises | Contract test |
| Admin apply | A valid event from the region | One row; the buyer fields are stored encrypted | — |
| Replay | The same `event_id` | No second row | The existing dedupe |
| Erasure | `client.erased` for that doctor | The client record goes; the accounting rows stay with buyer data | — |

## Decisions (user, 2026-11-21)

- Event types: three, `invoice.issued` (kinds order, manual, credit_note), `payment.confirmed`, `refund.completed`.
- No backlog: records issued before the deploy are not sent (production has not launched).
- Retention: the accounting copy is never deleted for now; a deferred-work entry asks the tax advisor for the ES and UAE legal periods before any purge job.
- €0 orders send the invoice only, no payment event.

</frozen-after-approval>

## Code Map

**mytp-core** (`../mytp-core`):
- `mytp_core/contracts/voucher_usage.py`: the template. It has closed `WriterModel` payloads, `ContractModel` readers, type constants, `*_READERS`, and the closed `UsageAmount` money model to copy.
- `mytp_core/contracts/payloads.py`: `register_payload` (around l.170). It refuses non-closed models.
- `mytp_core/contracts/__init__.py`: `SCHEMA_MODELS`, the exports and `__all__`.
- `mytp_core/schemas/`: regenerate with `export_contract_schemas`.
- `mytp_core/contracts/checks.py`: the patient and float checks. A `patient_code` must match `^[A-Z]{2}-[0-9A-Z]{8}$`.
- `mytp_core/contracts/clients.py` l.85-105: `_FiscalEntityFields`, the buyer field names to mirror.
- Tests:
  - `tests/test_contracts.py` l.648 hard-codes `registered_types()`; update it.
  - Write the new tests on the model of `tests/test_voucher_usage.py`.
- Release 0.31.0:
  - `pyproject.toml` l.7.
  - `mytp_core/__init__.py` l.55 and its docstring.
  - `tests/test_hub.py` version test.
  - A `README.md` section after l.1255.

**Portal:**
- `integration/outbox.py::record` (l.137): it must run inside an atomic block. A payload error rolls back the caller.
- `invoices/service.py`:
  - `issue_for_order` (l.137), `issue_credit_note` (l.187) and `issue_manual` (l.334). Emit right after each `bulk_create` of the lines, on first creation only.
  - `_check_line`: add the patient-code format check.
- `payments/service.py`: emit after `_set_payment_state(..., CONFIRMED)` in `confirm_payment` (l.692) and `mark_transfer_paid` (l.731). The `paid` hook runs before CONFIRMED, so do not emit from the hook.
- `payments/refunds.py::_settle` (l.118): the one emit point for refunds.
- `client_uid` comes from `order.user.uid`, through `original.order` for a credit note. Omit it when there is no user (manual sales, orphan invoices).
- `subscriptions/vouchers.py::_record` (around l.58): the emission pattern.
- `vendor/` and `scripts/vendor-mytp-core.sh`, and `test_vendored_core.py`: the version.

**Admin** (`../mytp-admin`):
- `vouchers/models.py` l.97 (`VoucherRedemption`) and `vouchers/events.py::_apply`: the model and applier template (`_read`, the id check, `_locked_record`).
- `interface/events.py::appliers()` (around l.177): register the three types lazily.
- `clients/models.py` l.111-160: the encrypted buyer fields to copy (`mytp_core.crypto.EncryptedCharField` / `EncryptedEmailField`).
- `mytp_admin/settings.py` `INSTALLED_APPS` (around l.245), `pytest.ini` `testpaths`, the vendored wheel and `test_vendored_core.py`.
- `conftest.py` (`send`, `sites`) and `vouchers/tests/test_redemptions.py`: the test pattern.
- Leave `permissions/areas.py` `accounting` as it is (sales-finance).

## Tasks & Acceptance

**Execution:**
- [ ] **Core** `contracts/accounting.py`:
  - Three closed payloads: `InvoiceIssued` (with closed nested seller, buyer, line and VAT-group models), `PaymentConfirmed` and `RefundCompleted`.
  - Their readers and registration, the schemas and exports.
  - Tests: closed writer, reader that ignores unknown fields, refusal of `patient_name`/floats/a bad code, and the schema files.
  - Release 0.31.0 with the README; tag it locally.
- [ ] **Portal** `invoices/feed.py`:
  - `send_invoice(invoice)`, `send_payment(payment)` and `send_refund(refund)` build the payloads and call `outbox.record`.
  - Wire them into the three issue paths, the two confirm paths and `_settle`.
  - Patient-code check in `_check_line`.
  - Vendor 0.31.0 and update the README.
- [ ] **Portal** `invoices/tests/test_feed.py`: one test per portal matrix row, asserting on the `OutboxEvent` rows and their payloads.
- [ ] **Admin** new app `accounting`:
  - Models `AccountingInvoice` (lines and VAT groups as JSON), `AccountingPayment` and `AccountingRefund`, with append-only guards and migration 0001.
  - Appliers in `accounting/events.py`, registered in `interface/events.py`, `INSTALLED_APPS` and `pytest.ini`.
  - Vendor 0.31.0.
- [ ] `_bmad-output/initiative-mytreatplan-portal-admin/deferred-work.md` -- one entry: the ES and UAE legal retention periods for the accounting copy, to confirm with the tax advisor before any purge job.
- [ ] **Admin** `accounting/tests/test_feed.py`: the admin matrix rows, including the erasure row and that the buyer columns are stored as ciphertext.

**Acceptance Criteria:**
- Given an ES order paid and then refunded on the portal, when the dispatcher runs, then the admin holds one invoice, one credit note, one payment and one refund. They carry patient codes only, and the buyer data reads back as issued.
- Given the full suites in all three repos, then they have 0 failures, including contract schema freshness.

## Implementation Notes

## Plan Change Log

## Review Triage Log

## Design Notes

A credit note is an `invoice.issued` with kind `credit_note`. Using one type keeps one series of records on the admin and mirrors the portal's single `Invoice` table. The aggregate id is the invoice UUID; the payment UUID and the refund UUID are the aggregate ids for the other two. Each record is sent once, so the version is always 1. A later change would need a new event type, not a new version.

## Verification

**Commands:**
- Core: `pytest`. Expected: green, and the schema files are fresh.
- Portal: `makemigrations --check` shows no changes, and the full suite has 0 failures.
- Admin: `makemigrations --check` and `pytest` (in the admin test venv) are green.

**Manual checks:**
- Staging (dev → admindev): a fake-gateway order is paid and refunded. admindev then holds the four rows with codes only. Erasing a test client on admindev leaves them in place.
